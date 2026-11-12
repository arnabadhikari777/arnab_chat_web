"""Database health, backups and safe clean-up for the Admin Panel (SQLite only).

No arbitrary SQL is ever run and nothing here deletes user accounts or messages. Restoring is deliberately NOT
a web action (replacing the live database from a web request is unsafe): use `flask restore-db`.
"""
import os
import re
import sqlite3
from datetime import timedelta
from pathlib import Path

from flask import current_app
from sqlalchemy import text

from ..extensions import db
from ..models import (Conversation, Message, Notification, PushDelivery, PushSubscription, User)
from ..utils.time import utcnow
from .errors import ServiceError

NAME_RE = re.compile(r"^goppo-\d{8}-\d{6}(-pre-restore)?\.db$")


def _db_file() -> Path | None:
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    if not uri.startswith("sqlite:///"):
        return None
    raw = uri[len("sqlite:///"):].split("?")[0]
    return Path(raw) if raw and raw != ":memory:" else None


def supported() -> bool:
    f = _db_file()
    return f is not None and f.exists()


def backup_dir() -> Path:
    d = _db_file().parent / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def info() -> dict:
    f = _db_file()
    out = {
        "supported": supported(),
        "size_mb": round(f.stat().st_size / 1048576, 2) if f and f.exists() else None,
        "users": User.query.count(), "messages": Message.query.count(),
        "conversations": Conversation.query.count(), "notices": Notification.query.count(),
        "push_devices": PushSubscription.query.count(), "push_deliveries": PushDelivery.query.count(),
        "health": "unknown",
    }
    try:
        out["health"] = str(db.session.execute(text("PRAGMA quick_check")).scalar())
    except Exception:  # noqa: BLE001
        db.session.rollback()
    return out


def list_backups() -> list[dict]:
    if not supported():
        return []
    rows = []
    for p in sorted(backup_dir().iterdir(), reverse=True):
        if NAME_RE.match(p.name):
            rows.append({"name": p.name, "size_mb": round(p.stat().st_size / 1048576, 2)})
    return rows


def backup_path(name: str) -> Path:
    """Only names this system created, only inside the backup folder (no path tricks)."""
    if not supported() or not NAME_RE.match(name or ""):
        raise ServiceError("admin.db.bad_backup")
    p = backup_dir() / name
    if not p.is_file():
        raise ServiceError("admin.db.bad_backup")
    return p


def create_backup(suffix: str = "") -> str:
    """A consistent copy made with SQLite's own backup API, safe while the site is running."""
    if not supported():
        raise ServiceError("admin.db.unsupported")
    name = f"goppo-{utcnow().strftime('%Y%m%d-%H%M%S')}{suffix}.db"
    target = backup_dir() / name
    src = sqlite3.connect(f"file:{_db_file()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.chmod(target, 0o600)
    return name


def verify_file(path: Path) -> dict:
    """Open read-only, run SQLite's integrity check and count users."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        check = con.execute("PRAGMA integrity_check").fetchone()[0]
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return {"ok": check == "ok", "integrity": check, "users": users}
    except sqlite3.DatabaseError as exc:
        return {"ok": False, "integrity": str(exc)[:80], "users": None}
    finally:
        con.close()


def delete_backup(name: str) -> None:
    backup_path(name).unlink()


def cleanup_read_notices(days: int) -> int:
    """Delete only READ notices older than `days` (30 or more). Unread notices and all messages are never touched."""
    if not 30 <= days <= 3650:
        raise ServiceError("admin.db.bad_days")
    n = (Notification.query.filter(Notification.is_read.is_(True),
                                   Notification.created_at < utcnow() - timedelta(days=days))
         .delete(synchronize_session=False))
    db.session.commit()
    return n
