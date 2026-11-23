import hmac
from hashlib import sha256

from flask import current_app
from flask_login import UserMixin
from sqlalchemy.orm import validates
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..utils.time import utcnow


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    username = db.Column(db.String(32), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    bio = db.Column(db.String(300), nullable=False, default="")

    avatar_url = db.Column(db.String(500))
    avatar_public_id = db.Column(db.String(255))
    cover_url = db.Column(db.String(500))
    cover_public_id = db.Column(db.String(255))

    is_public = db.Column(db.Boolean, nullable=False, default=True)
    is_email_verified = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(20), nullable=False, default="active")  # active|suspended|banned
    role = db.Column(db.String(20), nullable=False, default="user")  # user|admin (admin = any staff member)
    # Staff level, only meaningful when role == "admin": super_admin|admin|moderator (see services/permissions.py)
    admin_role = db.Column(db.String(20))
    language = db.Column(db.String(5), nullable=False, default="en")
    # Notification settings. Important account/security notices are always sent.
    notify_messages = db.Column(db.Boolean, nullable=False, default=True)
    notify_announcements = db.Column(db.Boolean, nullable=False, default=True)
    last_seen_at = db.Column(db.DateTime)
    # A suspension can end by itself. None = until an admin lifts it.
    suspended_until = db.Column(db.DateTime)
    # Admin "sign out everywhere" raises this; it is part of the session id, so older sessions stop working.
    session_epoch = db.Column(db.Integer, nullable=False, default=0, server_default="0")

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        db.CheckConstraint("status in ('active','suspended','banned')", name="ck_users_status"),
        db.CheckConstraint("role in ('user','admin')", name="ck_users_role"),
    )

    # --- rules enforced in the model itself -------------------------------
    @validates("email")
    def _normalize_email(self, _key, value):
        return value.strip().lower()

    @validates("username")
    def _lock_username(self, _key, value):
        """Usernames are lowercase and can never change after registration."""
        value = value.strip().lower()
        if self.username and self.username != value:
            raise ValueError("Username cannot be changed.")
        return value

    # --- helpers -----------------------------------------------------------
    def set_password(self, raw_password: str) -> None:
        method = current_app.config.get("PASSWORD_HASH_METHOD")
        self.password_hash = (
            generate_password_hash(raw_password, method=method)
            if method else generate_password_hash(raw_password)
        )

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    def session_fingerprint(self) -> str:
        """Changes whenever the password changes, which signs out every older session."""
        key = (current_app.config.get("SECRET_KEY") or "").encode()
        data = (self.password_hash or "") + (f":{self.session_epoch}" if self.session_epoch else "")
        return hmac.new(key, data.encode(), sha256).hexdigest()[:16]

    def get_id(self) -> str:  # Flask-Login stores this in the session cookie
        return f"{self.id}.{self.session_fingerprint()}"

    @property
    def is_active(self) -> bool:  # Flask-Login: inactive users cannot log in
        if self.status == "active":
            return True
        # a timed suspension that has run out no longer blocks the account
        return (self.status == "suspended" and self.suspended_until is not None
                and self.suspended_until <= utcnow())

    def lift_expired_suspension(self) -> bool:
        """Write the end of a finished timed suspension. The caller commits. True if something changed."""
        if self.status == "suspended" and self.suspended_until is not None and self.suspended_until <= utcnow():
            self.status, self.suspended_until = "active", None
            return True
        return False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def staff_level(self) -> str | None:
        """None for normal users. An older admin row without a level was a full admin before levels existed."""
        if self.role != "admin":
            return None
        return self.admin_role if self.admin_role in ("moderator", "admin", "super_admin") else "super_admin"

    def __repr__(self) -> str:
        return f"<User {self.username}>"
