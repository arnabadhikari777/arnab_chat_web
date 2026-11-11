"""Sending email (OTP codes and password resets).

PythonAnywhere Free only lets the app talk to a short list of sites, and only over HTTP/HTTPS. Plain SMTP is
refused for everyone except (officially) Gmail, and even that can fail if Google's addresses change. So two
ways are supported, chosen by MAIL_PROVIDER in .env:

  smtp                                    Gmail SMTP (the default, unchanged)
  sendgrid | mailgun | brevo | resend     an HTTPS email API (free tiers exist; check the provider is on the
                                          allow-list of your PythonAnywhere account: run `flask check-services`)

A failure is never hidden: send_email() raises EmailDeliveryError, so the code is discarded and the person
is told to try again. Only errors that happen BEFORE the message is handed over are retried (once), so a
person can never receive the same code twice because of a retry.
"""
import smtplib
import socket
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr

import requests
from flask import current_app

from ..i18n import t
from .errors import EmailDeliveryError

_HTTP_PROVIDERS = {"sendgrid", "mailgun", "brevo", "resend"}


def _from_address(cfg) -> str:
    return cfg["MAIL_FROM_ADDRESS"] or cfg["MAIL_USERNAME"]


def is_configured() -> bool:
    cfg = current_app.config
    if cfg["MAIL_PROVIDER"] in _HTTP_PROVIDERS:
        return bool(cfg["MAIL_API_KEY"] and _from_address(cfg))
    return bool(cfg["MAIL_USERNAME"] and cfg["MAIL_PASSWORD"])


# ---------------------------------------------------------------- SMTP (Gmail)
def _smtp_connect(cfg):
    """Connect + secure + log in. Safe to retry: nothing has been sent yet."""
    smtp = smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=cfg["MAIL_TIMEOUT_SECONDS"])
    try:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
    except Exception:
        try:
            smtp.close()
        except Exception:  # noqa: BLE001
            pass
        raise
    return smtp


def _send_smtp(cfg, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg["MAIL_FROM_NAME"], _from_address(cfg)))
    msg["To"] = to
    msg.set_content(body)
    smtp = None
    for attempt in (1, 2):
        try:
            smtp = _smtp_connect(cfg)
            break
        except smtplib.SMTPAuthenticationError:
            raise                                        # wrong password: retrying is pointless
        except (smtplib.SMTPException, OSError, socket.timeout):
            if attempt == 2:
                raise
            time.sleep(1)
    try:
        smtp.send_message(msg)                           # never retried: it may have been delivered
    finally:
        try:
            smtp.quit()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- HTTPS APIs
def _post_json(url: str, *, headers: dict, json: dict | None = None, data: dict | None = None,
               auth=None, timeout: int = 15) -> None:
    last = None
    for attempt in (1, 2):
        try:
            resp = requests.post(url, headers=headers, json=json, data=data, auth=auth,
                                 timeout=(min(timeout, 10), timeout))
        except (requests.ConnectionError, requests.ConnectTimeout) as exc:   # nothing was sent: retry once
            last = exc
            if attempt == 1:
                time.sleep(1)
                continue
            raise
        if resp.status_code >= 500 and attempt == 1:     # the provider failed before accepting: retry once
            time.sleep(1)
            continue
        if resp.status_code >= 300:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return
    if last:
        raise last


def _send_http(cfg, to: str, subject: str, body: str) -> None:
    provider, key, sender = cfg["MAIL_PROVIDER"], cfg["MAIL_API_KEY"], _from_address(cfg)
    name, timeout = cfg["MAIL_FROM_NAME"], cfg["MAIL_TIMEOUT_SECONDS"]
    if provider == "sendgrid":
        _post_json("https://api.sendgrid.com/v3/mail/send",
                   headers={"Authorization": f"Bearer {key}"}, timeout=timeout,
                   json={"personalizations": [{"to": [{"email": to}]}],
                         "from": {"email": sender, "name": name}, "subject": subject,
                         "content": [{"type": "text/plain", "value": body}]})
    elif provider == "mailgun":
        domain = cfg["MAIL_API_DOMAIN"]
        if not domain:
            raise RuntimeError("MAIL_API_DOMAIN is missing (needed for Mailgun)")
        _post_json(f"https://api.mailgun.net/v3/{domain}/messages", headers={}, timeout=timeout,
                   auth=("api", key),
                   data={"from": formataddr((name, sender)), "to": to, "subject": subject, "text": body})
    elif provider == "brevo":
        _post_json("https://api.brevo.com/v3/smtp/email",
                   headers={"api-key": key, "accept": "application/json"}, timeout=timeout,
                   json={"sender": {"name": name, "email": sender}, "to": [{"email": to}],
                         "subject": subject, "textContent": body})
    elif provider == "resend":
        _post_json("https://api.resend.com/emails",
                   headers={"Authorization": f"Bearer {key}"}, timeout=timeout,
                   json={"from": formataddr((name, sender)), "to": [to], "subject": subject, "text": body})
    else:
        raise RuntimeError(f"Unknown MAIL_PROVIDER {provider!r}")


# ---------------------------------------------------------------- public API
def send_email(to: str, subject: str, body: str) -> None:
    cfg = current_app.config

    if not is_configured():
        if cfg["APP_ENV"] == "development" and cfg["MAIL_DEV_CONSOLE"]:
            current_app.logger.warning("DEV EMAIL to=%s subject=%s\n%s", to, subject, body)
            return
        current_app.logger.error("Mail is not configured (MAIL_PROVIDER=%s)", cfg["MAIL_PROVIDER"])
        raise EmailDeliveryError()

    try:
        if cfg["MAIL_PROVIDER"] in _HTTP_PROVIDERS:
            _send_http(cfg, to, subject, body)
        else:
            _send_smtp(cfg, to, subject, body)
    except Exception as exc:  # noqa: BLE001 - every failure becomes one generic answer for the person
        # The real reason goes to the server log only (never the address in a way that helps an attacker).
        current_app.logger.error("Could not send email (provider=%s): %r", cfg["MAIL_PROVIDER"], exc)
        raise EmailDeliveryError() from exc


def send_otp_email(to: str, code: str, purpose: str, lang: str) -> None:
    app_name = current_app.config["APP_NAME"]
    minutes = current_app.config["OTP_EXPIRY_MINUTES"]
    key = "email.otp.reset" if purpose == "reset_password" else "email.otp.register"
    subject = t("email.otp.subject", lang=lang, app=app_name)
    body = t(key, lang=lang, app=app_name, code=code, minutes=minutes)
    send_email(to, subject, body)
