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
from sqlalchemy.exc import IntegrityError

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
    if note.type == "incoming_call":   # a ringing phone is only useful for a minute; never replay an old ring
        from ..utils.time import utcnow
        if note.created_at and (utcnow() - note.created_at).total_seconds() > 90:
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
    if note.type == "incoming_call":
        p = note.payload or {}
        payload.update({"call_id": p.get("call_id"), "call_type": p.get("call_type"), "caller": p.get("full_name"),
                        "require_interaction": True})
    token = _ack_token(note, user, sub)
    if token:
        payload["ack"] = token       # the phone's service worker sends this back: it is the proof of receipt
    return payload


def build_native_payload(note: Notification, user: User | None = None) -> dict[str, Any]:
    """The DATA-ONLY payload a future native (FCM) sender will deliver to the Android app (Phase 3).

    Same contract as the Web Push payload: same ``tag`` / ``notification_id`` (so the app can drop a notification
    it already showed through another channel), same signed ``ack`` receipt (accepted by POST /api/push/ack), and
    never any private message text. It carries no device token and no secret. Nothing sends it in Phase 1.
    """
    payload = _build_payload(note, user, None)
    for web_only in ("renotify",):
        payload.pop(web_only, None)
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
            ttl=60 if payload.get("type") == "incoming_call" else int(current_app.config.get("PUSH_TTL_SECONDS", 86400)),
            timeout=int(current_app.config.get("PUSH_SEND_TIMEOUT_SECONDS", 6)),
            headers=({"Topic": _topic(group_key), "Urgency": "high"} if payload.get("type") == "incoming_call"
                     else {"Topic": _topic(group_key)}),
            **extra,
        )
        return "accepted", None
    except WebPushException as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        return ("expired" if code in GONE_STATUSES else "failed"), str(exc)[:300]
    except Exception as exc:  # noqa: BLE001 - e.g. a blocked redirect or a network error: failed, never a crash
        return "failed", exc.__class__.__name__


# ------------------------------------------------------------------ queue, retry and offline recovery
# One PushDelivery row = one notification on one device. The row is the durable queue entry:
#   pending  -> created, not sent yet (or the process stopped before the answer was saved)
#   failed   -> the push service refused / timed out; retried a bounded number of times by sync
#   accepted -> the push service queued it (phone may still be off; it arrives when the phone is back)
#   expired  -> the device is gone for good; its subscription is dropped
# "accepted" is NEVER a delivery proof. Only acked_at (the phone's receipt) is, see acknowledge_receipt().
EXPIRED_KEEP_DAYS = 1               # an expired subscription is kept this long, then removed for good
DELIVERY_KEEP_DAYS = 30             # old delivery rows are cleaned up after this


def _group_key(note: Notification) -> str:
    return note.dedupe_key or f"n:{note.id}"


def _push_available() -> bool:
    return _push_switch_on() and vapid_status().configured


def _get_or_create_delivery(note: Notification, sub: PushSubscription) -> PushDelivery:
    """The (notification, device) pair is unique, so two requests at the same moment cannot double-queue."""
    row = PushDelivery.query.filter_by(notification_id=note.id, subscription_id=sub.id).first()
    if row is not None:
        return row
    row = PushDelivery(notification_id=note.id, subscription_id=sub.id, user_id=sub.user_id,
                       group_key=_group_key(note), status="pending", attempt_count=0)
    try:
        with db.session.begin_nested():
            db.session.add(row)
            db.session.flush()
    except IntegrityError:
        row = PushDelivery.query.filter_by(notification_id=note.id, subscription_id=sub.id).first()
        if row is None:
            raise
    return row


def _attempt(row: PushDelivery, note: Notification, user: User, sub: PushSubscription) -> str:
    """One send to one device. Never raises. Returns accepted | failed | expired and saves the outcome."""
    row.attempt_count = (row.attempt_count or 0) + 1
    try:
        payload = _build_payload(note, user, sub)
        outcome, error = _send_one(sub, payload, row.group_key)
    except Exception as exc:  # noqa: BLE001 - a push problem must never reach the person sending a message
        outcome, error = "failed", exc.__class__.__name__
    if outcome not in ("accepted", "expired"):
        outcome = "failed"
    now = utcnow()
    sub.last_attempt_at = now
    row.status = outcome
    row.last_error = (error or "")[:300] or None
    if outcome == "accepted":
        row.accepted_at = now
        row.last_error = None
        sub.last_success_at = now
        sub.failure_count = 0
    elif outcome == "expired":
        sub.status = "expired"           # removed by cleanup_expired(); this device is not tried again
    else:
        sub.failure_count = (sub.failure_count or 0) + 1
    return outcome


