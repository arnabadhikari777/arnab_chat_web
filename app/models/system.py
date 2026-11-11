from ..extensions import db
from ..utils.time import utcnow


class OtpVerification(db.Model):
    """Only a hash of the code is stored, never the code itself."""

    __tablename__ = "otp_verifications"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    purpose = db.Column(db.String(30), nullable=False)  # register|reset_password|change_password
    code_hash = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    used_at = db.Column(db.DateTime)
    last_sent_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        db.CheckConstraint(
            "purpose in ('register','reset_password','change_password')", name="ck_otp_purpose"
        ),
        db.Index("ix_otp_email_purpose_id", "email", "purpose", "id"),
    )


class Announcement(db.Model):
    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    body = db.Column(db.Text, nullable=False)
    title_bn = db.Column(db.String(150))
    body_bn = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Report(db.Model):
    __tablename__ = "reports"

    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reported_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reason = db.Column(db.String(60), nullable=False)
    details = db.Column(db.String(1000), nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="open")
    handled_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    handled_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        db.CheckConstraint(
            "status in ('open','reviewing','resolved','dismissed')", name="ck_report_status"
        ),
    )


class AdminAuditLog(db.Model):
    __tablename__ = "admin_audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    action = db.Column(db.String(60), nullable=False)
    target_type = db.Column(db.String(40))
    target_id = db.Column(db.Integer)
    details = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
