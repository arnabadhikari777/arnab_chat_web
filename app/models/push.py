"""Web Push subscription and delivery tracking.

Push delivery is best-effort. The browser push service may queue while a device is offline;
we still keep durable records so the app can recover and avoid duplicate sends.
"""
from ..extensions import db
from ..utils.time import utcnow


class PushSubscription(db.Model):
    """One browser / device push endpoint for a user."""

    __tablename__ = "push_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Unique endpoint URL from the browser Push API.
    endpoint = db.Column(db.Text, nullable=False)
    p256dh = db.Column(db.String(200), nullable=False)
    auth = db.Column(db.String(100), nullable=False)
    user_agent = db.Column(db.String(300))
    # created | active | expired | failed
    status = db.Column(db.String(20), nullable=False, default="active")
    failure_count = db.Column(db.Integer, nullable=False, default=0)
    last_success_at = db.Column(db.DateTime)
    last_attempt_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        db.UniqueConstraint("endpoint", name="uq_push_endpoint"),
        db.Index("ix_push_user_status", "user_id", "status"),
        db.CheckConstraint(
            "status in ('active','expired','failed')", name="ck_push_status"
        ),
    )


class PushDelivery(db.Model):
    """Tracks a single attempt to deliver a notification via Web Push.

    States:
      pending   – created, not yet accepted by the push service
      accepted  – push service accepted the payload (does NOT mean user saw it)
      failed    – temporary or permanent failure
      expired   – subscription no longer valid
    """

    __tablename__ = "push_deliveries"

    id = db.Column(db.Integer, primary_key=True)
    notification_id = db.Column(
        db.Integer, db.ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subscription_id = db.Column(
        db.Integer, db.ForeignKey("push_subscriptions.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Group key used for offline batching, e.g. "msg:42" or "ann:7".
    group_key = db.Column(db.String(120), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="pending")
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.String(300))
    accepted_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            "notification_id", "subscription_id", name="uq_push_delivery_pair"
        ),
        db.Index("ix_push_delivery_pending", "user_id", "status", "created_at"),
        db.CheckConstraint(
            "status in ('pending','accepted','failed','expired')",
            name="ck_push_delivery_status",
        ),
    )
