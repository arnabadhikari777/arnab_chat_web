"""Web Push delivery using VAPID.

Compatible with PythonAnywhere Free: no background workers, no Redis.
Delivery is attempted inline (short timeout, few devices) right after a notification is committed.
Failed deliveries are retried a bounded number of times when the app calls /api/push/sync.

A successful answer from the browser's push service only means "accepted for delivery". It never proves
the person saw the notification, and a device that is switched off gets it only when it comes back
(and only within PUSH_TTL_SECONDS).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from functools import lru_cache
from typing import Any

from flask import current_app, url_for
from itsdangerous import BadData, URLSafeTimedSerializer

from ..extensions import db
from ..models import Conversation, Message, Notification, PushDelivery, PushSubscription, User
from ..utils.time import utcnow
from . import push_endpoint, vapid

log = logging.getLogger(__name__)

GONE_STATUSES = {404, 410}          # the subscription is dead for good
MAX_ATTEMPTS = 5                    # per notification+device, then we stop
MAX_ENDPOINT_LEN = 1000


# ------------------------------------------------------------------ configuration
@lru_cache(maxsize=8)
def _evaluate(private: str, public: str, contact: str) -> vapid.VapidStatus:
    return vapid.evaluate(private, public, contact)


def vapid_status() -> vapid.VapidStatus:
    cfg = current_app.config
    return _evaluate(cfg.get("VAPID_PRIVATE_KEY") or "", cfg.get("VAPID_PUBLIC_KEY") or "",
                     cfg.get("VAPID_CONTACT") or "")


def _push_switch_on() -> bool:
    """Admin switch (Admin > Settings). Off = nothing is pushed; messages and the Notice Box still work."""
    from . import site_settings
    return site_settings.enabled("push_enabled")


def vapid_configured() -> bool:
    return vapid_status().configured


def public_vapid_key() -> str | None:
    """The public key, only when the WHOLE push setup is valid. The private key never leaves the server."""
    st = vapid_status()
    return st.public if st.configured else None


# ------------------------------------------------------------------ subscriptions
def validate_subscription(endpoint: str, p256dh: str, auth: str) -> str:
    try:
        endpoint = push_endpoint.check_endpoint(endpoint)          # SSRF guard: host allow-list + public addresses only
    except push_endpoint.EndpointRejected as exc:
        log.warning("push endpoint rejected (%s)", exc.code)        # the URL itself is never logged
        raise ValueError("bad_endpoint") from None
    if not p256dh or not auth or len(p256dh) > 200 or len(auth) > 100:
        raise ValueError("bad_keys")
    return endpoint


def save_subscription(user: User, endpoint: str, p256dh: str, auth: str, user_agent: str | None) -> PushSubscription:
    """Create or refresh a subscription. The same endpoint is never stored twice."""
    endpoint = validate_subscription(endpoint, p256dh, auth)
    ua = (user_agent or "")[:300]
    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if sub is None:
        sub = PushSubscription(user_id=user.id, endpoint=endpoint, p256dh=p256dh, auth=auth,
                               user_agent=ua, status="active")
        db.session.add(sub)
    else:
        sub.user_id = user.id          # the endpoint moves with the browser after logout/login
        sub.p256dh, sub.auth, sub.user_agent = p256dh, auth, ua
        sub.status = "active"
        sub.failure_count = 0
        sub.updated_at = utcnow()
    db.session.flush()
    _prune(user.id, keep_id=sub.id)
    db.session.commit()
    return sub


def _prune(user_id: int, keep_id: int) -> None:
    """Drop dead endpoints and keep only the newest few devices per person."""
    PushSubscription.query.filter(
        PushSubscription.user_id == user_id, PushSubscription.status == "expired",
        PushSubscription.id != keep_id).delete(synchronize_session=False)
    limit = int(current_app.config.get("PUSH_MAX_DEVICES_PER_USER", 5))
    rows = (PushSubscription.query.filter_by(user_id=user_id)
            .order_by(PushSubscription.updated_at.desc(), PushSubscription.id.desc()).all())
    for old in rows[limit:]:
        if old.id != keep_id:
            db.session.delete(old)


def remove_subscription(user: User, endpoint: str | None = None) -> int:
    q = PushSubscription.query.filter_by(user_id=user.id)
    if endpoint:
        q = q.filter_by(endpoint=endpoint)
    n = q.delete(synchronize_session=False)
    db.session.commit()
    return n


def forget_session_subscription(user: User, sub_id) -> None:
    """On logout: this browser must stop receiving the account's notifications."""
    if not isinstance(sub_id, int):
        return
    PushSubscription.query.filter_by(id=sub_id, user_id=user.id).delete(synchronize_session=False)
    db.session.commit()


def list_active_subscriptions(user_id: int) -> list[PushSubscription]:
    limit = int(current_app.config.get("PUSH_MAX_DEVICES_PER_USER", 5))
    return (PushSubscription.query.filter_by(user_id=user_id, status="active")
            .order_by(PushSubscription.id.desc()).limit(limit).all())


# ------------------------------------------------------------------ sending
def _wanted(user: User, note: Notification) -> bool:
    if note.is_read:
        return False
    if note.type == "new_message" and not user.notify_messages:
        return False
    if note.type == "announcement" and not user.notify_announcements:
        return False
    return True


