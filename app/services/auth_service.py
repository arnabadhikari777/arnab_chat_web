"""Account logic: sign up, verify, log in, reset password. Routes only call these."""
from datetime import timedelta

from flask import current_app
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import User
from ..utils.time import utcnow
from . import cloudinary_service, otp_service
from .email_service import send_otp_email
from .notification_service import add_notification
from .errors import (
    EmailDeliveryError,
    FieldErrors,
    NeedsVerification,
    OtpCooldown,
    OtpError,
    ServiceError,
    UploadError,
)
from .upload_validator import validate_upload
from .validation import check_email, check_password, clean_email, validate_registration

# Used so a wrong username takes as long as a wrong password (no timing hints).
_DUMMY_HASH = generate_password_hash("not-a-real-password")


# ---------- admin role ----------
def sync_admin_role(user: User) -> None:
    """Admins are exactly the VERIFIED users whose email is listed in ADMIN_EMAILS."""
    should_be_admin = user.is_email_verified and user.email in current_app.config["ADMIN_EMAILS"]
    wanted = "admin" if should_be_admin else "user"
    if user.role != wanted:
        user.role = wanted
        current_app.logger.info("Role of %s set to %s", user.username, wanted)


# ---------- sending codes ----------
def send_code(email: str, purpose: str, lang: str) -> None:
    """Issue a code and email it. If the email fails, the code is thrown away."""
    code, row = otp_service.issue_otp(email, purpose)
    try:
        send_otp_email(email, code, purpose, lang)
    except EmailDeliveryError:
        otp_service.discard_otp(row)
        raise


# ---------- registration ----------
def purge_stale_unverified() -> None:
    cutoff = utcnow() - timedelta(minutes=current_app.config["UNVERIFIED_ACCOUNT_TTL_MINUTES"])
    stale = User.query.filter(User.is_email_verified.is_(False), User.created_at < cutoff).all()
    for user in stale:
        _remove_unverified(user)
    if stale:
        db.session.commit()


def _remove_unverified(user: User) -> str | None:
    public_id = user.avatar_public_id
    db.session.delete(user)
    return public_id


def register_user(form, photo_file, lang: str) -> User:
    """Create an UNVERIFIED account. Raises FieldErrors / UploadError."""
    data = validate_registration(form, current_app.config["APP_NAME"])
    errors: dict = {}

    purge_stale_unverified()
    same_email = User.query.filter_by(email=data["email"]).first()
    if same_email and same_email.is_email_verified:
        errors["email"] = ("err.email_taken", {})
    same_username = User.query.filter_by(username=data["username"]).first()
    if same_username and (same_username.is_email_verified or same_username.email != data["email"]):
        errors["username"] = ("err.username_taken", {})

    # The photo is OPTIONAL. If one was chosen, check it is a valid image.
    upload = None
    if photo_file is not None and photo_file.filename:
        try:
            upload = validate_upload(photo_file, "image")
        except ServiceError as exc:
            errors["photo"] = (exc.key, exc.values)
    if errors:
        raise FieldErrors(errors)

    asset = None
    if upload is not None:
        try:
            asset = cloudinary_service.upload_image(upload, "avatar")
        except UploadError:
            # Never block a new user because the picture could not be stored.
            # They can add a photo later from Edit profile.
            current_app.logger.warning("Sign-up photo upload failed; continuing without photo")
            asset = None

    old_asset_id = None
    if same_email and not same_email.is_email_verified:
        # Same person signing up again before verifying: replace the old attempt.
        old_asset_id = _remove_unverified(same_email)
        db.session.flush()

    user = User(
        full_name=data["full_name"],
        email=data["email"],
        username=data["username"],
        is_public=data["is_public"],
        avatar_url=asset["url"] if asset else None,
        avatar_public_id=asset["public_id"] if asset else None,
        language=lang,
    )
    user.set_password(data["password"])
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:  # someone else took the name a moment ago
        db.session.rollback()
        if asset:
            cloudinary_service.delete_asset(asset["public_id"])
        raise FieldErrors({"username": ("err.username_taken", {})})

    cloudinary_service.delete_asset(old_asset_id)
    current_app.logger.info("New sign-up: %s", user.username)
    return user


def verify_registration(email: str, code: str) -> User:
    email = clean_email(email)
    otp_service.verify_otp(email, "register", code)
    user = User.query.filter_by(email=email).first()
    if user is None:
        raise OtpError("otp.invalid")
    user.is_email_verified = True
    sync_admin_role(user)
    add_notification(user.id, "welcome", {}, None, "welcome")
    db.session.commit()
    current_app.logger.info("Email verified: %s", user.username)
    return user


