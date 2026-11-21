"""Gmail OTP: HTML email, SMTP transport choice, expiry message, atomic single use.

Nothing here talks to a real mail server. SMTP and HTTP calls are replaced by recorders, so these tests prove
the message that WOULD be sent and the protocol steps taken, not real delivery. Real delivery is checked with
`flask check-services` and a real sign-up on the hosting account.
"""
import email
import smtplib
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import OtpVerification
from app.services import email_service, otp_service
from app.services.errors import OtpError
from app.services.email_templates import render_otp_html
from app.utils.time import utcnow

REAL_SEND_EMAIL = email_service.send_email      # captured before the autouse fixture replaces it
REAL_SEND_OTP = email_service.send_otp_email


class FakeSMTP:
    """Records the protocol steps; never opens a socket."""
    instances = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.steps, self.message = [], None
        self.ssl = context is not None
        FakeSMTP.instances.append(self)

    def ehlo(self):
        self.steps.append("ehlo")

    def starttls(self, context=None):
        self.steps.append("starttls")

    def login(self, user, password):
        self.steps.append(("login", user, password))

    def send_message(self, msg):
        self.message = msg

    def quit(self):
        self.steps.append("quit")

    def close(self):
        self.steps.append("close")


@pytest.fixture()
def smtp(app, monkeypatch):
    FakeSMTP.instances = []
    app.config.update(MAIL_PROVIDER="smtp", MAIL_SERVER="smtp.gmail.com", MAIL_PORT=587,
                      MAIL_USERNAME="me@gmail.com", MAIL_PASSWORD="abcd efgh ijkl mnop", MAIL_FROM_ADDRESS="")
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def _parts(msg):
    return {p.get_content_type(): p.get_content() for p in msg.walk() if not p.is_multipart()}


# ---------------------------------------------------------------- transport
def test_port_587_uses_starttls_and_removes_spaces_from_app_password(smtp):
    REAL_SEND_OTP("to@example.com", "123456", "register", "en")
    conn = smtp.instances[0]
    assert conn.port == 587 and conn.ssl is False
    assert "starttls" in conn.steps
    assert ("login", "me@gmail.com", "abcdefghijklmnop") in conn.steps
    assert conn.timeout == 15


def test_port_465_uses_implicit_ssl(app, smtp):
    app.config["MAIL_PORT"] = 465
    REAL_SEND_OTP("to@example.com", "123456", "register", "en")
    conn = smtp.instances[0]
    assert conn.ssl is True and "starttls" not in conn.steps


def test_message_is_multipart_with_plain_text_first_and_html_second(smtp):
    REAL_SEND_OTP("to@example.com", "482913", "register", "en")
    msg = smtp.instances[0].message
    parts = _parts(msg)
    assert list(parts) == ["text/plain", "text/html"]
    assert "482913" in parts["text/plain"] and "482913" in parts["text/html"]
    assert msg["To"] == "to@example.com"


def test_send_failure_log_never_contains_the_password(app, smtp, monkeypatch, caplog):
    def refuse(self, user, password):
        raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    monkeypatch.setattr(FakeSMTP, "login", refuse)
    with pytest.raises(email_service.EmailDeliveryError):
        REAL_SEND_OTP("to@example.com", "123456", "register", "en")
    assert "abcdefghijklmnop" not in caplog.text and "abcd efgh" not in caplog.text
    assert "App Password" in caplog.text                  # the plain-language hint


# ---------------------------------------------------------------- HTML template
def test_html_has_branding_code_expiry_and_security_warning(app):
    html = render_otp_html(app_name="Goppo", code="123456", minutes=10, purpose="register", lang="en")
    assert "Goppo" in html and "123456" in html
    assert "10 minutes" in html
    assert "Never share this code" in html
    assert 'name="viewport"' in html                      # responsive on phones
    assert "<img" not in html and "http" not in html.replace("http-equiv", "")   # nothing remote


def test_html_escapes_the_app_name(app):
    html = render_otp_html(app_name="<script>x</script>", code="123456", minutes=5, purpose="register", lang="en")
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_html_is_translated_for_bengali(app):
    html = render_otp_html(app_name="Goppo", code="123456", minutes=10, purpose="reset_password", lang="bn")
    assert "পাসওয়ার্ড রিসেট" in html


def test_change_password_email_uses_the_reset_wording(smtp):
    REAL_SEND_OTP("to@example.com", "123456", "change_password", "en")
    text = _parts(smtp.instances[0].message)["text/plain"]
    assert "password reset code" in text and "create an account" not in text


@pytest.mark.parametrize("provider,field", [("brevo", "htmlContent"), ("resend", "html"), ("mailgun", "html")])
def test_http_providers_receive_the_html_body(app, monkeypatch, provider, field):
    app.config.update(MAIL_PROVIDER=provider, MAIL_API_KEY="k", MAIL_FROM_ADDRESS="a@b.com", MAIL_API_DOMAIN="mg.b.com")
    seen = {}

    class Ok:
        status_code = 200
        text = ""

    def post(url, **kw):
        seen.update(kw)
        return Ok()

    monkeypatch.setattr(email_service.requests, "post", post)
    REAL_SEND_EMAIL("to@example.com", "Hi", "plain", "<p>rich</p>")
    payload = seen.get("json") or seen.get("data")
    assert payload[field] == "<p>rich</p>"


# ---------------------------------------------------------------- OTP rules
def _issue(email_addr="a@example.com", purpose="register"):
    return otp_service.issue_otp(email_addr, purpose)


def test_expired_code_says_expired_not_wrong(app):
    code, row = _issue()
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    with pytest.raises(OtpError) as err:
        otp_service.verify_otp("a@example.com", "register", code)
    assert err.value.key == "otp.expired"


def test_code_is_numeric_and_the_right_length(app):
    code, _ = _issue()
    assert code.isdigit() and len(code) == app.config["OTP_LENGTH"]


def test_only_the_hash_is_stored(app):
    code, row = _issue()
    assert code not in row.code_hash and len(row.code_hash) == 64


def test_spent_code_cannot_be_used_again(app):
    code, _ = _issue()
    otp_service.verify_otp("a@example.com", "register", code)
    with pytest.raises(OtpError):
        otp_service.verify_otp("a@example.com", "register", code)


def test_wrong_tries_count_down_and_lock(app):
    app.config["OTP_MAX_ATTEMPTS"] = 3
    code, _ = _issue()
    wrong = "000000" if code != "000000" else "111111"
    with pytest.raises(OtpError) as e1:
        otp_service.verify_otp("a@example.com", "register", wrong)
    assert e1.value.key == "otp.wrong" and e1.value.values["left"] == 2
    with pytest.raises(OtpError):
        otp_service.verify_otp("a@example.com", "register", wrong)
    with pytest.raises(OtpError) as e3:
        otp_service.verify_otp("a@example.com", "register", wrong)
    assert e3.value.key == "otp.locked"
    with pytest.raises(OtpError) as e4:                                 # the right code no longer works
        otp_service.verify_otp("a@example.com", "register", code)
    assert e4.value.key == "otp.locked"


def test_new_code_invalidates_the_previous_one(app):
    app.config["OTP_RESEND_COOLDOWN_SECONDS"] = 0
    first, _ = _issue()
    second, _ = _issue()
    if first != second:
        with pytest.raises(OtpError):
            otp_service.verify_otp("a@example.com", "register", first)
    otp_service.verify_otp("a@example.com", "register", second)
