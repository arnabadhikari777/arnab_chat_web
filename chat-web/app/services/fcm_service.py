"""Native Android push through Firebase Cloud Messaging HTTP v1 (Phase 3).

Separate from browser Web Push (``push_service``): different table (``native_devices``), different credentials,
different failure rules. Both are triggered from ``push_service.notify_after_commit`` so a message / incoming call
is announced on every channel the person has, once per channel.

Credentials (never in the APK, never in the repository, never logged)
  * A Firebase *service account* JSON, supplied through ONE of these environment variables on the server:
      FCM_SERVICE_ACCOUNT_B64   base64 of the JSON file (easiest to paste)
      FCM_SERVICE_ACCOUNT_JSON  the JSON text itself
      FCM_SERVICE_ACCOUNT_FILE  path of the JSON file (keep it outside the web root, chmod 600)
  * The service account's private key signs a short-lived JWT; Google's OAuth 2.0 endpoint exchanges it for an
    access token (cached ~55 minutes in the worker process); the token authorises ``messages:send``.
  * The legacy FCM API and legacy server keys are NOT used.

Delivery rules
  * DATA-ONLY messages: the Android app builds the notification / incoming-call screen itself.
  * Incoming calls: priority HIGH, 60 s time-to-live, collapse key = call id.  Messages: collapse key = chat.
  * Never any private message text in a payload (same rule as Web Push). Values are strings, as FCM requires.
  * A token Google reports as dead (UNREGISTERED / INVALID_ARGUMENT / SENDER_ID_MISMATCH) is invalidated and
    cleared; temporary failures only count up and are retried by the next event, never in a loop.
  * Device tokens appear in logs never: only the device row id and the Google error status.
  * Runs inside the request that created the event (short timeouts, devices in parallel): no worker, Redis or Celery.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from flask import current_app

from ..extensions import db
from ..models import CallHistory, NativeDevice, Notification, User
from ..utils.time import utcnow

log = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SEND_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
DEAD_STATUSES = {"UNREGISTERED", "INVALID_ARGUMENT", "SENDER_ID_MISMATCH", "NOT_FOUND"}
MAX_PARALLEL = 5
CALL_TTL_SECONDS = 60


# ------------------------------------------------------------------ configuration
@dataclass(frozen=True)
class Status:
    configured: bool
    reason: str = ""                  # why not (never contains a secret)
    project_id: str = ""
    client_email: str = ""


class _Account:
    def __init__(self, data: dict):
        self.client_email: str = data["client_email"]
        self.private_key: str = data["private_key"]
        self.project_id: str = data["project_id"]
        self.token_uri: str = data.get("token_uri") or TOKEN_URI


@lru_cache(maxsize=4)
def _parse(raw: str, project_override: str) -> tuple[_Account | None, str]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None, "service account is not valid JSON"
    if not isinstance(data, dict) or data.get("type") != "service_account":
        return None, "not a service-account key (type must be service_account)"
    for key in ("client_email", "private_key"):
        if not isinstance(data.get(key), str) or not data[key]:
            return None, f"service account has no {key}"
    if "BEGIN PRIVATE KEY" not in data["private_key"]:
        return None, "private_key is not a PEM key"
    project = project_override or data.get("project_id") or ""
    if not project:
        return None, "no project_id (set FCM_PROJECT_ID)"
    token_uri = data.get("token_uri") or TOKEN_URI
    if urlparse(token_uri).hostname != "oauth2.googleapis.com" or not token_uri.startswith("https://"):
        return None, "token_uri must be Google's OAuth endpoint"      # the key file can never point us elsewhere
    return _Account({**data, "project_id": project}), ""


def _raw_account() -> str:
    cfg = current_app.config
    if cfg.get("FCM_SERVICE_ACCOUNT_JSON"):
        return cfg["FCM_SERVICE_ACCOUNT_JSON"]
    if cfg.get("FCM_SERVICE_ACCOUNT_B64"):
        try:
            return base64.b64decode(cfg["FCM_SERVICE_ACCOUNT_B64"]).decode("utf-8")
        except Exception:  # noqa: BLE001
            return ""
    path = cfg.get("FCM_SERVICE_ACCOUNT_FILE")
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""
    return ""


def _account() -> tuple[_Account | None, str]:
    raw = _raw_account()
    if not raw:
        return None, "FCM_SERVICE_ACCOUNT_* is not set"
    return _parse(raw, current_app.config.get("FCM_PROJECT_ID") or "")


def status() -> Status:
    acc, why = _account()
    if acc is None:
        return Status(False, why)
    return Status(True, "", acc.project_id, acc.client_email)


def configured() -> bool:
    return status().configured


# ------------------------------------------------------------------ OAuth 2.0 (service account -> access token)
_token_lock = threading.Lock()
_token_cache: dict[str, Any] = {"token": None, "exp": 0.0, "key": None}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_assertion(acc: _Account, now: int | None = None) -> str:
    """RS256-signed JWT that Google exchanges for an access token."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    now = int(now if now is not None else time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {"iss": acc.client_email, "scope": SCOPE, "aud": acc.token_uri, "iat": now, "exp": now + 3600}
    signing_input = (_b64url(json.dumps(header, separators=(",", ":")).encode())
                     + "." + _b64url(json.dumps(claims, separators=(",", ":")).encode()))
    key = serialization.load_pem_private_key(acc.private_key.encode("utf-8"), password=None)
    sig = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + _b64url(sig)


