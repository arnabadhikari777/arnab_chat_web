import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app

from ..i18n import t
from .errors import EmailDeliveryError


def send_email(to: str, subject: str, body: str) -> None:
    cfg = current_app.config
    configured = bool(cfg["MAIL_USERNAME"] and cfg["MAIL_PASSWORD"])

    if not configured:
        if cfg["APP_ENV"] == "development" and cfg["MAIL_DEV_CONSOLE"]:
            current_app.logger.warning("DEV EMAIL to=%s subject=%s\n%s", to, subject, body)
            return
        current_app.logger.error("Mail is not configured (MAIL_USERNAME / MAIL_PASSWORD)")
        raise EmailDeliveryError()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg["MAIL_FROM_NAME"], cfg["MAIL_USERNAME"]))
    msg["To"] = to
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=15) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError):
        current_app.logger.exception("Could not send email to %s", to)
        raise EmailDeliveryError()


def send_otp_email(to: str, code: str, purpose: str, lang: str) -> None:
    app_name = current_app.config["APP_NAME"]
    minutes = current_app.config["OTP_EXPIRY_MINUTES"]
    key = "email.otp.reset" if purpose == "reset_password" else "email.otp.register"
    subject = t("email.otp.subject", lang=lang, app=app_name)
    body = t(key, lang=lang, app=app_name, code=code, minutes=minutes)
    send_email(to, subject, body)
