"""Call history model for voice and video calls."""

from ..extensions import db
from ..utils.time import utcnow


class CallHistory(db.Model):
    """Records every voice/video call attempt between two users."""

    __tablename__ = "call_history"

    id = db.Column(db.Integer, primary_key=True)
    call_id = db.Column(db.String(36), nullable=False, unique=True, index=True)  # UUID
    conversation_id = db.Column(
        db.Integer, db.ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    caller_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    receiver_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_type = db.Column(db.String(10), nullable=False)  # voice | video
    # initiated | ringing | connected | completed | missed | rejected | busy | failed | cancelled
    status = db.Column(db.String(20), nullable=False, default="initiated")
    started_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    answered_at = db.Column(db.DateTime)
    ended_at = db.Column(db.DateTime)
    duration_seconds = db.Column(db.Integer, default=0)
    # Added by migration c3d5e7f9a1b2 (all nullable, so old rows stay valid)
    ringing_at = db.Column(db.DateTime)        # the receiver's device confirmed the call is ringing
    accepted_at = db.Column(db.DateTime)       # the receiver pressed Accept
    ended_by_id = db.Column(db.Integer)  # user id; plain column (no FK) so old rows and the migration stay trivially safe
    end_reason = db.Column(db.String(30))

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    caller = db.relationship("User", foreign_keys=[caller_id])
    receiver = db.relationship("User", foreign_keys=[receiver_id])

    __table_args__ = (
        db.CheckConstraint("call_type in ('voice','video')", name="ck_call_type"),
        db.CheckConstraint(
            "status in ('initiated','ringing','connected','completed','missed','rejected','busy','failed','cancelled')",
            name="ck_call_status",
        ),
        db.Index("ix_call_history_pair", "caller_id", "receiver_id"),
        db.Index("ix_call_history_started", "started_at"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "call_id": self.call_id,
            "conversation_id": self.conversation_id,
            "caller_id": self.caller_id,
            "receiver_id": self.receiver_id,
            "call_type": self.call_type,
            "status": self.status,
            "started_at": self.started_at.isoformat() + "Z" if self.started_at else None,
            "answered_at": self.answered_at.isoformat() + "Z" if self.answered_at else None,
            "ended_at": self.ended_at.isoformat() + "Z" if self.ended_at else None,
            "duration_seconds": self.duration_seconds or 0,
            "end_reason": self.end_reason,
        }
