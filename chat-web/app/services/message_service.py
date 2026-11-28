"""Everything about messages: send, history, status, delete. The server decides every status."""
import re
from datetime import datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from flask import current_app

from ..extensions import db
from ..models import Conversation, Message, MessageAttachment, MessageDeletion, SyncCounter, User
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


def next_rev() -> int:
    """The next sync revision (see models.SyncCounter). Call it inside the transaction of the change it numbers: the
    UPDATE takes the row lock, which is held until that transaction commits, so revisions commit in order."""
    if db.session.get(SyncCounter, 1) is None:
        top = db.session.query(func.coalesce(func.max(Message.sync_rev), 0)).scalar() or 0
        db.session.add(SyncCounter(id=1, value=int(top)))
        try:
            db.session.flush()
        except IntegrityError:               # another request created it first
            db.session.rollback()
    db.session.execute(update(SyncCounter).where(SyncCounter.id == 1).values(value=SyncCounter.value + 1))
    return int(db.session.execute(select(SyncCounter.value).where(SyncCounter.id == 1)).scalar_one())


def sync_head() -> int:
    """The newest revision handed out so far (0 on a database that has no messages yet)."""
    row = db.session.execute(select(SyncCounter.value).where(SyncCounter.id == 1)).scalar()
    if row is not None:
        return int(row)
    return int(db.session.query(func.coalesce(func.max(Message.sync_rev), 0)).scalar() or 0)


def thumbnail_url(url: str | None, resource_type: str | None) -> str | None:
    """A small preview for a Cloudinary image / video (no extra storage: Cloudinary makes it on request).
    Anything that is not a Cloudinary delivery URL gets no thumbnail (the app then shows a placeholder)."""
    if not url or "/upload/" not in url or resource_type not in ("image", "video"):
        return None
    head, _, tail = url.partition("/upload/")
    if resource_type == "image":
        return f"{head}/upload/c_limit,w_480,q_auto,f_auto/{tail}"
    stem = tail.rsplit(".", 1)[0] if "." in tail.rsplit("/", 1)[-1] else tail
    return f"{head}/upload/so_0,c_limit,w_480,q_auto/{stem}.jpg"


def serialize(msg: Message, viewer_id: int) -> dict:
    att = None
    if msg.attachments and not msg.deleted_for_all:
        a = msg.attachments[0]
        att = {"id": a.id, "url": a.url, "name": a.original_name, "size": a.size_bytes,
               "mime": a.mime_type, "dur": a.duration_seconds, "w": a.width, "h": a.height,
               "thumb": thumbnail_url(a.url, a.resource_type)}
    return {
        "id": msg.id, "uuid": msg.client_uuid, "type": msg.type,
        "body": "" if msg.deleted_for_all else msg.body,
        "mine": msg.sender_id == viewer_id, "status": msg.status,
        "at": iso(msg.created_at), "deleted": msg.deleted_for_all, "att": att,
        # added for the Android local database (the website ignores them)
        "conv": msg.conversation_id, "sender": msg.sender_id, "rev": msg.sync_rev or 0,
        "delivered_at": iso(msg.delivered_at) if msg.delivered_at else None,
        "seen_at": iso(msg.seen_at) if msg.seen_at else None,
        "deleted_at": iso(msg.deleted_at) if msg.deleted_at else None,
    }


def _check_uuid(client_uuid: str) -> str:
    if not client_uuid or not _UUID_RE.match(client_uuid):
        raise ServiceError("msg.bad_request")
    return client_uuid


def _existing(user_id: int, client_uuid: str):
    return Message.query.filter_by(sender_id=user_id, client_uuid=client_uuid).first()


def _commit_message(conv: Conversation, msg: Message) -> None:
    """The ONLY step whose failure means "the message was not saved". Everything after it is best effort."""
    conv.last_message_at = msg.created_at
    db.session.commit()


