"""JSON endpoints used by the chat pages. Every one checks the person is in the conversation."""
from datetime import datetime

from flask import abort, current_app, g, jsonify, request, session
from flask_login import current_user

from ...extensions import csrf, db, limiter
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
    """Kept only so pages cached before this change do not break. A browser can no longer declare a message
    Delivered: only the signed push receipt (POST /api/push/ack) does that."""
    _conv(conversation_id)
    return jsonify({"ok": True, "updated": 0})


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
def _push_error(code: str, status: int = 400):
    """Same error shape as every other endpoint: {"ok": false, "error": {"code", "message"}}."""
    return jsonify({"ok": False, "error": {"code": code, "message": t(f"push.err.{code}")}}), status


@bp.get("/api/push/config")
@auth_required
def push_config():
    """Is Web Push fully set up on the server? Reports state only; the private key is never included.

    Normal people only learn "not configured". Admins also see which .env variables to fix."""
    from ...services import push_service
    st = push_service.vapid_status()
    from ...services import site_settings
    body = {"ok": True, "configured": st.configured, "publicKey": st.public if st.configured else None,
            "enabled": site_settings.enabled("push_enabled")}      # false = an admin switched push off for the whole site
    if not st.configured and current_user.is_admin:
        body["missing"] = st.missing
        body["problems"] = st.problems
    res = jsonify(body)
    res.headers["Cache-Control"] = "no-store"
    return res


@bp.get("/api/push/vapid-public-key")
@auth_required
def push_vapid_key():
    """Older name of /api/push/config, kept so cached pages keep working."""
    return push_config()


@bp.post("/api/push/subscribe")
@auth_required
@limiter.limit("60 per hour")
def push_subscribe():
    """Save (or refresh) this browser's subscription. Safe to call again: the same endpoint is never stored twice."""
    from ...services import push_service
    if not push_service.vapid_configured():
        return _push_error("server_not_configured", 503)
    from ...services import site_settings
    if not site_settings.enabled("push_enabled"):
        return _push_error("server_disabled", 503)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _push_error("save_failed")
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    try:
        sub = push_service.save_subscription(
            current_user, str(data.get("endpoint") or ""), str(keys.get("p256dh") or ""),
            str(keys.get("auth") or ""), (request.headers.get("User-Agent") or "")[:300])
    except ValueError:
        return _push_error("save_failed")
    except Exception:  # noqa: BLE001 - database or code problem: log the real traceback, answer with JSON
        db.session.rollback()
        current_app.logger.exception("push subscribe failed")
        return _push_error("server_error", 500)
    session["push_sub_id"] = sub.id           # lets logout remove exactly this browser
    return jsonify({"ok": True, "id": sub.id})


@bp.post("/api/push/unsubscribe")
@auth_required
@limiter.limit("30 per hour")
def push_unsubscribe():
    from ...services import push_service
    data = request.get_json(silent=True)
    endpoint = data.get("endpoint") if isinstance(data, dict) else None
    n = push_service.remove_subscription(current_user, endpoint if isinstance(endpoint, str) else None)
    session.pop("push_sub_id", None)
    return jsonify({"ok": True, "removed": n})


@bp.post("/api/push/sync")
@auth_required
@limiter.limit("20 per minute")
def push_sync():
    """Retry (a few times, bounded) pushes that failed earlier. Never creates new pushes."""
    from ...services import push_service
    return jsonify({"ok": True, **push_service.sync_pending_for_user(current_user.id)})


@bp.post("/api/push/ack")
@csrf.exempt                       # called by the service worker, which has no page / CSRF token; the signed token is the credential
@limiter.limit("120 per minute")
def push_ack():
    """The phone's service worker confirms it received a push. This is what turns a message into Delivered."""
    from ...services import push_service
    data = request.get_json(silent=True)
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token or len(token) > 1000:
        return jsonify({"ok": False, "error": {"code": "bad_token", "message": "Invalid receipt."}}), 400
    try:
        changed = push_service.acknowledge_receipt(token)
    except Exception:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception("push ack failed")
        return jsonify({"ok": False, "error": {"code": "server_error", "message": "Try again."}}), 500
    if changed is None:
        return jsonify({"ok": False, "error": {"code": "bad_token", "message": "Invalid or expired receipt."}}), 400
    return jsonify({"ok": True, "updated": changed})


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
