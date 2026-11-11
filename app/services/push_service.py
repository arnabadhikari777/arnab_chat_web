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
import hmac
import json
import logging
import secrets
from datetime import timedelta
from functools import lru_cache
from typing import Any

from flask import current_app, url_for
from sqlalchemy import func, or_

from ..extensions import db
from ..models import (Conversation, Message, Notification, PushDelivery, PushSubscription,
                      User)
from ..utils.time import utcnow
from . import vapid

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


def vapid_configured() -> bool:
    return vapid_status().configured


def public_vapid_key() -> str | None:
    """The public key, only when the WHOLE push setup is valid. The private key never leaves the server."""
    st = vapid_status()
    return st.public if st.configured else None


# ------------------------------------------------------------------ subscriptions
def validate_subscription(endpoint: str, p256dh: str, auth: str) -> str:
    endpoint = (endpoint or "").strip()
    if not endpoint.startswith("https://") or len(endpoint) > MAX_ENDPOINT_LEN:
        raise ValueError("bad_endpoint")
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


def _build_payload(note: Notification, user: User | None = None) -> dict[str, Any]:
    """What the service worker receives. Never contains private message text. Written in the RECIPIENT's language."""
    from . import notification_service as nsvc

    lang = getattr(user, "language", None)
    described = nsvc.describe(note, lang=lang)
    return {
        "title": current_app.config.get("APP_NAME", "Goppo"),
        "body": described.get("text") or "New notification",
        "url": note.link or url_for("notices.index"),
        "tag": note.dedupe_key or f"n:{note.id}",
        "notification_id": note.id,
        "type": note.type,
        "renotify": True,
    }


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
        from pywebpush import WebPushException, webpush
    except ImportError:
        return "failed", "pywebpush not installed"
    try:
        webpush(
            subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            data=json.dumps(payload),
            vapid_private_key=st.private_raw,
            vapid_claims={"sub": st.contact},                 # fresh dict: the library edits it
            ttl=int(current_app.config.get("PUSH_TTL_SECONDS", 86400)),
            timeout=int(current_app.config.get("PUSH_SEND_TIMEOUT_SECONDS", 6)),
            headers={"Topic": _topic(group_key)},
        )
        return "accepted", None
    except WebPushException as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        return ("expired" if code in GONE_STATUSES else "failed"), str(exc)[:300]
    except Exception as exc:  # noqa: BLE001
        return "failed", f"{exc.__class__.__name__}: {exc}"[:300]


def _record(note: Notification, sub: PushSubscription, group_key: str, row: PushDelivery | None,
            result: str, error: str | None, restart: bool = False) -> None:
    now = utcnow()
    if row is None:
        row = PushDelivery(notification_id=note.id, subscription_id=sub.id, user_id=note.user_id,
                           group_key=group_key, status=result, attempt_count=1, last_error=error,
                           accepted_at=now if result == "accepted" else None)
        db.session.add(row)
    else:
        row.status, row.last_error, row.updated_at = result, error, now
        row.attempt_count = 1 if restart else (row.attempt_count or 0) + 1
        row.accepted_at = now if result == "accepted" else row.accepted_at
    sub.last_attempt_at = now
    if result == "accepted":
        sub.last_success_at, sub.failure_count, sub.status = now, 0, "active"
    elif result == "expired":
        sub.status = "expired"
    else:
        sub.failure_count = (sub.failure_count or 0) + 1
        if sub.failure_count >= MAX_ATTEMPTS:
            sub.status = "failed"      # the app re-activates it the next time this browser re-registers


# ------------------------------------------------------------------ delivery acknowledgement
# "accepted" (the push service took it) is NOT "delivered". A message turns Delivered only when the
# recipient's service worker posts the single-use token that was inside that very push, AND the
# recipient is online (presence) at that moment or later. See docs in PushDelivery.
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _newest_message_id(note: Notification) -> int | None:
    """Newest message this notice talks about. An ack can never cover messages sent after the push."""
    if note.type != "new_message":
        return None
    cid = (note.payload or {}).get("conversation_id")
    if not isinstance(cid, int):
        return None
    return (db.session.query(func.max(Message.id))
            .filter(Message.conversation_id == cid, Message.sender_id != note.user_id).scalar())


