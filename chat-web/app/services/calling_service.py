"""Voice/video calls: signed tokens for the Render calling server, the call state machine and call history.

Security model
  * Every call lives in ``call_history``. Roles (caller / receiver) are ALWAYS derived from that row, never from
    anything the browser sends.
  * Tokens are short-lived, HMAC-signed and carry the role, a random ``jti`` (single use at the calling server)
    and ``iat``/``exp``. A token is only issued to a participant of a live call.
  * Status changes follow a fixed table of allowed transitions per role. Terminal states never change again.
  * Duration is computed on the server; whatever duration a browser sends is ignored.
  * A user can be in one live call only (busy); stale calls expire lazily, so nobody stays "busy" forever.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import timedelta

from flask import current_app, url_for
from sqlalchemy import func, or_

from ..extensions import db
from ..models import CallHistory, User
from ..utils.time import utcnow
from . import conversation_service

ACTIVE = ("initiated", "ringing", "connected")
TERMINAL = ("completed", "missed", "rejected", "busy", "failed", "cancelled")

# (from_status, to_status) -> who may do it: "caller", "receiver", "either"
TRANSITIONS = {
    ("initiated", "ringing"): "receiver",
    ("initiated", "connected"): "either",
    ("ringing", "connected"): "either",
    ("initiated", "cancelled"): "caller",
    ("ringing", "cancelled"): "caller",
    ("initiated", "rejected"): "receiver",
    ("ringing", "rejected"): "receiver",
    ("initiated", "busy"): "receiver",
    ("ringing", "busy"): "receiver",
    ("initiated", "failed"): "either",
    ("ringing", "failed"): "either",
    ("connected", "completed"): "either",
    ("connected", "failed"): "either",
    # "missed" is set by the server only (ring timeout), never by a browser request
}


class CallingError(Exception):
    def __init__(self, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


def _secret() -> bytes:
    secret = current_app.config.get("CALLING_HMAC_SECRET")
    if not secret or len(secret) < 32:
        raise CallingError("Calling is not configured on the server", 503)
    return secret.encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_call_token(*, user_id: int, peer_id: int, conversation_id: int, call_type: str, call_id: str,
                    role: str, ttl_seconds: int | None = None) -> str:
    """base64url(claims) + "." + base64url(HMAC-SHA256). Same format the Go server verifies."""
    ttl = int(ttl_seconds or current_app.config.get("CALLING_TOKEN_TTL_SECONDS", 120))
    now = utcnow()
    claims = {"v": 1, "uid": user_id, "pid": peer_id, "cid": conversation_id or 0, "type": call_type, "call_id": call_id,
              "role": role, "jti": secrets.token_urlsafe(18), "iat": int(now.timestamp()),
              "exp": int((now + timedelta(seconds=ttl)).timestamp())}
    payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    mac = hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest()
    return f"{payload}.{_b64(mac)}"


def _role(record: CallHistory, user: User) -> str | None:
    if user.id == record.caller_id:
        return "caller"
    if user.id == record.receiver_id:
        return "receiver"
    return None


def _peer_of(record: CallHistory, user: User) -> int:
    return record.receiver_id if user.id == record.caller_id else record.caller_id


def _finish(record: CallHistory, status: str, *, by: int | None, reason: str | None = None) -> None:
    now = utcnow()
    record.status, record.ended_at, record.ended_by_id, record.end_reason = status, now, by, reason or status
    if status == "completed" and record.answered_at:
        record.duration_seconds = max(0, min(int((now - record.answered_at).total_seconds()),
                                             int(current_app.config.get("CALLING_MAX_CALL_SECONDS", 14400))))
    else:
        record.duration_seconds = 0


def expire_stale(user_ids: tuple[int, ...] | None = None) -> int:
    """Unanswered calls become "missed"; connected calls nobody closed become "failed". Returns how many changed."""
    now = utcnow()
    ring = timedelta(seconds=int(current_app.config.get("CALLING_RING_SECONDS", 60)))
    maxlen = timedelta(seconds=int(current_app.config.get("CALLING_MAX_CALL_SECONDS", 14400)))
    q = CallHistory.query.filter(CallHistory.status.in_(ACTIVE))
    if user_ids:
        q = q.filter(or_(CallHistory.caller_id.in_(user_ids), CallHistory.receiver_id.in_(user_ids)))
    n = 0
    missed: list[tuple[CallHistory, object]] = []
    for rec in q.all():
        if rec.status in ("initiated", "ringing") and now - rec.started_at > ring:
            _finish(rec, "missed", by=None, reason="timeout")
            missed.append((rec, _notify_missed(rec)))
            n += 1
        elif rec.status == "connected" and rec.answered_at and now - rec.answered_at > maxlen:
            _finish(rec, "failed", by=None, reason="max_duration")
            n += 1
        elif rec.status == "connected" and not rec.answered_at and now - rec.started_at > maxlen:
            _finish(rec, "failed", by=None, reason="stale")
            n += 1
    if n:
        db.session.commit()
        for rec, note in missed:                      # native phones: stop the ringing screen, show "missed call"
            _native_call_event(rec, "missed", note)
    return n


def _native_call_event(rec: CallHistory, reason: str, note=None) -> None:
    """Best effort, never raises: FCM to the receiver's Android phones (browser Web Push is unchanged)."""
    try:
        from . import fcm_service

        fcm_service.deliver_call_event(rec, reason)
        if note is not None:
            fcm_service.deliver_notification(note)
    except Exception:  # noqa: BLE001
        current_app.logger.exception("native call event failed")