# ---------- login ----------
def log_in(identifier: str, password: str) -> User:
    """Return the user if the password is right and the account may sign in.

    Raises ServiceError('auth.invalid_credentials') for any wrong detail and
    NeedsVerification if the password is right but the email is not verified yet.
    """
    identifier = (identifier or "").strip().lower()
    user = User.query.filter(
        (User.email == identifier) | (User.username == identifier)
    ).first()

    if user is None:
        check_password_hash(_DUMMY_HASH, password or "")
        raise ServiceError("auth.invalid_credentials")
    if not user.check_password(password or ""):
        current_app.logger.info("Failed login for %s", user.username)
        raise ServiceError("auth.invalid_credentials")
    if not user.is_email_verified:
        raise NeedsVerification(user.email)
    if user.status != "active" and user.status_until is not None:
        from . import admin_service
        admin_service.release_if_expired(user)
    if user.status == "suspended":
        raise ServiceError("auth.suspended")
    if user.status != "active":
        raise ServiceError("auth.banned")

    sync_admin_role(user)
    user.last_seen_at = utcnow()
    db.session.commit()
    return user


# ---------- password reset ----------
def start_password_reset(email: str, lang: str) -> str:
    """Always behaves the same from the outside, so nobody can test which emails exist."""
    email = clean_email(email)
    if (err := check_email(email)):
        raise FieldErrors({"email": err})
    user = User.query.filter_by(email=email).first()
    if user and user.is_email_verified and user.is_active:
        try:
            send_code(email, "reset_password", lang)
        except (OtpError, EmailDeliveryError):
            current_app.logger.warning("Reset code not sent for %s", user.username)
    return email


def reset_password(email: str, code: str, new_password: str, confirm: str) -> None:
    email = clean_email(email)
    if (err := check_password(new_password)):
        raise FieldErrors({"password": err})
    if new_password != confirm:
        raise FieldErrors({"confirm_password": ("err.password_mismatch", {})})

    otp_service.verify_otp(email, "reset_password", code)
    user = User.query.filter_by(email=email).first()
    if user is None or not user.is_email_verified:
        raise OtpError("otp.invalid")
    user.set_password(new_password)
    db.session.commit()
    current_app.logger.info("Password reset for %s", user.username)


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}{'*' * max(2, len(local) - 1)}@{domain}"


# ---------- change password (while logged in): current password + email code ----------
def start_password_change(user: User, current_password: str, lang: str) -> None:
    if not user.check_password(current_password or ""):
        raise FieldErrors({"current_password": ("auth.wrong_current_password", {})})
    send_code(user.email, "change_password", lang)


def change_password(user: User, code: str, new_password: str, confirm: str) -> None:
    if (err := check_password(new_password)):
        raise FieldErrors({"password": err})
    if new_password != confirm:
        raise FieldErrors({"confirm_password": ("err.password_mismatch", {})})
    if user.check_password(new_password):
        raise FieldErrors({"password": ("err.password_same", {})})
    otp_service.verify_otp(user.email, "change_password", code)
    user.set_password(new_password)
    add_notification(user.id, "password_changed", {}, None, f"pwchange:{utcnow().timestamp()}")
    db.session.commit()
    current_app.logger.info("Password changed for %s", user.username)


# ---------- Math Class SSO: find or create user without password / photo ----------
def _unique_username_from_email(email: str) -> str:
    """Build a valid unique username from the email local part."""
    import re
    local = email.split("@")[0].lower()
    base = re.sub(r"[^a-z0-9_]", "", local)[:16] or "student"
    if len(base) < 3:
        base = (base + "user")[:3]
    candidate = base
    n = 0
    while User.query.filter_by(username=candidate).first() is not None:
        n += 1
        suffix = str(n)
        candidate = (base[: 20 - len(suffix)] + suffix)
    return candidate


def create_sso_user(email: str, full_name: str, language: str = "en") -> User:
    """
    Create a verified user coming from Math Class SSO.
    No profile photo required. Random password (they always enter via SSO).
    """
    import secrets
    email = clean_email(email)
    full_name = (" ".join((full_name or "").split()) or email.split("@")[0])[:80]
    username = _unique_username_from_email(email)

    user = User(
        full_name=full_name,
        email=email,
        username=username,
        bio="",
        is_public=True,            # PUBLIC by default: classmates can message each other straight away
        is_email_verified=True,    # Math Class already verified email via OTP
        status="active",
        role="user",
        language=language if language in ("en", "bn") else "en",
    )
    user.set_password(secrets.token_hex(16))
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # Race: email already exists
        user = User.query.filter_by(email=email).first()
        if user is None:
            raise
        return user
    current_app.logger.info("SSO user created from Math Class: %s", user.username)
    return user


def find_or_create_sso_user(email: str, full_name: str, language: str = "en") -> User:
    email = clean_email(email)
    user = User.query.filter_by(email=email).first()
    if user is None:
        return create_sso_user(email, full_name, language)
    # Keep account usable
    if not user.is_email_verified:
        user.is_email_verified = True
    if user.status != "active":
        # do not auto-unban; just return and login_user will respect is_active
        pass
    if full_name and (not user.full_name or user.full_name == user.email.split("@")[0]):
        user.full_name = full_name[:80]
    db.session.commit()
    sync_admin_role(user)
    return user

