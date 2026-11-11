"""Everything about messages: send, history, status, delete. The server decides every status."""
import re
from datetime import datetime

from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError

from flask import current_app

from ..extensions import db
from ..models import Conversation, Message, MessageAttachment, MessageDeletion, User
from ..realtime import get_backend
from ..utils.time import utcnow
from . import cloudinary_service, notification_service
from .errors import ServiceError, UploadError
from .presence_service import iso
from .upload_validator import kind_for_extension, split_filename, validate_upload

_UUID_RE = re.compile(r"^[A-Za-z0-9\-]{8,36}$")
_ORDER = {"sent": 0, "delivered": 1, "seen": 2}   # a status only ever moves forward


# ------------------------------------------------------------------ helpers
def _my_conversation_ids(user_id: int):
    return db.session.query(Conversation.id).filter(
        or_(Conversation.user_low_id == user_id, Conversation.user_high_id == user_id)
    )


def _hidden_for(user_id: int):
    return db.session.query(MessageDeletion.message_id).filter(MessageDeletion.user_id == user_id)


def _visible(user_id: int, conversation_id: int):
    """Messages this user may see (not 'deleted for me')."""
    return Message.query.filter(
        Message.conversation_id == conversation_id, Message.id.not_in(_hidden_for(user_id))
    )


def _unread_filter(user_id: int):
    return and_(
        Message.sender_id != user_id, Message.status != "seen",
        Message.deleted_for_all.is_(False), Message.id.not_in(_hidden_for(user_id)),
    )


def serialize(msg: Message, viewer_id: int) -> dict:
    att = None
    if msg.attachments and not msg.deleted_for_all:
        a = msg.attachments[0]
        att = {"url": a.url, "name": a.original_name, "size": a.size_bytes,
               "mime": a.mime_type, "dur": a.duration_seconds}
    return {
        "id": msg.id, "uuid": msg.client_uuid, "type": msg.type,
        "body": "" if msg.deleted_for_all else msg.body,
        "mine": msg.sender_id == viewer_id, "status": msg.status,
        "at": iso(msg.created_at), "deleted": msg.deleted_for_all, "att": att,
    }


def _check_uuid(client_uuid: str) -> str:
    if not client_uuid or not _UUID_RE.match(client_uuid):
        raise ServiceError("msg.bad_request")
    return client_uuid


def _existing(user_id: int, client_uuid: str):
    return Message.query.filter_by(sender_id=user_id, client_uuid=client_uuid).first()


def _after_send(user: User, conv: Conversation, msg: Message) -> None:
    conv.last_message_at = msg.created_at
    other = db.session.get(User, conv.other_user_id(user.id))
    if other and other.is_active:
        notification_service.upsert_message_notice(other, user, conv)
    db.session.commit()
    get_backend().publish("message", conv.id, {"id": msg.id})


# ------------------------------------------------------------------ sending
def send_text(user: User, conv: Conversation, client_uuid: str, body: str):
    """Returns (message, created). A repeated client_uuid returns the first message again."""
    _check_uuid(client_uuid)
    body = (body or "").strip()
    if not body:
        raise ServiceError("msg.empty")
    if len(body) > current_app.config["MESSAGE_MAX_CHARS"]:
        raise ServiceError("msg.too_long", max=current_app.config["MESSAGE_MAX_CHARS"])
    if (old := _existing(user.id, client_uuid)):
        return old, False
    msg = Message(conversation_id=conv.id, sender_id=user.id, type="text", body=body,
                  client_uuid=client_uuid)
    db.session.add(msg)
    try:
        db.session.flush()
    except IntegrityError:           # two taps at the same moment
        db.session.rollback()
        return _existing(user.id, client_uuid), False
    _after_send(user, conv, msg)
    return msg, True


