from ..extensions import db
from ..utils.time import utcnow


class MessageRequest(db.Model):
    __tablename__ = "message_requests"

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    receiver_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    responded_at = db.Column(db.DateTime)

    __table_args__ = (
        db.CheckConstraint(
            "status in ('pending','accepted','rejected')", name="ck_request_status"
        ),
        db.CheckConstraint("sender_id != receiver_id", name="ck_request_not_self"),
        # Only ONE pending request per sender->receiver pair.
        # After a rejection the sender may send a new one.
        db.Index(
            "uq_request_pending",
            "sender_id",
            "receiver_id",
            unique=True,
            sqlite_where=db.text("status = 'pending'"),
            postgresql_where=db.text("status = 'pending'"),
        ),
    )


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type = db.Column(db.String(40), nullable=False)
    # Translatable data (names, ids). Never message text of a private chat.
    payload = db.Column(db.JSON, nullable=False, default=dict)
    link = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    # Stops duplicates, e.g. "request:42:created".
    dedupe_key = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "dedupe_key", name="uq_notification_dedupe"),
        db.Index("ix_notifications_user_read_created", "user_id", "is_read", "created_at"),
    )