def _arm(row: PushDelivery, note: Notification) -> dict:
    """Give THIS send its own random single-use token. Only its hash is stored. Returns the payload block."""
    token = secrets.token_urlsafe(32)
    cfg = current_app.config
    ttl = int(cfg.get("PUSH_TTL_SECONDS", 86400)) + int(cfg.get("PUSH_ACK_EXTRA_SECONDS", 300))
    row.ack_token_hash = _hash_token(token)
    row.ack_expires_at = utcnow() + timedelta(seconds=ttl)
    row.message_upto_id = _newest_message_id(note)
    return {"id": row.id, "token": token}


def _ack_failed(reason: str, delivery_id) -> bool:
    db.session.rollback()
    # Never log the token. Callers always give the same generic answer, so nothing leaks about which check failed.
    log.warning("push ack rejected (%s) for delivery %s", reason, delivery_id)
    return False


def _apply(row: PushDelivery, note: Notification) -> int:
    """Turn an acknowledged push into Delivered messages (caller commits). Idempotent and forward-only."""
    upto = row.acked_upto_id or 0
    if not upto or upto <= (row.applied_upto_id or 0):
        return 0
    row.applied_upto_id = upto
    cid = (note.payload or {}).get("conversation_id")
    conv = db.session.get(Conversation, cid) if isinstance(cid, int) else None
    if note.type != "new_message" or conv is None or not conv.has_member(row.user_id):
        return 0
    return (Message.query.filter(
        Message.conversation_id == conv.id, Message.sender_id != row.user_id,
        Message.status == "sent", Message.id <= upto,
    ).update({"status": "delivered", "delivered_at": utcnow()}, synchronize_session=False))


def ack_delivery(delivery_id, token, endpoint) -> bool:
    """Verify a service-worker acknowledgement. True only for a valid, first-time, unexpired one.

    Checks: the delivery exists, the token matches (constant time) and is unused and unexpired, the device
    endpoint is exactly the subscription this push was sent to (still active, same owner), the notice and the
    account still exist. The token is consumed with one conditional UPDATE, so two simultaneous copies of the
    same acknowledgement cannot both win.
    """
    if (not isinstance(delivery_id, int) or isinstance(delivery_id, bool) or delivery_id <= 0
            or not isinstance(token, str) or not 20 <= len(token) <= 100
            or not isinstance(endpoint, str) or not endpoint or len(endpoint) > MAX_ENDPOINT_LEN):
        return _ack_failed("malformed", delivery_id if isinstance(delivery_id, int) else None)
    row = db.session.get(PushDelivery, delivery_id)
    stored = row.ack_token_hash if row is not None else None
    given = _hash_token(token)
    if not stored or not hmac.compare_digest(stored.encode(), given.encode()):
        return _ack_failed("bad_or_used_token", delivery_id)
    now = utcnow()
    if row.ack_expires_at is None or row.ack_expires_at < now:
        return _ack_failed("token_expired", delivery_id)
    sub = db.session.get(PushSubscription, row.subscription_id)
    if (sub is None or sub.status != "active" or sub.user_id != row.user_id
            or not hmac.compare_digest(sub.endpoint.encode(), endpoint.encode())):
        return _ack_failed("subscription_mismatch", delivery_id)
    note = db.session.get(Notification, row.notification_id)
    user = db.session.get(User, row.user_id)
    if note is None or user is None or note.user_id != row.user_id or not user.is_active:
        return _ack_failed("notice_or_user_gone", delivery_id)

    covers = max(row.acked_upto_id or 0, row.message_upto_id or 0) or None
    won = (PushDelivery.query.filter(PushDelivery.id == row.id, PushDelivery.ack_token_hash == stored)
           .update({"ack_token_hash": None, "acked_at": now, "acked_upto_id": covers,
                    "status": "accepted", "accepted_at": row.accepted_at or now, "last_error": None},
                   synchronize_session=False))
    if won != 1:
        return _ack_failed("lost_race", delivery_id)
    sub.last_success_at, sub.failure_count = now, 0
    db.session.flush()
    db.session.refresh(row)
    from . import presence_service
    if presence_service.is_online(user):
        _apply(row, note)
    # Offline right now: the receipt stays recorded and is applied by apply_pending_acks() the moment
    # this person is online again. Nothing is marked Delivered while they are offline.
    db.session.commit()
    return True