def _build_payload(note: Notification, user: User | None = None, sub: PushSubscription | None = None) -> dict[str, Any]:
    """What the service worker receives. Never contains private message text. Written in the RECIPIENT's language."""
    from . import notification_service as nsvc

    lang = getattr(user, "language", None)
    described = nsvc.describe(note, lang=lang)
    payload = {
        "title": current_app.config.get("APP_NAME", "Goppo"),
        "body": described.get("text") or "New notification",
        "url": note.link or url_for("notices.index"),
        "tag": note.dedupe_key or f"n:{note.id}",
        "notification_id": note.id,
        "type": note.type,
        "renotify": True,
    }
    token = _ack_token(note, user, sub)
    if token:
        payload["ack"] = token       # the phone's service worker sends this back: it is the proof of receipt
    return payload


# ------------------------------------------------------------------ device receipt (drives the double tick)
_ACK_SALT = "goppo-push-ack-v1"


def _ack_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_ACK_SALT)


def _ack_token(note: Notification, user: User | None, sub: PushSubscription | None) -> str | None:
    """A signed receipt token for a new-message push: it names the person, the chat, the newest message that
    this push announces, the notification and the device. Only the device that decrypted the push has it."""
    if note.type != "new_message" or user is None:
        return None
    conv_id = (note.payload or {}).get("conversation_id")
    if not isinstance(conv_id, int):
        return None
    newest = (db.session.query(db.func.max(Message.id))
              .filter(Message.conversation_id == conv_id, Message.sender_id != user.id,
                      Message.deleted_for_all.is_(False)).scalar())
    if not newest:
        return None
    return _ack_serializer().dumps({"u": user.id, "c": conv_id, "m": int(newest), "n": note.id,
                                    "s": sub.id if sub is not None else None})


def acknowledge_receipt(token: str) -> int | None:
    """The phone confirms it received a push. Marks that chat's waiting messages up to the announced id as
    Delivered. Returns how many messages changed, or None if the token is invalid / expired / not usable.

    Safe to call twice (idempotent). Cannot be forged (signed with SECRET_KEY) and can never touch another
    person's chat (the token is bound to the recipient and re-checked against the conversation)."""
    from . import message_service

    try:
        data = _ack_serializer().loads(token or "", max_age=int(current_app.config.get("PUSH_ACK_MAX_AGE_SECONDS", 90000)))
        uid, conv_id, upto, nid, sid = int(data["u"]), int(data["c"]), int(data["m"]), int(data["n"]), data.get("s")
    except (BadData, KeyError, TypeError, ValueError):
        return None
    user = db.session.get(User, uid)
    conv = db.session.get(Conversation, conv_id)
    if user is None or not user.is_active or conv is None or not conv.has_member(uid):
        return None
    if isinstance(sid, int):
        PushDelivery.query.filter_by(notification_id=nid, subscription_id=sid, user_id=uid, acked_at=None).update(
            {"acked_at": utcnow()}, synchronize_session=False)
    changed = message_service.mark_delivered_upto(user, conv, upto)
    db.session.commit()
    return changed


def _topic(group_key: str) -> str:
    """Push services keep only the newest queued message per Topic, so a phone that was offline gets ONE
    notification per chat (with the latest count) instead of a pile."""
    return vapid.b64url(hashlib.sha256(group_key.encode()).digest())[:32]


def _send_one(sub: PushSubscription, payload: dict, group_key: str) -> tuple[str, str | None]:
    """Return (accepted|expired|failed, error text)."""
    st = vapid_status()
    if not st.configured:
        return "failed", "VAPID not configured"
    try:
        sub_endpoint = push_endpoint.check_endpoint(sub.endpoint)  # DNS can change after saving: check again now
    except push_endpoint.EndpointRejected as exc:
        log.warning("push send blocked, endpoint not allowed (%s)", exc.code)
        return "expired", "endpoint not allowed"                   # never contacted; the device is dropped as dead
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return "failed", "pywebpush not installed"
    extra = {}
    try:
        import inspect

        if "requests_session" in inspect.signature(webpush).parameters:
            import requests

            session = requests.Session()
            session.max_redirects = 0                              # a push service never needs to redirect a POST
            extra["requests_session"] = session
    except (TypeError, ValueError, ImportError):
        pass
    try:
        webpush(
            subscription_info={"endpoint": sub_endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            data=json.dumps(payload),
            vapid_private_key=st.private_raw,
            vapid_claims={"sub": st.contact},                 # fresh dict: the library edits it
            ttl=int(current_app.config.get("PUSH_TTL_SECONDS", 86400)),
            timeout=int(current_app.config.get("PUSH_SEND_TIMEOUT_SECONDS", 6)),
            headers={"Topic": _topic(group_key)},
            **extra,
        )
        return "accepted", None
    except WebPushException as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        return ("expired" if code in GONE_STATUSES else "failed"), str(exc)[:300]
    except Exception as exc:  # noqa: BLE001 - e.g. a blocked redirect or a network error: failed, never a crash
        return "failed", exc.__class__.__name__