def _notify_missed(rec: CallHistory):
    try:
        from .notification_service import add_notification
        caller = db.session.get(User, rec.caller_id)
        return add_notification(rec.receiver_id, "missed_call",
                         {"call_id": rec.call_id, "call_type": rec.call_type, "caller_id": rec.caller_id,
                          "username": caller.username if caller else "", "full_name": caller.full_name if caller else ""},
                         link=url_for("chat.room", conversation_id=rec.conversation_id) if rec.conversation_id else None,
                         dedupe_key=f"call:{rec.call_id}:missed")
    except Exception:  # noqa: BLE001 - history must be saved even if the notice cannot be
        current_app.logger.exception("missed-call notice failed")
        return None


def active_call_for(user_id: int) -> CallHistory | None:
    return (CallHistory.query.filter(CallHistory.status.in_(ACTIVE),
                                     or_(CallHistory.caller_id == user_id, CallHistory.receiver_id == user_id))
            .order_by(CallHistory.started_at.desc()).first())


def _peer_dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "avatar_url": u.avatar_url}


def initiate_call(caller: User, peer_id: int, call_type: str) -> dict:
    if call_type not in ("voice", "video"):
        raise CallingError("Invalid call type")
    if peer_id == caller.id:
        raise CallingError("Cannot call yourself")
    peer = db.session.get(User, peer_id)
    if not peer or not peer.is_active or not peer.is_email_verified:
        raise CallingError("User not available", 404)
    # Calls only inside an existing conversation: this can never be used to reach someone who did not accept a chat.
    conv = conversation_service.find_between(caller.id, peer.id)
    if conv is None:
        raise CallingError("You can only call people you already chat with", 403)
    _secret()  # fail early (503) if calling is not configured

    expire_stale((caller.id, peer.id))
    mine = active_call_for(caller.id)
    if mine is not None:
        raise CallingError("You are already in a call", 409, busy=True, call_id=mine.call_id)
    if active_call_for(peer.id) is not None:
        rec = CallHistory(call_id=str(uuid.uuid4()), conversation_id=conv.id, caller_id=caller.id, receiver_id=peer.id,
                          call_type=call_type, status="busy", started_at=utcnow())
        _finish(rec, "busy", by=peer.id, reason="peer_busy")
        db.session.add(rec)
        db.session.commit()
        raise CallingError("The other person is busy in another call", 409, busy=True, peer_busy=True)

    call_id = str(uuid.uuid4())
    rec = CallHistory(call_id=call_id, conversation_id=conv.id, caller_id=caller.id, receiver_id=peer.id,
                      call_type=call_type, status="initiated", started_at=utcnow())
    db.session.add(rec)
    note = None
    try:
        from .notification_service import add_notification
        note = add_notification(peer.id, "incoming_call",
                                {"call_id": call_id, "call_type": call_type, "caller_id": caller.id,
                                 "username": caller.username, "full_name": caller.full_name, "avatar_url": caller.avatar_url},
                                link=url_for("chat.room", conversation_id=conv.id) + f"?incoming={call_id}",
                                dedupe_key=f"call:{call_id}")
    except Exception:  # noqa: BLE001
        current_app.logger.exception("incoming-call notice failed")
    db.session.commit()
    if note is not None:  # Web Push wakes a phone whose browser is in the background
        from . import push_service
        push_service.notify_after_commit(note, force=True)

    token = make_call_token(user_id=caller.id, peer_id=peer.id, conversation_id=conv.id, call_type=call_type,
                            call_id=call_id, role="caller")
    return {"call_id": call_id, "conversation_id": conv.id, "call_type": call_type, "role": "caller", "token": token,
            "peer": _peer_dict(peer), "calling_server_url": current_app.config.get("CALLING_SERVER_URL", ""),
            "expires_in": int(current_app.config.get("CALLING_TOKEN_TTL_SECONDS", 120)),
            "ring_seconds": int(current_app.config.get("CALLING_RING_SECONDS", 60))}