def send_media(user: User, conv: Conversation, client_uuid: str, file, voice: bool = False,
               caption: str = "", duration=None):
    """Validate, upload to Cloudinary, then save. Retrying never uploads twice."""
    _check_uuid(client_uuid)
    if (old := _existing(user.id, client_uuid)):
        return old, False

    _, ext = split_filename(getattr(file, "filename", "") or "")
    kind = kind_for_extension(ext, "voice" if voice else None)
    if kind is None:
        raise UploadError("upload.bad_type")
    upload = validate_upload(file, kind)
    caption = (caption or "").strip()[:1000] if kind != "voice" else ""
    try:
        dur = float(duration) if duration not in (None, "") else None
    except (TypeError, ValueError):
        dur = None
    if dur is not None:
        dur = max(0.0, min(dur, float(current_app.config["MAX_VOICE_SECONDS"] if kind == "voice" else 36000)))

    asset = cloudinary_service.upload_media(upload)
    msg = Message(conversation_id=conv.id, sender_id=user.id, type=kind, body=caption,
                  client_uuid=client_uuid)
    msg.attachments.append(MessageAttachment(
        public_id=asset["public_id"], url=asset["url"], resource_type=asset["resource_type"],
        mime_type=upload.mime, original_name=upload.filename,
        size_bytes=upload.size, duration_seconds=dur,
    ))
    db.session.add(msg)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        cloudinary_service.delete_asset(asset["public_id"], asset["resource_type"])
        return _existing(user.id, client_uuid), False
    try:
        _after_send(user, conv, msg)
    except Exception:
        db.session.rollback()
        cloudinary_service.delete_asset(asset["public_id"], asset["resource_type"])
        raise
    return msg, True