def _post(url: str, *, headers: dict | None = None, data: dict | None = None, json_body: dict | None = None,
          timeout: float = 6.0) -> tuple[int, dict]:
    """The ONLY network call of this module (tests replace it). Never follows redirects."""
    import requests

    try:
        r = requests.post(url, headers=headers, data=data, json=json_body, timeout=timeout, allow_redirects=False)
    except requests.RequestException as exc:
        return 0, {"error": exc.__class__.__name__}
    try:
        body = r.json()
    except ValueError:
        body = {}
    return r.status_code, body if isinstance(body, dict) else {}


def access_token(acc: _Account, force: bool = False) -> str | None:
    with _token_lock:
        key = (acc.client_email, acc.project_id)
        if (not force and _token_cache["token"] and _token_cache["key"] == key
                and _token_cache["exp"] - time.time() > 60):
            return _token_cache["token"]
        try:
            assertion = make_assertion(acc)
        except Exception:  # noqa: BLE001 - a malformed key must not crash a message send
            log.error("FCM: the service-account private key could not be used")
            return None
        code, body = _post(acc.token_uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                                "assertion": assertion}, timeout=6.0)
        token = body.get("access_token") if code == 200 else None
        if not isinstance(token, str) or not token:
            log.error("FCM: OAuth token request failed (http %s, %s)", code, str(body.get("error"))[:60])
            return None
        _token_cache.update(token=token, key=key, exp=time.time() + min(int(body.get("expires_in") or 3600), 3600) - 300)
        return token


def reset_token_cache() -> None:
    with _token_lock:
        _token_cache.update(token=None, exp=0.0, key=None)


# ------------------------------------------------------------------ payloads
def _switch_on() -> bool:
    from . import site_settings
    return site_settings.enabled("push_enabled")        # the admin switch also stops native push


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _https(url: Any) -> str:
    return url if isinstance(url, str) and url.startswith("https://") and len(url) <= 500 else ""


def _safe_path(link: Any) -> str:
    """A site-relative path only. The app refuses anything else, this keeps the payload clean too."""
    return link if isinstance(link, str) and link.startswith("/") and not link.startswith("//") and len(link) <= 300 else ""


def build_data(note: Notification, user: User) -> dict[str, str] | None:
    """The data payload for one notification, or None if this type is not announced natively. No private text."""
    from . import notification_service as nsvc, push_service

    p = note.payload or {}
    lang = getattr(user, "language", None)
    base = {"type": note.type, "notification_id": _s(note.id), "path": _safe_path(note.link),
            "sent_at": _s(int(time.time() * 1000)), "app_name": current_app.config.get("APP_NAME", "Goppo")}
    if note.type == "incoming_call":
        ring = int(current_app.config.get("CALLING_RING_SECONDS", 60))
        started = int(note.created_at.timestamp() * 1000) if note.created_at else int(time.time() * 1000)
        return {**base, "call_id": _s(p.get("call_id")), "call_type": "video" if p.get("call_type") == "video" else "voice",
                "caller_id": _s(p.get("caller_id")), "caller_name": _s(p.get("full_name") or p.get("username")),
                "avatar_url": _https(p.get("avatar_url")), "expires_at": _s(started + ring * 1000)}
    if note.type == "missed_call":
        return {**base, "call_id": _s(p.get("call_id")), "call_type": "video" if p.get("call_type") == "video" else "voice",
                "caller_name": _s(p.get("full_name") or p.get("username")),
                "body": nsvc.describe(note, lang=lang).get("text") or ""}
    if note.type == "new_message":
        sender = db.session.get(User, p.get("user_id")) if isinstance(p.get("user_id"), int) else None
        out = {**base, "conversation_id": _s(p.get("conversation_id")), "sender_id": _s(p.get("user_id")),
               "sender_name": _s(p.get("full_name") or p.get("username")), "count": _s(int(p.get("count", 1))),
               "avatar_url": _https(sender.avatar_url if sender else None),
               "body": nsvc.describe(note, lang=lang).get("text") or ""}
        ack = push_service.build_native_payload(note, user).get("ack")
        if ack:
            out["ack"] = ack                                     # signed receipt; the app posts it to /api/push/ack
        return out
    if note.type == "announcement":
        return {**base, "body": nsvc.describe(note, lang=lang).get("text") or ""}
    return None


def _message(token: str, data: dict[str, str], *, ttl: int, collapse: str | None, priority: str) -> dict:
    android: dict[str, Any] = {"priority": priority, "ttl": f"{int(ttl)}s"}
    if collapse:
        android["collapse_key"] = collapse[:60]
    return {"message": {"token": token, "data": data, "android": android}}


