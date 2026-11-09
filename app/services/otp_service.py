"""One-time codes: hashed, expiring, single-use, attempt-limited, send-limited."""
import hmac
import secrets
from datetime import timedelta
from hashlib import sha256

from flask import current_app

from ..extensions import db
from ..models import OtpVerification
from ..utils.time import utcnow
from .errors import OtpCooldown, OtpError


def _hash(email: str, purpose: str, code: str) -> str:
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"{email}:{purpose}:{code}".encode(), sha256).hexdigest()


def seconds_until_resend(email: str, purpose: str) -> int:
    latest = (
        OtpVerification.query.filter_by(email=email, purpose=purpose)
        .order_by(OtpVerification.id.desc())
        .first()
    )
    if not latest:
        return 0
    cooldown = current_app.config["OTP_RESEND_COOLDOWN_SECONDS"]
    elapsed = (utcnow() - latest.last_sent_at).total_seconds()
    return max(0, int(cooldown - elapsed + 0.999))


def issue_otp(email: str, purpose: str):
    """Create a fresh code. Returns (plain_code, row). Raises OtpCooldown / OtpError."""
    cfg = current_app.config
    now = utcnow()

    wait = seconds_until_resend(email, purpose)
    if wait > 0:
        raise OtpCooldown(wait)

    sent_last_hour = OtpVerification.query.filter(
        OtpVerification.email == email,
        OtpVerification.purpose == purpose,
        OtpVerification.created_at > now - timedelta(hours=1),
    ).count()
    if sent_last_hour >= cfg["OTP_MAX_SENDS_PER_HOUR"]:
        raise OtpError("otp.too_many")

    # Any older unused code stops working the moment a new one is issued.
    OtpVerification.query.filter_by(email=email, purpose=purpose, used_at=None).update(
        {"used_at": now}
    )
    code = "".join(secrets.choice("0123456789") for _ in range(cfg["OTP_LENGTH"]))
    row = OtpVerification(
        email=email,
        purpose=purpose,
        code_hash=_hash(email, purpose, code),
        expires_at=now + timedelta(minutes=cfg["OTP_EXPIRY_MINUTES"]),
        last_sent_at=now,
    )
    db.session.add(row)
    db.session.commit()
    return code, row


def discard_otp(row: OtpVerification) -> None:
    """Used when the email could not be sent, so the person can retry at once."""
    db.session.delete(row)
    db.session.commit()


def verify_otp(email: str, purpose: str, code: str) -> None:
    """Return None on success, raise OtpError otherwise."""
    cfg = current_app.config
    now = utcnow()
    row = (
        OtpVerification.query.filter_by(email=email, purpose=purpose, used_at=None)
        .order_by(OtpVerification.id.desc())
        .first()
    )
    if row is None or row.expires_at < now:
        raise OtpError("otp.invalid")
    if row.attempts >= cfg["OTP_MAX_ATTEMPTS"]:
        raise OtpError("otp.locked")

    code = (code or "").strip()
    if not hmac.compare_digest(_hash(email, purpose, code), row.code_hash):
        row.attempts += 1
        db.session.commit()
        left = cfg["OTP_MAX_ATTEMPTS"] - row.attempts
        if left <= 0:
            raise OtpError("otp.locked")
        raise OtpError("otp.wrong", left=left)

    row.used_at = now  # single use
    db.session.commit()
