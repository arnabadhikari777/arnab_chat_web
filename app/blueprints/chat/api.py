"""JSON endpoints used by the chat pages. Every one checks the person is in the conversation."""
from datetime import datetime

from flask import abort, g, jsonify, request
from flask_login import current_user

from ...extensions import db, limiter
from ...i18n import t
from ...middleware.guards import auth_required
from ...models import User
from ...realtime import get_backend
from ...services import conversation_service, message_service, notification_service, presence_service
from ...services.errors import ServiceError, UploadError
from ...utils.time import utcnow
from . import bp


def _error(exc: ServiceError, status: int = 400):
    return jsonify({"ok": False, "error": {"code": exc.key, "message": t(exc.key, **exc.values)}}), status


def _conv(conversation_id: int):
    """The conversation, only for its two members, and only while the other person's account is active.
    The other person is loaded once here and reused for the presence info (one query less per poll)."""
    conv = conversation_service.get_for_member(current_user.id, conversation_id)
    if conv is None:
        abort(404)
    other = db.session.get(User, conv.other_user_id(current_user.id))
    if other is None or other.status != "active":
        abort(404)
    g.chat_peer = other
    return conv


def _peer(conv):
    other = getattr(g, "chat_peer", None) or db.session.get(User, conv.other_user_id(current_user.id))
    info = presence_service.peer_info(other)
    info["typing"] = get_backend().is_typing(conv.id, other.id)
    return info


def _server_time() -> str:
    return presence_service.iso(utcnow())