def _after_commit(user: User, conv: Conversation, msg_id: int) -> None:
    """Side effects of a message that is ALREADY saved: realtime event, notice, Web Push.

    Nothing in here may raise. A failed notice or push is logged and the sender still gets "sent": the message
    is in the database, and its tick stays a single tick until the recipient's phone confirms receipt.
    """
    try:
        get_backend().publish("message", conv.id, {"id": msg_id})
    except Exception:  # noqa: BLE001
        current_app.logger.exception("realtime publish failed for conversation %s", conv.id)
    try:
        other = db.session.get(User, conv.other_user_id(user.id))
        if not (other and other.is_active):
            return
        note = _notify_recipient(other, user, conv)
        if note is not None:
            from . import push_service
            push_service.notify_after_commit(note, force=True)    # new activity: send again with the new count
    except Exception:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception("notification after message failed for conversation %s", conv.id)


def _after_send(user: User, conv: Conversation, msg: Message) -> None:
    """Commit the message first, then run the best-effort side effects (see _after_commit)."""
    _commit_message(conv, msg)
    _after_commit(user, conv, msg.id)


def _existing_after_race(user_id: int, client_uuid: str) -> Message:
    """A double tap: the other request owns this client_uuid, so return its message (never a duplicate)."""
    old = _existing(user_id, client_uuid)
    if old is None:
        raise ServiceError("msg.bad_request")
    return old


def _notify_recipient(other: User, user: User, conv: Conversation):
    """One notice per chat. Two messages at the same moment could both try to create it (unique key):
    the loser rolls back only the notice and retries as an update."""
    for _ in range(2):
        try:
            note = notification_service.upsert_message_notice(other, user, conv)
            db.session.commit()
            return note
        except IntegrityError:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            db.session.rollback()
            current_app.logger.exception("message notice failed for conversation %s", conv.id)
            return None
    return None


# ------------------------------------------------------------------ sending
def _check_site_allows(voice: bool, media: bool) -> None:
    """Admin switches (Admin > Settings). Sending is refused with a clear message; nothing is stored."""
    from . import site_settings

    if not site_settings.enabled("messaging_enabled"):
        raise ServiceError("site.messaging_off")
    if media and voice and not site_settings.enabled("voice_enabled"):
        raise ServiceError("site.voice_off")
    if media and not voice and not site_settings.enabled("uploads_enabled"):
        raise ServiceError("site.uploads_off")


def send_text(user: User, conv: Conversation, client_uuid: str, body: str):
    """Returns (message, created). A repeated client_uuid returns the first message again."""
    _check_site_allows(voice=False, media=False)
    _check_uuid(client_uuid)
    body = (body or "").strip()
    if not body:
        raise ServiceError("msg.empty")
    if len(body) > current_app.config["MESSAGE_MAX_CHARS"]:
        raise ServiceError("msg.too_long", max=current_app.config["MESSAGE_MAX_CHARS"])
    if (old := _existing(user.id, client_uuid)):
        return old, False
    msg = Message(conversation_id=conv.id, sender_id=user.id, type="text", body=body,
                  client_uuid=client_uuid, sync_rev=next_rev())
    db.session.add(msg)
    try:
        db.session.flush()
    except IntegrityError:           # two taps at the same moment
        db.session.rollback()
        return _existing_after_race(user.id, client_uuid), False
    _after_send(user, conv, msg)
    return msg, True


def send_media(user: User, conv: Conversation, client_uuid: str, file, voice: bool = False,
               caption: str = "", duration=None):
    """Validate, upload to Cloudinary, then save. Retrying never uploads twice."""
    _check_site_allows(voice=voice, media=True)
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
                  client_uuid=client_uuid, sync_rev=next_rev())
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
        cloudinary_service.delete_asset(asset["public_id"], asset["resource_type"])   # the other request keeps its own upload
        return _existing_after_race(user.id, client_uuid), False
    try:
        _commit_message(conv, msg)
    except Exception:
        # Only HERE is the message really not saved, so only here is the uploaded file removed.
        db.session.rollback()
        cloudinary_service.delete_asset(asset["public_id"], asset["resource_type"])
        raise
    # From this line on the message AND its Cloudinary asset are permanent: a notice/push problem must never
    # delete the asset or fail the request.
    _after_commit(user, conv, msg.id)
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
def mark_delivered_upto(user: User, conv: Conversation, upto_id: int) -> int:
    """Delivered = the recipient's DEVICE confirmed it received the push (see push_service.acknowledge_receipt).
    Opening a page or asking for the chat list does not count: the browser cannot prove a message arrived.
    Only 'sent' messages from the OTHER person move forward; seen is never moved back."""
    if not upto_id:
        return 0
    q = Message.query.filter(
        Message.conversation_id == conv.id, Message.sender_id != user.id,
        Message.status == "sent", Message.id <= upto_id,
    )
    if q.with_entities(Message.id).first() is None:
        return 0                                   # nothing to change = no new revision
    return q.update({"status": "delivered", "delivered_at": utcnow(), "sync_rev": next_rev()},
                    synchronize_session=False)


