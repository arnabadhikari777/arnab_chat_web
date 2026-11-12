"""Sign-in activity log for admins. Writing an event can never break a sign-in."""
from datetime import timedelta

from flask import request

from ..extensions import db
from ..models import SecurityEvent
from ..utils.time import utcnow


def record(kind: str, user_id: int | None = None, detail: str = "") -> None:
    try:
        ip = (request.remote_addr or "")[:45] if request else ""
        db.session.add(SecurityEvent(kind=kind, user_id=user_id, ip=ip,
                                     detail=(detail or "").replace("\n", " ")[:60]))
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()


def recent(kind: str, limit: int = 40):
    return SecurityEvent.query.filter_by(kind=kind).order_by(SecurityEvent.id.desc()).limit(limit).all()


def prune(days: int = 90) -> int:
    n = SecurityEvent.query.filter(SecurityEvent.created_at < utcnow() - timedelta(days=days)).delete(synchronize_session=False)
    db.session.commit()
    return n