def enqueue_and_send(note: Notification, force: bool = False) -> int:
    """Queue `note` for every active device of its recipient and try to send it now (short timeout).

    force=False: a device that already accepted this notification, or used up its attempts, is skipped.
    force=True : new activity in the same chat (the counter changed): send again, attempts start over.
    Returns how many devices the push service accepted. Accepted does NOT mean delivered.
    """
    if note is None or note.id is None or not _push_available():
        return 0
    user = db.session.get(User, note.user_id)
    if user is None or not user.is_active or not _wanted(user, note):
        return 0
    accepted = 0
    for sub in list_active_subscriptions(user.id):
        row = _get_or_create_delivery(note, sub)
        if force:
            row.attempt_count, row.acked_at = 0, None      # a new push needs its own receipt
        elif row.status == "accepted" or (row.attempt_count or 0) >= MAX_ATTEMPTS:
            continue
        if _attempt(row, note, user, sub) == "accepted":
            accepted += 1
        db.session.commit()                                 # save each device's result on its own
    return accepted


def notify_after_commit(note: Notification | None, force: bool = False) -> int:
    """Called after the message / notice is safely committed. A push problem of ANY kind is logged and
    swallowed: it must never turn a saved message into an error for the sender."""
    if note is None:
        return 0
    try:
        return enqueue_and_send(note, force=force)
    except Exception:  # noqa: BLE001
        db.session.rollback()
        log.exception("push after commit failed (notification %s)", getattr(note, "id", None))
        return 0


def cleanup_expired(user_id: int | None = None) -> int:
    """Remove subscriptions that were dead for a day (and their queue rows) and very old delivery rows."""
    now = utcnow()
    q = PushSubscription.query.filter(PushSubscription.status == "expired",
                                      PushSubscription.updated_at < now - timedelta(days=EXPIRED_KEEP_DAYS))
    if user_id is not None:
        q = q.filter(PushSubscription.user_id == user_id)
    ids = [s.id for s in q.all()]
    if ids:
        PushDelivery.query.filter(PushDelivery.subscription_id.in_(ids)).delete(synchronize_session=False)
        PushSubscription.query.filter(PushSubscription.id.in_(ids)).delete(synchronize_session=False)
    PushDelivery.query.filter(PushDelivery.updated_at < now - timedelta(days=DELIVERY_KEEP_DAYS)).delete(
        synchronize_session=False)
    db.session.commit()
    return len(ids)


def sync_pending_for_user(user_id: int, limit: int = 20) -> dict[str, int]:
    """Retry pushes of this person that failed earlier (called by /api/push/sync when the app opens or comes
    back online). Bounded: at most MAX_ATTEMPTS per notification+device, only for unread notices, only on
    active devices, only within the push TTL. It never creates a push that was not attempted before."""
    result = {"accepted": 0, "failed": 0, "expired": 0}
    try:
        cleanup_expired(user_id)
    except Exception:  # noqa: BLE001
        db.session.rollback()
        log.exception("push cleanup failed")
    if not _push_available():
        return result
    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        return result
    horizon = utcnow() - timedelta(seconds=int(current_app.config.get("PUSH_TTL_SECONDS", 86400)))
    rows = (PushDelivery.query
            .join(PushSubscription, PushSubscription.id == PushDelivery.subscription_id)
            .join(Notification, Notification.id == PushDelivery.notification_id)
            .filter(PushDelivery.user_id == user_id, PushDelivery.status.in_(("pending", "failed")),
                    PushDelivery.attempt_count < MAX_ATTEMPTS, PushDelivery.updated_at >= horizon,
                    PushSubscription.status == "active", Notification.is_read.is_(False))
            .order_by(PushDelivery.updated_at.asc()).limit(max(1, min(int(limit), 50))).all())
    for row in rows:
        try:
            note = db.session.get(Notification, row.notification_id)
            sub = db.session.get(PushSubscription, row.subscription_id)
            if note is None or sub is None or not _wanted(user, note):
                continue
            result[_attempt(row, note, user, sub)] += 1
            db.session.commit()
        except Exception:  # noqa: BLE001
            db.session.rollback()
            log.exception("push retry failed")
    return result