def issue_token(user: User, call_id: str) -> dict:
    """First token for the callee (Accept) or a refreshed token for either party (reconnect / cold start)."""
    rec = CallHistory.query.filter_by(call_id=call_id).first()
    if rec is None or _role(rec, user) is None:
        raise CallingError("Call not found", 404)  # same answer for "missing" and "not yours": nothing leaks
    expire_stale((rec.caller_id, rec.receiver_id))
    db.session.refresh(rec)
    if rec.status not in ACTIVE:
        raise CallingError("Call already ended", 409, call_status=rec.status)
    role = "caller" if user.id == rec.caller_id else "callee"
    token = make_call_token(user_id=user.id, peer_id=_peer_of(rec, user), conversation_id=rec.conversation_id or 0,
                            call_type=rec.call_type, call_id=call_id, role=role)
    return {"call_id": call_id, "token": token, "call_type": rec.call_type, "role": role, "status": rec.status,
            "calling_server_url": current_app.config.get("CALLING_SERVER_URL", ""),
            "expires_in": int(current_app.config.get("CALLING_TOKEN_TTL_SECONDS", 120))}


def accept_call(user: User, call_id: str) -> dict:
    """Receiver presses Accept: records it and hands out the receiver's token."""
    rec = CallHistory.query.filter_by(call_id=call_id).first()
    if rec is None or rec.receiver_id != user.id:
        raise CallingError("Call not found", 404)
    expire_stale((rec.caller_id, rec.receiver_id))
    db.session.refresh(rec)
    if rec.status not in ("initiated", "ringing"):
        raise CallingError("Call is no longer ringing", 409, call_status=rec.status)
    if rec.accepted_at is None:
        rec.accepted_at = utcnow()
        db.session.commit()
        _native_call_event(rec, "answered_elsewhere")     # the accepting phone ignores it; the user's other phones stop ringing
    return issue_token(user, call_id)