# ------------------------------------------------------------------ reading
def history(user: User, conv: Conversation, before: int | None = None, limit: int | None = None):
    limit = limit or current_app.config["CHAT_PAGE_SIZE"]
    q = _visible(user.id, conv.id)
    if before:
        q = q.filter(Message.id < before)
    rows = q.order_by(Message.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    return list(reversed(rows[:limit])), has_more


def poll(user: User, conv: Conversation, after_id: int, since: datetime | None):
    """New messages, plus status/deletion changes since `since` (a server timestamp)."""
    new = (_visible(user.id, conv.id).filter(Message.id > after_id)
           .order_by(Message.id.asc()).limit(100).all())
    updates = []
    if since:
        changed = Message.query.filter(
            Message.conversation_id == conv.id, Message.id <= after_id,
            or_(
                and_(Message.sender_id == user.id,
                     or_(Message.delivered_at > since, Message.seen_at > since)),
                Message.deleted_at > since,
            ),
        ).limit(200).all()
        updates = [{"id": m.id, "status": m.status, "deleted": m.deleted_for_all} for m in changed]
    return new, updates


# ------------------------------------------------------------------ status
def mark_delivered(user: User, conv: Conversation, ids=None) -> int:
    if ids is not None and not ids:
        return 0
    q = Message.query.filter(
        Message.conversation_id == conv.id, Message.sender_id != user.id, Message.status == "sent"
    )
    if ids is not None:
        q = q.filter(Message.id.in_(ids))
    n = q.update({"status": "delivered", "delivered_at": utcnow()}, synchronize_session=False)
    if n:
        db.session.commit()
    return n


def mark_all_delivered(user: User) -> int:
    """The recipient's app is open and asked for its chats, so waiting messages arrived."""
    waiting = Message.query.filter(
        Message.conversation_id.in_(_my_conversation_ids(user.id)),
        Message.sender_id != user.id, Message.status == "sent",
    )
    if waiting.first() is None:        # the common case: a cheap read, no write, no commit
        return 0
    n = waiting.update({"status": "delivered", "delivered_at": utcnow()}, synchronize_session=False)
    db.session.commit()
    return n


def mark_seen(user: User, conv: Conversation, upto_id: int | None = None) -> int:
    now = utcnow()
    q = Message.query.filter(
        Message.conversation_id == conv.id, Message.sender_id != user.id,
        Message.status != "seen", Message.deleted_for_all.is_(False),
    )
    if upto_id:
        q = q.filter(Message.id <= upto_id)
    n = q.update({"status": "seen", "seen_at": now,
                  "delivered_at": func.coalesce(Message.delivered_at, now)},
                 synchronize_session=False)
    cleared = notification_service.clear_message_notice(user.id, conv.id)
    if n or cleared:                        # nothing new to acknowledge = nothing to write
        db.session.commit()
    return n


# ------------------------------------------------------------------ deleting
def get_message_for_member(user: User, message_id: int):
    msg = db.session.get(Message, message_id)
    if msg is None:
        return None
    conv = db.session.get(Conversation, msg.conversation_id)
    return msg if conv and conv.has_member(user.id) else None


def delete_for_me(user: User, msg: Message) -> None:
    if not db.session.get(MessageDeletion, (msg.id, user.id)):
        db.session.add(MessageDeletion(message_id=msg.id, user_id=user.id))
        db.session.commit()


def delete_for_everyone(user: User, msg: Message) -> None:
    """Only the sender may do this. Checked here, not just in the page."""
    if msg.sender_id != user.id:
        raise ServiceError("msg.not_yours")
    if msg.deleted_for_all:
        return
    assets = [(a.public_id, a.resource_type) for a in msg.attachments]
    msg.deleted_for_all = True
    msg.deleted_at = utcnow()
    msg.body = ""
    for a in list(msg.attachments):
        db.session.delete(a)
    db.session.commit()
    for public_id, resource_type in assets:
        cloudinary_service.delete_asset(public_id, resource_type)
    get_backend().publish("deleted", msg.conversation_id, {"id": msg.id})


# ------------------------------------------------------------------ chat list
def _preview(msg: Message | None, viewer_id: int):
    if msg is None:
        return None
    return {"type": msg.type, "body": "" if msg.deleted_for_all else msg.body[:100],
            "deleted": msg.deleted_for_all, "mine": msg.sender_id == viewer_id,
            "status": msg.status, "at": iso(msg.created_at)}


def chat_list(user: User, limit: int = 100) -> list[dict]:
    from .presence_service import is_online

    convs = (Conversation.query.filter(or_(Conversation.user_low_id == user.id,
                                           Conversation.user_high_id == user.id))
             .order_by(Conversation.last_message_at.desc(), Conversation.id.desc()).limit(limit).all())
    if not convs:
        return []
    conv_ids = [c.id for c in convs]
    others = {u.id: u for u in User.query.filter(
        User.id.in_([c.other_user_id(user.id) for c in convs])).all()}
    # Two grouped queries for ALL conversations (instead of two queries for each one).
    newest = (db.session.query(func.max(Message.id))
              .filter(Message.conversation_id.in_(conv_ids), Message.id.not_in(_hidden_for(user.id)))
              .group_by(Message.conversation_id))
    last_by_conv = {m.conversation_id: m for m in Message.query.filter(Message.id.in_(newest)).all()}
    unread_by_conv = dict(
        db.session.query(Message.conversation_id, func.count(Message.id))
        .filter(Message.conversation_id.in_(conv_ids), _unread_filter(user.id))
        .group_by(Message.conversation_id).all())
    items = []
    for conv in convs:
        other = others.get(conv.other_user_id(user.id))
        if other is None or other.status != "active":
            continue
        items.append({
            "id": conv.id, "unread": unread_by_conv.get(conv.id, 0),
            "last": _preview(last_by_conv.get(conv.id), user.id),
            "user": {"name": other.full_name, "username": other.username,
                     "avatar": other.avatar_url, "online": is_online(other), "seed": other.id % 5},
        })
    return items


def total_unread(user: User) -> int:
    return Message.query.filter(
        Message.conversation_id.in_(_my_conversation_ids(user.id)), _unread_filter(user.id)
    ).count()