def apply_pending_acks(user_id: int, limit: int = 20) -> int:
    """Called while this person is making authenticated requests (so they are online): apply receipts that
    arrived while they were offline. One indexed query; no write unless there is something to apply."""
    rows = (PushDelivery.query.filter(
        PushDelivery.user_id == user_id, PushDelivery.acked_at.isnot(None),
        or_(PushDelivery.applied_upto_id.is_(None), PushDelivery.applied_upto_id < PushDelivery.acked_upto_id),
    ).order_by(PushDelivery.id.asc()).limit(limit).all())
    if not rows:
        return 0
    total = 0
    for row in rows:
        note = db.session.get(Notification, row.notification_id)
        if note is not None:
            total += _apply(row, note)
    db.session.commit()
    return total


def enqueue_and_send(note: Notification, force: bool = False) -> int:
    """Send this notification to the person's devices. Returns how many devices accepted it.

    force=True means "this notice just got NEW activity" (another message in the same chat), so a
    delivery that was accepted earlier must be sent again with the new count.
    """
    if not vapid_configured():
        return 0
    user = db.session.get(User, note.user_id)
    if user is None or not _wanted(user, note):
        return 0
    subs = list_active_subscriptions(note.user_id)
    if not subs:
        return 0
    group_key = note.dedupe_key or f"n:{note.id}"
    base = _build_payload(note, user)
    rows = {d.subscription_id: d for d in PushDelivery.query.filter_by(notification_id=note.id).all()}
    accepted = 0
    for sub in subs:
        row = rows.get(sub.id)
        if row is not None and row.status == "accepted" and not force:
            continue
        if row is None:               # one row per notice+device (unique pair), created BEFORE sending so it has an id
            row = PushDelivery(notification_id=note.id, subscription_id=sub.id, user_id=note.user_id,
                               group_key=group_key, status="pending", attempt_count=0)
            db.session.add(row)
            db.session.flush()
            rows[sub.id] = row
        payload = {**base, "ack": _arm(row, note)}      # every send gets its own fresh single-use token
        result, err = _send_one(sub, payload, group_key)
        _record(note, sub, group_key, row, result, err, restart=force)
        accepted += result == "accepted"
    db.session.commit()
    return accepted


def sync_pending_for_user(user_id: int, limit: int = 10) -> dict:
    """Retry deliveries that FAILED earlier (network hiccup, push service busy). Bounded three ways:
    at most MAX_ATTEMPTS tries each, only while the push is still young (PUSH_TTL_SECONDS), only `limit` per call.
    It never creates pushes for notices that were not attempted before, so opening the app cannot cause a flood.
    """
    st = vapid_status()
    if not st.configured:
        return {"attempted": 0, "accepted": 0, "configured": False}
    subs = {s.id: s for s in list_active_subscriptions(user_id)}
    user = db.session.get(User, user_id)
    if not subs or user is None:
        return {"attempted": 0, "accepted": 0, "configured": True}
    cutoff = utcnow() - timedelta(seconds=int(current_app.config.get("PUSH_TTL_SECONDS", 86400)))
    rows = (PushDelivery.query.filter(
        PushDelivery.user_id == user_id, PushDelivery.status.in_(("pending", "failed")),
        PushDelivery.attempt_count < MAX_ATTEMPTS, PushDelivery.created_at >= cutoff,
        PushDelivery.subscription_id.in_(list(subs))).order_by(PushDelivery.updated_at.asc()).limit(limit).all())
    attempted = accepted = 0
    for row in rows:
        note = db.session.get(Notification, row.notification_id)
        if note is None or not _wanted(user, note):
            row.attempt_count = MAX_ATTEMPTS         # nothing to alert about any more: stop for good
            continue
        sub = subs[row.subscription_id]
        result, err = _send_one(sub, {**_build_payload(note, user), "ack": _arm(row, note)}, row.group_key)
        _record(note, sub, row.group_key, row, result, err)
        attempted += 1
        accepted += result == "accepted"
    db.session.commit()
    return {"attempted": attempted, "accepted": accepted, "configured": True}


def notify_after_commit(note: Notification, force: bool = False) -> None:
    """Best-effort push after the notification row is committed. Never raises."""
    try:
        enqueue_and_send(note, force=force)
    except Exception:  # noqa: BLE001
        db.session.rollback()
        log.exception("push delivery failed for notification %s", getattr(note, "id", None))
