"""Online / offline / last seen. 'Online' simply means 'seen very recently'.

Every page and poll updates last_seen_at, so a closed tab or a lost connection turns
offline by itself after PRESENCE_TIMEOUT_SECONDS. Nothing can stay online forever.
"""
from flask import current_app

from ..extensions import db
from ..utils.time import utcnow

_WRITE_EVERY_SECONDS = 10  # do not write to the database on every single request


def touch(user) -> None:
    now = utcnow()
    if user.last_seen_at is None or (now - user.last_seen_at).total_seconds() >= _WRITE_EVERY_SECONDS:
        user.last_seen_at = now
        db.session.commit()


def is_online(user) -> bool:
    if user.last_seen_at is None:
        return False
    timeout = current_app.config["PRESENCE_TIMEOUT_SECONDS"]
    return (utcnow() - user.last_seen_at).total_seconds() <= timeout


def iso(dt) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def peer_info(user) -> dict:
    return {"online": is_online(user), "last_seen": iso(user.last_seen_at)}