def mark_seen(user: User, conv: Conversation, upto_id: int | None = None) -> int:
    now = utcnow()
    q = Message.query.filter(
        Message.conversation_id == conv.id, Message.sender_id != user.id,
        Message.status != "seen", Message.deleted_for_all.is_(False),
    )
    if upto_id:
        q = q.filter(Message.id <= upto_id)
    values = {"status": "seen", "seen_at": now, "delivered_at": func.coalesce(Message.delivered_at, now)}
    if q.with_entities(Message.id).first() is not None:
        values["sync_rev"] = next_rev()            # the sender's app learns about the blue ticks through the change feed
    n = q.update(values, synchronize_session=False)
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
        msg.sync_rev = next_rev()                  # this person's other devices drop it on their next sync
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
    msg.sync_rev = next_rev()                      # the tombstone travels through the change feed, also to offline phones
    for a in list(msg.attachments):
        db.session.delete(a)
    db.session.commit()
    for public_id, resource_type in assets:
        cloudinary_service.delete_asset(public_id, resource_type)
    get_backend().publish("deleted", msg.conversation_id, {"id": msg.id})
    _sync_hint_other_member(user, msg, "deleted")


def _sync_hint_other_member(user: User, msg: Message, reason: str) -> None:
    """Best effort: wake the other person's Android app so it fetches the change now. A phone that is off or has no
    push simply picks the change up on its next sync - the change feed, not this hint, is what guarantees delivery."""
    try:
        conv = db.session.get(Conversation, msg.conversation_id)
        if conv is None:
            return
        from . import fcm_service
        fcm_service.deliver_sync_hint(conv.other_user_id(user.id), reason, conv.id)
    except Exception:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception("sync hint failed for message %s", msg.id)


# ------------------------------------------------------------------ change feed (Android local database)
def sync_changes(user: User, after_rev: int, after_id: int, limit: int = 200):
    """Every message of this person's conversations that is newer than the cursor (rev, id), oldest change first.

    The cursor is (sync_rev, id): one bulk change (e.g. "seen" for 50 messages) shares one revision, so the id
    breaks the tie and a page boundary can never skip a message. Returns (rows, hidden_ids, has_more).
    Messages this person deleted "for me" are returned flagged hidden so a second phone of theirs drops them too.
    """
    limit = max(1, min(int(limit or 200), 500))
    q = Message.query.filter(
        Message.conversation_id.in_(_my_conversation_ids(user.id)),
        or_(Message.sync_rev > after_rev, and_(Message.sync_rev == after_rev, Message.id > after_id)),
    ).order_by(Message.sync_rev.asc(), Message.id.asc()).limit(limit + 1)
    rows = q.all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    hidden = set()
    if rows:
        hidden = {mid for (mid,) in db.session.query(MessageDeletion.message_id).filter(
            MessageDeletion.user_id == user.id, MessageDeletion.message_id.in_([m.id for m in rows])).all()}
    return rows, hidden, has_more


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
            "id": conv.id, "peer_id": other.id, "unread": unread_by_conv.get(conv.id, 0),
            "last": _preview(last_by_conv.get(conv.id), user.id),
            "user": {"name": other.full_name, "username": other.username,
                     "avatar": other.avatar_url, "online": is_online(other), "seed": other.id % 5},
        })
    return items


def total_unread(user: User) -> int:
    return Message.query.filter(
        Message.conversation_id.in_(_my_conversation_ids(user.id)), _unread_filter(user.id)
    ).count()
