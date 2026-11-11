"""Web Push delivery using VAPID.

Compatible with PythonAnywhere Free: no background workers, no Redis.
Delivery is attempted inline when a notification is created. Pending
deliveries are also retried when the user reconnects (client calls /api/push/sync).

Important: a successful response from the browser push service only means the
service accepted the message. It does not prove the user saw the notification.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from flask import current_app, url_for

from ..extensions import db
from ..models import Notification, PushDelivery, PushSubscription, User
from ..utils.time import utcnow

log = logging.getLogger(__name__)

# Permanent HTTP statuses from push services that mean the subscription is dead.
GONE_STATUSES = {404, 410}
# Transient failures worth a later retry.
RETRY_STATUSES = {429, 500, 502, 503, 504}


def vapid_configured() -> bool:
    return bool(
        current_app.config.get("VAPID_PRIVATE_KEY")
        and current_app.config.get("VAPID_PUBLIC_KEY")
        and current_app.config.get("VAPID_CONTACT")
    )


def public_vapid_key() -> str | None:
    return current_app.config.get("VAPID_PUBLIC_KEY") or None


def save_subscription(user: User, endpoint: str, p256dh: str, auth: str, user_agent: str | None) -> PushSubscription:
    """Create or refresh a subscription for this endpoint."""
    endpoint = (endpoint or "").strip()
    if not endpoint or not p256dh or not auth:
        raise ValueError("Invalid subscription payload")

    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if sub is None:
        sub = PushSubscription(
            user_id=user.id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=(user_agent or "")[:300],
            status="active",
        )
        db.session.add(sub)
    else:
        # Endpoint may move between accounts after logout/login on the same browser.
        sub.user_id = user.id
        sub.p256dh = p256dh
        sub.auth = auth
        sub.user_agent = (user_agent or "")[:300]
        sub.status = "active"
        sub.failure_count = 0
        sub.updated_at = utcnow()
    db.session.commit()
    return sub


def remove_subscription(user: User, endpoint: str | None = None) -> int:
    """Remove one endpoint or every subscription belonging to the user."""
    q = PushSubscription.query.filter_by(user_id=user.id)
    if endpoint:
        q = q.filter_by(endpoint=endpoint)
    n = q.delete(synchronize_session=False)
    db.session.commit()
    return n


def list_active_subscriptions(user_id: int) -> list[PushSubscription]:
    return (
        PushSubscription.query.filter_by(user_id=user_id, status="active")
        .order_by(PushSubscription.id.desc())
        .all()
    )


def _build_payload(note: Notification) -> dict[str, Any]:
    """Payload the service worker receives. Never include private message text."""
    from . import notification_service as nsvc

    described = nsvc.describe(note)
    title = current_app.config.get("APP_NAME", "Goppo")
    body = described.get("text") or "New notification"
    url = note.link or url_for("notices.index")
    return {
        "title": title,
        "body": body,
        "url": url,
        "tag": note.dedupe_key or f"n:{note.id}",
        "notification_id": note.id,
        "type": note.type,
        "renotify": True,
    }


def _send_one(sub: PushSubscription, payload: dict) -> tuple[str, str | None]:
    """Return (status, error_message). status is accepted|expired|failed."""
    if not vapid_configured():
        return "failed", "VAPID not configured"

    try:
        from pywebpush import WebPusher, WebPushException
    except ImportError:
        return "failed", "pywebpush not installed"

    subscription_info = {
        "endpoint": sub.endpoint,
        "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
    }
    claims = {
        "sub": current_app.config["VAPID_CONTACT"],
    }
    try:
        WebPusher(subscription_info).send(
            data=json.dumps(payload),
            vapid_private_key=current_app.config["VAPID_PRIVATE_KEY"],
            vapid_claims=claims,
            ttl=int(current_app.config.get("PUSH_TTL_SECONDS", 86400)),
            timeout=12,
        )
        return "accepted", None
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        msg = str(exc)[:300]
        if status in GONE_STATUSES:
            return "expired", msg
        return "failed", msg
    except Exception as exc:  # noqa: BLE001
        return "failed", str(exc)[:300]


def _record_delivery(
    note: Notification,
    sub: PushSubscription,
    group_key: str,
    result: str,
    error: str | None,
) -> None:
    row = PushDelivery.query.filter_by(
        notification_id=note.id, subscription_id=sub.id
    ).first()
    now = utcnow()
    if row is None:
        row = PushDelivery(
            notification_id=note.id,
            subscription_id=sub.id,
            user_id=note.user_id,
            group_key=group_key,
            status=result,
            attempt_count=1,
            last_error=error,
            accepted_at=now if result == "accepted" else None,
        )
        db.session.add(row)
    else:
        # Do not re-send an already accepted delivery.
        if row.status == "accepted":
            return
        row.status = result
        row.attempt_count = (row.attempt_count or 0) + 1
        row.last_error = error
        row.updated_at = now
        if result == "accepted":
            row.accepted_at = now

    sub.last_attempt_at = now
    if result == "accepted":
        sub.last_success_at = now
        sub.failure_count = 0
        sub.status = "active"
    elif result == "expired":
        sub.status = "expired"
        sub.failure_count = (sub.failure_count or 0) + 1
    else:
        sub.failure_count = (sub.failure_count or 0) + 1
        if sub.failure_count >= 5:
            sub.status = "failed"


def enqueue_and_send(note: Notification) -> int:
    """Create delivery rows for all active subscriptions and attempt send.

    Returns how many subscriptions accepted the push.
    Safe to call multiple times: accepted deliveries are not resent.
    """
    if not vapid_configured():
        return 0

    user = db.session.get(User, note.user_id)
    if user is None:
        return 0

    # Respect preference flags.
    if note.type == "new_message" and not user.notify_messages:
        return 0
    if note.type == "announcement" and not user.notify_announcements:
        return 0

    subs = list_active_subscriptions(note.user_id)
    if not subs:
        return 0

    group_key = note.dedupe_key or f"n:{note.id}"
    payload = _build_payload(note)
    accepted = 0

    for sub in subs:
        # Skip if already accepted for this notification+subscription.
        existing = PushDelivery.query.filter_by(
            notification_id=note.id, subscription_id=sub.id, status="accepted"
        ).first()
        if existing:
            continue

        result, err = _send_one(sub, payload)
        _record_delivery(note, sub, group_key, result, err)
        if result == "accepted":
            accepted += 1

    db.session.commit()
    return accepted


def send_for_message_notice(note: Notification) -> int:
    """Called after upsert_message_notice. Uses the same notification row so
    grouped counts appear in the push body."""
    return enqueue_and_send(note)


def sync_pending_for_user(user_id: int, limit: int = 20) -> dict:
    """Retry pending/failed deliveries when the client comes back online.

    Called from /api/push/sync. Does not create new notifications; only pushes
    existing unread ones that never got an accepted delivery.
    """
    if not vapid_configured():
        return {"attempted": 0, "accepted": 0}

    subs = list_active_subscriptions(user_id)
    if not subs:
        return {"attempted": 0, "accepted": 0}

    # Unread notifications that still lack an accepted delivery for this user.
    notes = (
        Notification.query.filter_by(user_id=user_id, is_read=False)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .all()
    )
    attempted = 0
    accepted = 0
    for note in notes:
        # Preference check
        user = db.session.get(User, user_id)
        if user is None:
            break
        if note.type == "new_message" and not user.notify_messages:
            continue
        if note.type == "announcement" and not user.notify_announcements:
            continue

        group_key = note.dedupe_key or f"n:{note.id}"
        payload = _build_payload(note)
        for sub in subs:
            existing = PushDelivery.query.filter_by(
                notification_id=note.id, subscription_id=sub.id
            ).first()
            if existing and existing.status == "accepted":
                continue
            # Cap retries to avoid hammering.
            if existing and (existing.attempt_count or 0) >= 5:
                continue
            result, err = _send_one(sub, payload)
            _record_delivery(note, sub, group_key, result, err)
            attempted += 1
            if result == "accepted":
                accepted += 1

    db.session.commit()
    return {"attempted": attempted, "accepted": accepted}


def notify_after_commit(note: Notification) -> None:
    """Best-effort push after the notification row is committed.

    Failures are logged; they must never roll back the main transaction.
    """
    try:
        enqueue_and_send(note)
    except Exception:  # noqa: BLE001
        log.exception("push delivery failed for notification %s", getattr(note, "id", None))