def update_call_status(user: User, call_id: str, status: str, *, duration_seconds: int | None = None) -> CallHistory:
    """Apply one transition. `duration_seconds` is accepted for old clients and deliberately ignored."""
    rec = CallHistory.query.filter_by(call_id=call_id).first()
    if rec is None or _role(rec, user) is None:
        raise CallingError("Call not found", 404)
    if status not in ACTIVE + TERMINAL or status == "initiated":
        raise CallingError("Invalid status")
    expire_stale((rec.caller_id, rec.receiver_id))
    db.session.refresh(rec)
    if rec.status in TERMINAL:
        if rec.status == status:
            return rec  # idempotent retry: nothing changes (no second completion, duration stays)
        raise CallingError(f"Call already {rec.status}", 409, call_status=rec.status)
    if status == rec.status:
        return rec
    who = TRANSITIONS.get((rec.status, status))
    if who is None:
        raise CallingError(f"Cannot change a {rec.status} call to {status}", 409, call_status=rec.status)
    role = _role(rec, user)
    if who != "either" and who != role:
        raise CallingError("Not allowed for your role in this call", 403)
    if status == "connected" and rec.accepted_at is None:
        raise CallingError("The call was not accepted yet", 409)
    now = utcnow()
    if status == "ringing":
        rec.status, rec.ringing_at = "ringing", now
    elif status == "connected":
        rec.status = "connected"
        rec.answered_at = rec.answered_at or now
    else:
        _finish(rec, status, by=user.id)
    db.session.commit()
    if status in ("cancelled", "rejected", "busy"):       # nobody should keep ringing for a call that just ended
        _native_call_event(rec, "cancelled" if status == "cancelled" else "handled_elsewhere")
    return rec


def incoming_for(user: User) -> dict | None:
    """The call currently ringing for this user (poll target). Marks it "ringing" = delivered to a device."""
    expire_stale((user.id,))
    rec = (CallHistory.query.filter(CallHistory.receiver_id == user.id, CallHistory.status.in_(("initiated", "ringing")))
           .order_by(CallHistory.started_at.desc()).first())
    if rec is None:
        return None
    if rec.status == "initiated":
        rec.status, rec.ringing_at = "ringing", utcnow()
        db.session.commit()
    caller = db.session.get(User, rec.caller_id)
    left = max(0, int(current_app.config.get("CALLING_RING_SECONDS", 60)) - int((utcnow() - rec.started_at).total_seconds()))
    return {"call_id": rec.call_id, "call_type": rec.call_type, "conversation_id": rec.conversation_id,
            "caller": _peer_dict(caller), "seconds_left": left}


def call_state(user: User, call_id: str) -> dict:
    rec = CallHistory.query.filter_by(call_id=call_id).first()
    if rec is None or _role(rec, user) is None:
        raise CallingError("Call not found", 404)
    expire_stale((rec.caller_id, rec.receiver_id))
    db.session.refresh(rec)
    return rec.to_dict() | {"accepted": rec.accepted_at is not None}


def list_calls_for_user(user: User, limit: int = 50) -> list[dict]:
    expire_stale((user.id,))
    q = (CallHistory.query.filter(or_(CallHistory.caller_id == user.id, CallHistory.receiver_id == user.id))
         .order_by(CallHistory.started_at.desc()).limit(max(1, min(limit, 100))))
    return [c.to_dict() for c in q.all()]


def admin_call_stats() -> dict:
    total = CallHistory.query.count()
    voice = CallHistory.query.filter_by(call_type="voice").count()
    video = CallHistory.query.filter_by(call_type="video").count()
    completed = CallHistory.query.filter_by(status="completed").count()
    missed = CallHistory.query.filter_by(status="missed").count()
    avg_dur = (db.session.query(func.avg(CallHistory.duration_seconds))
               .filter(CallHistory.status == "completed", CallHistory.duration_seconds > 0).scalar() or 0)
    return {"total_calls": total, "voice_calls": voice, "video_calls": video, "completed_calls": completed,
            "missed_calls": missed, "average_duration_seconds": round(float(avg_dur), 1)}
