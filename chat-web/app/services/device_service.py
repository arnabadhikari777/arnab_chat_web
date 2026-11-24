"""Native (Android) device registrations.

Kept apart from ``push_service`` (browser Web Push) on purpose: different table, different identifiers,
different failure handling. Phase 1 stores and manages registrations only; sending to FCM is Phase 3.

Security rules enforced here:
* the push token is stored server-side and NEVER returned by any API;
* a device row belongs to exactly one user; signing in as someone else on the same install moves it;
* one token = one row (SHA-256 unique index), one install id = one row;
* the number of devices per user is bounded, oldest are dropped first.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from flask import current_app

from ..extensions import db
from ..models import NativeDevice, User
from ..utils.time import utcnow

PLATFORMS = ("android",)
_DEVICE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9:_\-./=]{20,4096}$")     # FCM tokens: letters, digits, ':', '_', '-'
_LABEL = re.compile(r"^[\w .,+()/-]{0,80}$")


class DeviceError(ValueError):
    """Raised with a short machine code ('bad_device_id', 'bad_token', ...)."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _clean(value: Any, limit: int, code: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > limit or not _LABEL.match(value):
        raise DeviceError(code)
    return value.strip() or None


def _validate(data: dict, need_device_id: bool) -> dict:
    out: dict[str, Any] = {}
    if need_device_id or "device_id" in data:
        did = data.get("device_id")
        if not isinstance(did, str) or not _DEVICE_ID.match(did):
            raise DeviceError("bad_device_id")
        out["device_id"] = did
    platform = data.get("platform", "android")
    if platform not in PLATFORMS:
        raise DeviceError("bad_platform")
    out["platform"] = platform
    if "push_token" in data and data["push_token"] is not None:
        tok = data["push_token"]
        if not isinstance(tok, str) or not _TOKEN.match(tok):
            raise DeviceError("bad_token")
        out["push_token"] = tok
    out["app_version"] = _clean(data.get("app_version"), 32, "bad_app_version")
    out["os_version"] = _clean(data.get("os_version"), 32, "bad_os_version")
    out["device_model"] = _clean(data.get("device_model"), 80, "bad_device_model")
    return out


def enabled() -> bool:
    return bool(current_app.config.get("NATIVE_DEVICES_ENABLED", True))


def public_config() -> dict:
    return {"enabled": enabled(), "platforms": list(PLATFORMS),
            "push_delivery": bool(current_app.config.get("NATIVE_PUSH_DELIVERY", False)),
            "max_devices": int(current_app.config.get("NATIVE_MAX_DEVICES_PER_USER", 5))}


def public_dict(dev: NativeDevice) -> dict:
    """Safe to return to the owner. The token and its hash are never included."""
    return {"device_id": dev.device_id, "platform": dev.platform, "status": dev.status,
            "has_push_token": bool(dev.push_token), "app_version": dev.app_version,
            "os_version": dev.os_version, "device_model": dev.device_model,
            "last_seen_at": dev.last_seen_at.isoformat() + "Z" if dev.last_seen_at else None,
            "created_at": dev.created_at.isoformat() + "Z" if dev.created_at else None}


def _free_token(token_hash: str, keep_id: int | None) -> None:
    """A token can belong to one row only. If another row holds it (the same phone, an older install id
    or another account), that row loses the token instead of two rows both claiming it."""
    q = NativeDevice.query.filter(NativeDevice.token_hash == token_hash)
    if keep_id is not None:
        q = q.filter(NativeDevice.id != keep_id)
    for other in q.all():
        other.push_token = None
        other.token_hash = None
        other.status = "invalid"
    db.session.flush()                        # release the unique value before the new owner takes it


def _prune(user_id: int, keep_id: int) -> None:
    limit = max(1, int(current_app.config.get("NATIVE_MAX_DEVICES_PER_USER", 5)))
    rows = (NativeDevice.query.filter_by(user_id=user_id)
            .order_by(NativeDevice.updated_at.desc(), NativeDevice.id.desc()).all())
    for old in rows[limit:]:
        if old.id != keep_id:
            db.session.delete(old)


def register(user: User, data: dict) -> tuple[NativeDevice, bool]:
    """Create or refresh the registration of this install. Returns (device, created)."""
    v = _validate(data, need_device_id=True)
    now = utcnow()
    dev = NativeDevice.query.filter_by(platform=v["platform"], device_id=v["device_id"]).first()
    created = dev is None
    if created:
        dev = NativeDevice(user_id=user.id, platform=v["platform"], device_id=v["device_id"], status="active")
        db.session.add(dev)
    else:
        dev.user_id = user.id                 # the install moves with whoever signs in on it
        dev.status = "active"
        dev.failure_count = 0
    _apply(dev, v, now)
    db.session.flush()
    _prune(user.id, keep_id=dev.id)
    db.session.commit()
    return dev, created


def _apply(dev: NativeDevice, v: dict, now) -> None:
    if "push_token" in v:
        h = _hash(v["push_token"])
        if dev.id is not None or NativeDevice.query.filter_by(token_hash=h).first() is not None:
            _free_token(h, dev.id)
        dev.push_token, dev.token_hash = v["push_token"], h
    for name in ("app_version", "os_version", "device_model"):
        if v.get(name) is not None:
            setattr(dev, name, v[name])
    dev.last_seen_at = now


def update(user: User, device_id: str, data: dict) -> NativeDevice:
    """Token rotation or new app/OS version of a device this user already registered."""
    if not isinstance(device_id, str) or not _DEVICE_ID.match(device_id):
        raise DeviceError("bad_device_id")
    dev = NativeDevice.query.filter_by(user_id=user.id, platform="android", device_id=device_id).first()
    if dev is None:
        raise DeviceError("not_found", 404)
    data = {k: v for k, v in data.items() if k not in ("device_id", "platform")}
    v = _validate(data, need_device_id=False)
    _apply(dev, v, utcnow())
    if dev.status == "invalid" and v.get("push_token"):
        dev.status, dev.failure_count = "active", 0
    db.session.commit()
    return dev


def unregister(user: User, device_id: str) -> int:
    if not isinstance(device_id, str) or not _DEVICE_ID.match(device_id):
        raise DeviceError("bad_device_id")
    n = NativeDevice.query.filter_by(user_id=user.id, device_id=device_id).delete(synchronize_session=False)
    db.session.commit()
    return n


def forget_session_device(user: User, device_id) -> None:
    """On logout: this install must stop receiving the account's notifications."""
    if isinstance(device_id, str):
        NativeDevice.query.filter_by(user_id=user.id, device_id=device_id).delete(synchronize_session=False)
        db.session.commit()


def list_for_user(user_id: int) -> list[NativeDevice]:
    return (NativeDevice.query.filter_by(user_id=user_id)
            .order_by(NativeDevice.updated_at.desc(), NativeDevice.id.desc()).all())


def active_devices(user_id: int) -> list[NativeDevice]:
    """Devices that could receive a native push (used by the Phase 3 sender)."""
    return [d for d in list_for_user(user_id) if d.status == "active" and d.push_token]
