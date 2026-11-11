"""Online / offline / last seen. 'Online' simply means 'seen recently'.

Pages and polls refresh last_seen_at, but the database is written at most once per
PRESENCE_WRITE_INTERVAL_SECONDS. A hidden tab does not poll, so that person turns offline by
themselves after PRESENCE_TIMEOUT_SECONDS; when they come back the first request marks them online.
The timeout can never be shorter than the write interval + 15 s, so an active person is never
shown offline just because writes are throttled.
"""
from flask import current_app

from ..extensions import db
from ..utils.time import utcnow


def _write_interval() -> int:
    return max(1, int(current_app.config.get("PRESENCE_WRITE_INTERVAL_SECONDS", 30)))


def _timeout() -> int:
    return max(int(current_app.config["PRESENCE_TIMEOUT_SECONDS"]), _write_interval() + 15)


def touch(user) -> None:
    now = utcnow()
    if user.last_seen_at is None or (now - user.last_seen_at).total_seconds() >= _write_interval():
        from ..models import User

        # A targeted UPDATE: it does not touch updated_at (that is for profile changes only).
        db.session.query(User).filter(User.id == user.id).update(
            {"last_seen_at": now, "updated_at": User.updated_at}, synchronize_session=False)
        db.session.commit()


def is_online(user) -> bool:
    if user.last_seen_at is None:
        return False
    return (utcnow() - user.last_seen_at).total_seconds() <= _timeout()


def iso(dt) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def peer_info(user) -> dict:
    return {"online": is_online(user), "last_seen": iso(user.last_seen_at)}
