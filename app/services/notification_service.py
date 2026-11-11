"""Notice Box. Notifications hold names and ids only, never private message text."""
from flask import current_app, url_for
from sqlalchemy import func

from ..extensions import db
from ..models import Announcement, Notification
from ..i18n import t
from ..utils.time import utcnow

PAGE_SIZE = 30


def add_notification(user_id: int, type_: str, payload: dict, link: str | None, dedupe_key: str) -> Notification | None:
    """Queue a notification (caller commits). The same dedupe_key is never stored twice.

    Returns the new Notification or None if it was a duplicate.
    """
    existing = Notification.query.filter_by(user_id=user_id, dedupe_key=dedupe_key).first()
    if existing:
        return None
    note = Notification(user_id=user_id, type=type_, payload=payload, link=link, dedupe_key=dedupe_key)
    db.session.add(note)
    return note


def upsert_message_notice(recipient, sender, conversation) -> Notification | None:
    """ONE notice per chat: more messages raise its counter instead of adding rows.

    Returns the notification row so the caller can trigger a Web Push after commit.
    """
    if not recipient.notify_messages:
        return None
    key = f"msg:{conversation.id}"
    note = Notification.query.filter_by(user_id=recipient.id, dedupe_key=key).first()
    base = {
        "conversation_id": conversation.id, "user_id": sender.id,
        "username": sender.username, "full_name": sender.full_name,
    }
    if note is None:
        note = Notification(
            user_id=recipient.id, type="new_message", payload={**base, "count": 1},
            link=url_for("chat.room", conversation_id=conversation.id), dedupe_key=key,
        )
        db.session.add(note)
        return note
    count = 1 if note.is_read else int(note.payload.get("count", 1)) + 1
    note.payload = {**base, "count": count}
    note.is_read = False
    note.created_at = utcnow()
    return note


def clear_message_notice(user_id: int, conversation_id: int) -> int:
    """Mark this chat's notice read. Returns how many rows changed (0 = nothing was written)."""
    return Notification.query.filter_by(
        user_id=user_id, dedupe_key=f"msg:{conversation_id}", is_read=False
    ).update({"is_read": True})


def unread_count(user_id: int) -> int:
    return Notification.query.filter_by(user_id=user_id, is_read=False).count()


def list_notices(user_id: int, page: int):
    page = max(1, page)
    rows = (
        Notification.query.filter_by(user_id=user_id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1).all()
    )
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


def mark_all_read(user_id: int) -> int:
    n = Notification.query.filter_by(user_id=user_id, is_read=False).update({"is_read": True})
    db.session.commit()
    return n


def mark_read(user_id: int, notification_id: int):
    """Return the notification if it belongs to this user (and mark it read)."""
    note = Notification.query.filter_by(id=notification_id, user_id=user_id).first()
    if note and not note.is_read:
        note.is_read = True
        db.session.commit()
    return note


def describe(note: Notification) -> dict:
    """Turn a stored notification into text in the reader's language."""
    p = note.payload or {}
    app_name = current_app.config["APP_NAME"]
    name = p.get("full_name", "")
    kind = note.type
    icon = {"message_request_received": "i-user", "message_request_accepted": "i-check2",
            "message_request_rejected": "i-close", "new_message": "i-chat",
            "announcement": "i-bell", "password_changed": "i-lock"}.get(kind, "i-bell")
    if kind == "new_message":
        count = int(p.get("count", 1))
        text = t("notice.new_messages", name=name, count=count) if count > 1 else t("notice.new_message", name=name)
        return {"text": text, "icon": icon}
    if kind == "announcement":
        ann = db.session.get(Announcement, p.get("announcement_id"))
        if ann:
            from ..i18n import get_language
            bn = get_language() == "bn" and ann.title_bn
            return {"text": ann.title_bn if bn else ann.title, "icon": icon,
                    "detail": url_for("notices.announcement", announcement_id=ann.id)}
        return {"text": t("notice.announcement_gone"), "icon": icon}
    return {"text": t(f"notice.{kind}", name=name, app=app_name), "icon": icon}


def mark_displayed(user_id: int, notification_ids: list[int]) -> int:
    """Mark only the given notification ids as read (those actually shown in the box)."""
    if not notification_ids:
        return 0
    n = (
        Notification.query.filter(
            Notification.user_id == user_id,
            Notification.id.in_(notification_ids),
            Notification.is_read.is_(False),
        ).update({"is_read": True}, synchronize_session=False)
    )
    db.session.commit()
    return n