def _int(value, default=0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _since(value):
    try:
        return datetime.fromisoformat((value or "").replace("Z", "")) if value else None
    except ValueError:
        return None


# ------------------------------------------------------------------ lists & counters
@bp.get("/api/summary")
@auth_required
def summary():
    return jsonify({"messages": message_service.total_unread(current_user),
                    "notices": notification_service.unread_count(current_user.id)})


@bp.get("/api/chats")
@auth_required
def chats():
    message_service.mark_all_delivered(current_user)   # the app is open, so they have arrived
    items = message_service.chat_list(current_user)
    return jsonify({"chats": items, "total_unread": sum(c["unread"] for c in items)})


# ------------------------------------------------------------------ one conversation
@bp.get("/api/conversations/<int:conversation_id>/messages")
@auth_required
def messages(conversation_id):
    conv = _conv(conversation_id)
    server_time = _server_time()
    rows, has_more = message_service.history(current_user, conv, _int(request.args.get("before")) or None)
    return jsonify({"messages": [message_service.serialize(m, current_user.id) for m in rows],
                    "has_more": has_more, "server_time": server_time, "peer": _peer(conv)})


@bp.post("/api/conversations/<int:conversation_id>/messages")
@auth_required
@limiter.limit("90 per minute")
@limiter.limit("20 per minute", exempt_when=lambda: request.mimetype != "multipart/form-data")  # file uploads
def send(conversation_id):
    conv = _conv(conversation_id)
    try:
        file = request.files.get("file")
        if file is not None:
            msg, created = message_service.send_media(
                current_user, conv, request.form.get("uuid", ""), file,
                voice=request.form.get("voice") == "1", caption=request.form.get("caption", ""),
                duration=request.form.get("duration"),
            )
        else:
            data = request.get_json(silent=True) or {}
            msg, created = message_service.send_text(
                current_user, conv, str(data.get("uuid", "")), str(data.get("body", "")))
    except UploadError as exc:
        return _error(exc, 400)
    except ServiceError as exc:
        return _error(exc, 400)
    return jsonify({"ok": True, "created": created,
                    "message": message_service.serialize(msg, current_user.id)}), (201 if created else 200)


@bp.get("/api/conversations/<int:conversation_id>/poll")
@auth_required
@limiter.limit("120 per minute")
def poll(conversation_id):
    conv = _conv(conversation_id)
    server_time = _server_time()
    new, updates = message_service.poll(
        current_user, conv, _int(request.args.get("after")), _since(request.args.get("since")))
    return jsonify({"messages": [message_service.serialize(m, current_user.id) for m in new],
                    "updates": updates, "server_time": server_time, "peer": _peer(conv),
                    "more": len(new) >= 100})     # a full page came back: ask again straight away


@bp.post("/api/conversations/<int:conversation_id>/delivered")
@auth_required
@limiter.limit("120 per minute")
def delivered(conversation_id):
    conv = _conv(conversation_id)
    raw = (request.get_json(silent=True) or {}).get("ids")
    ids = [i for i in (raw if isinstance(raw, list) else []) if isinstance(i, int) and not isinstance(i, bool)][:200]
    return jsonify({"ok": True, "updated": message_service.mark_delivered(current_user, conv, ids)})


@bp.post("/api/conversations/<int:conversation_id>/seen")
@auth_required
@limiter.limit("120 per minute")
def seen(conversation_id):
    conv = _conv(conversation_id)
    upto = _int((request.get_json(silent=True) or {}).get("upto")) or None
    return jsonify({"ok": True, "updated": message_service.mark_seen(current_user, conv, upto)})


@bp.post("/api/conversations/<int:conversation_id>/typing")
@auth_required
@limiter.limit("120 per minute")
def typing(conversation_id):
    conv = _conv(conversation_id)
    is_typing = bool((request.get_json(silent=True) or {}).get("typing"))
    get_backend().set_typing(conv.id, current_user.id, is_typing)
    return jsonify({"ok": True})


# ------------------------------------------------------------------ one message
@bp.post("/api/messages/<int:message_id>/delete")
@auth_required
@limiter.limit("60 per minute")
def delete(message_id):
    msg = message_service.get_message_for_member(current_user, message_id)
    if msg is None:
        abort(404)
    scope = (request.get_json(silent=True) or {}).get("scope")
    if scope == "me":
        message_service.delete_for_me(current_user, msg)
    elif scope == "everyone":
        try:
            message_service.delete_for_everyone(current_user, msg)
        except ServiceError as exc:
            return _error(exc, 403)
    else:
        return _error(ServiceError("msg.bad_request"))
    return jsonify({"ok": True, "id": msg.id, "scope": scope})


# ------------------------------------------------------------------ Web Push
@bp.get("/api/push/vapid-public-key")
@auth_required
def push_vapid_key():
    from ...services import push_service
    key = push_service.public_vapid_key()
    return jsonify({"ok": True, "publicKey": key, "configured": bool(key)})


@bp.post("/api/push/subscribe")
@auth_required
@limiter.limit("30 per hour")
def push_subscribe():
    from ...services import push_service
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint") or ""
    keys = data.get("keys") or {}
    p256dh = keys.get("p256dh") or data.get("p256dh") or ""
    auth = keys.get("auth") or data.get("auth") or ""
    ua = (request.headers.get("User-Agent") or "")[:300]
    try:
        sub = push_service.save_subscription(current_user, endpoint, p256dh, auth, ua)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "id": sub.id})


@bp.post("/api/push/unsubscribe")
@auth_required
@limiter.limit("30 per hour")
def push_unsubscribe():
    from ...services import push_service
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    n = push_service.remove_subscription(current_user, endpoint)
    return jsonify({"ok": True, "removed": n})


@bp.post("/api/push/sync")
@auth_required
@limiter.limit("20 per minute")
def push_sync():
    """Retry pending push deliveries after the client comes back online."""
    from ...services import push_service
    result = push_service.sync_pending_for_user(current_user.id)
    return jsonify({"ok": True, **result})


@bp.post("/api/notices/mark-displayed")
@auth_required
@limiter.limit("60 per minute")
def notices_mark_displayed():
    """Mark only the notification ids that were shown in the notification box as read."""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    if not isinstance(ids, list):
        return jsonify({"ok": False, "error": "bad_ids"}), 400
    clean = []
    for x in ids[:100]:
        try:
            clean.append(int(x))
        except (TypeError, ValueError):
            continue
    n = notification_service.mark_displayed(current_user.id, clean)
    return jsonify({"ok": True, "marked": n, "notices": notification_service.unread_count(current_user.id)})
