from ..extensions import db
from ..utils.time import utcnow


class Conversation(db.Model):
    """One private conversation per pair of users.

    The two ids are stored in order (low < high) so (A,B) and (B,A) can never
    become two different rows.
    """

    __tablename__ = "conversations"

    id = db.Column(db.Integer, primary_key=True)
    user_low_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_high_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    last_message_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_low_id", "user_high_id", name="uq_conversation_pair"),
        db.CheckConstraint("user_low_id < user_high_id", name="ck_conversation_order"),
    )

    messages = db.relationship("Message", backref="conversation", cascade="all, delete-orphan")

    def has_member(self, user_id: int) -> bool:
        return user_id in (self.user_low_id, self.user_high_id)

    def other_user_id(self, user_id: int) -> int:
        return self.user_high_id if user_id == self.user_low_id else self.user_low_id


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.Integer, db.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    sender_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type = db.Column(db.String(20), nullable=False, default="text")
    body = db.Column(db.Text, nullable=False, default="")
    # Sent by the browser; makes retries and double-taps safe (no duplicate rows).
    client_uuid = db.Column(db.String(36), nullable=False)

    status = db.Column(db.String(20), nullable=False, default="sent")
    delivered_at = db.Column(db.DateTime)
    seen_at = db.Column(db.DateTime)

    deleted_for_all = db.Column(db.Boolean, nullable=False, default=False)
    deleted_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint("sender_id", "client_uuid", name="uq_message_client_uuid"),
        db.CheckConstraint("status in ('sent','delivered','seen')", name="ck_message_status"),
        db.CheckConstraint(
            "type in ('text','image','video','voice','document','file')", name="ck_message_type"
        ),
        db.Index("ix_messages_conversation_id_id", "conversation_id", "id"),
        db.Index("ix_messages_unread", "conversation_id", "sender_id", "status"),
    )

    attachments = db.relationship("MessageAttachment", backref="message", cascade="all, delete-orphan")


class MessageAttachment(db.Model):
    __tablename__ = "message_attachments"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(
        db.Integer, db.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    public_id = db.Column(db.String(255), nullable=False)  # Cloudinary id
    url = db.Column(db.String(500), nullable=False)
    resource_type = db.Column(db.String(20), nullable=False)  # image|video|raw
    mime_type = db.Column(db.String(100))
    original_name = db.Column(db.String(255))
    size_bytes = db.Column(db.Integer)
    duration_seconds = db.Column(db.Float)
    width = db.Column(db.Integer)
    height = db.Column(db.Integer)


class MessageDeletion(db.Model):
    """'Delete for me': hides one message from one user only."""

    __tablename__ = "message_deletions"

    message_id = db.Column(
        db.Integer, db.ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    deleted_at = db.Column(db.DateTime, nullable=False, default=utcnow)