# ------------------------------------------------------------------ sending
def _send_one(project: str, bearer: str, payload: dict) -> tuple[str, str]:
    """-> (ok | dead | retry | auth, google error status). Runs in a worker thread: no database, no app context."""
    code, body = _post(SEND_URL.format(project=project), headers={"Authorization": f"Bearer {bearer}"},
                       json_body=payload, timeout=float(6))
    if code == 200:
        return "ok", ""
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    st = str(err.get("status") or "")
    if code == 401:
        return "auth", st or "UNAUTHENTICATED"
    if st in DEAD_STATUSES or code == 404:
        # INVALID_ARGUMENT can also mean a malformed message; the token is the only caller-supplied value we send
        return "dead", st or "NOT_FOUND"
    return "retry", st or (f"HTTP_{code}" if code else "NETWORK")


def _deliver(devices: list[NativeDevice], data: dict[str, str], *, ttl: int, collapse: str | None, priority: str) -> int:
    acc, _ = _account()
    if acc is None or not devices:
        return 0
    bearer = access_token(acc)
    if bearer is None:
        return 0
    jobs = [(d.id, _message(d.push_token, data, ttl=ttl, collapse=collapse, priority=priority)) for d in devices if d.push_token]
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, max(1, len(jobs)))) as pool:
        results = list(pool.map(lambda j: (j[0], *_send_one(acc.project_id, bearer, j[1])), jobs))
    retry_auth = [r[0] for r in results if r[1] == "auth"]
    if retry_auth:                                                   # the cached token expired early: refresh once
        bearer = access_token(acc, force=True)
        if bearer:
            again = {i: m for i, m in jobs if i in retry_auth}
            with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(again))) as pool:
                redo = {r[0]: r for r in pool.map(lambda j: (j[0], *_send_one(acc.project_id, bearer, j[1])), again.items())}
            results = [redo.get(r[0], r) for r in results]
    return _record(devices, results)


def _record(devices: list[NativeDevice], results: list[tuple[int, str, str]]) -> int:
    by_id = {d.id: d for d in devices}
    now = utcnow()
    ok = 0
    max_fail = int(current_app.config.get("FCM_MAX_FAILURES", 10))
    for dev_id, outcome, why in results:
        dev = by_id.get(dev_id)
        if dev is None:
            continue
        if outcome == "ok":
            ok += 1
            dev.last_success_at, dev.failure_count = now, 0
        elif outcome == "dead":
            log.info("FCM: device row %s token is no longer valid (%s); invalidated", dev.id, why)
            dev.status, dev.push_token, dev.token_hash = "invalid", None, None      # the app re-registers on next open
        else:
            dev.failure_count = (dev.failure_count or 0) + 1
            log.warning("FCM: send to device row %s failed (%s), failures=%s", dev.id, why, dev.failure_count)
            if dev.failure_count >= max_fail:
                dev.status, dev.push_token, dev.token_hash = "invalid", None, None
    try:
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()
        log.exception("FCM: could not save delivery results")
    return ok


def _available() -> bool:
    return configured() and _switch_on()


def deliver_notification(note: Notification | None, force: bool = False) -> int:
    """Announce one Notice Box notification on the recipient's Android devices. Returns how many accepted it.
    Never raises (a push problem must never fail a message or a call)."""
    try:
        if note is None or note.id is None or not _available():
            return 0
        from . import device_service, push_service

        user = db.session.get(User, note.user_id)
        if user is None or not user.is_active or not push_service._wanted(user, note):   # same preference rules as Web Push
            return 0
        devices = device_service.active_devices(user.id)
        if not devices:
            return 0
        data = build_data(note, user)
        if data is None:
            return 0
        if note.type == "incoming_call":
            return _deliver(devices, data, ttl=CALL_TTL_SECONDS, collapse=f"call:{data['call_id']}", priority="HIGH")
        ttl = int(current_app.config.get("PUSH_TTL_SECONDS", 86400))
        prio = "NORMAL" if str(current_app.config.get("FCM_MESSAGE_PRIORITY", "high")).lower() == "normal" else "HIGH"
        collapse = f"msg:{data.get('conversation_id')}" if note.type == "new_message" else f"{note.type}:{note.id}"
        return _deliver(devices, data, ttl=ttl, collapse=collapse, priority=prio)
    except Exception:  # noqa: BLE001
        db.session.rollback()
        log.exception("FCM delivery failed (notification %s)", getattr(note, "id", None))
        return 0


def deliver_call_event(rec: CallHistory, reason: str) -> int:
    """Tell the callee's phones that a call stopped ringing (caller hung up, timeout, answered/declined elsewhere),
    so a stale incoming-call screen is dismissed. The phone also re-checks the call with the server before accepting."""
    try:
        if rec is None or not _available():
            return 0
        from . import device_service

        devices = device_service.active_devices(rec.receiver_id)
        if not devices:
            return 0
        data = {"type": "call_cancelled", "call_id": rec.call_id, "reason": reason[:24], "sent_at": _s(int(time.time() * 1000))}
        return _deliver(devices, data, ttl=CALL_TTL_SECONDS, collapse=f"call:{rec.call_id}", priority="HIGH")
    except Exception:  # noqa: BLE001
        db.session.rollback()
        log.exception("FCM call event failed (call %s)", getattr(rec, "id", None))
        return 0
