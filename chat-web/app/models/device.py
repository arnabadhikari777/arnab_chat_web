"""Native (Android) device registrations.

Deliberately a SEPARATE table from ``push_subscriptions`` (browser Web Push): a Web Push endpoint is
a URL plus encryption keys, a native device is an install id plus a push token (Firebase Cloud
Messaging in Phase 3). Mixing them would break the SSRF guard and the delivery-receipt logic.

Phase 1 only stores registrations. Nothing is sent to these tokens yet.
"""
from ..extensions import db
from ..utils.time import utcnow


class NativeDevice(db.Model):
    """One installed copy of the Android app, signed in as one user."""

    __tablename__ = "native_devices"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # "android" today. A column (not a constant) so another platform never needs a schema change.
    platform = db.Column(db.String(16), nullable=False, default="android")
    # Random id the app creates once per install (UUID). Stays the same when the push token rotates.
    device_id = db.Column(db.String(64), nullable=False)
    # The push token (FCM registration token). Kept server-side only; never returned by any API.
    push_token = db.Column(db.Text)
    # SHA-256 of the token: lets us enforce "one token = one row" with a short unique index.
    token_hash = db.Column(db.String(64))
    app_version = db.Column(db.String(32))
    os_version = db.Column(db.String(32))
    device_model = db.Column(db.String(80))
    # active | disabled (the user turned it off) | invalid (the push service said the token is dead)
    status = db.Column(db.String(16), nullable=False, default="active")
    failure_count = db.Column(db.Integer, nullable=False, default=0)
    last_seen_at = db.Column(db.DateTime)
    last_success_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        db.UniqueConstraint("platform", "device_id", name="uq_native_device_install"),
        db.UniqueConstraint("token_hash", name="uq_native_device_token"),
        db.Index("ix_native_device_user_status", "user_id", "status"),
        db.CheckConstraint("status in ('active','disabled','invalid')", name="ck_native_device_status"),
        db.CheckConstraint("platform in ('android')", name="ck_native_device_platform"),
    )
