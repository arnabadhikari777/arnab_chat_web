"""Android device registration API (Phase 1: store only).

Session-cookie authenticated and CSRF-protected exactly like every other API: the Android WebView loads the
live website, so it already has the session cookie and the ``csrf-token`` meta tag. No token login exists.
The push token is accepted here but never returned.
"""
from flask import current_app, jsonify, request, session
from flask_login import current_user

from ...extensions import db, limiter
from ...middleware.guards import auth_required
from ...services import device_service
from ...services.device_service import DeviceError
from . import bp


def _err(code: str, status: int = 400):
    return jsonify({"ok": False, "error": {"code": code, "message": code.replace("_", " ")}}), status


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _closed():
    return None if device_service.enabled() else _err("devices_disabled", 503)


@bp.get("/config")
@auth_required
def config():
    """What the app needs to know before registering. Contains no secret."""
    return jsonify({"ok": True, **device_service.public_config()})


@bp.get("")
@auth_required
def list_devices():
    return jsonify({"ok": True, "devices": [device_service.public_dict(d) for d in device_service.list_for_user(current_user.id)]})


@bp.post("")
@auth_required
@limiter.limit("20 per minute")
def register():
    if (blocked := _closed()) is not None:
        return blocked
    try:
        dev, created = device_service.register(current_user, _body())
    except DeviceError as e:
        return _err(e.code, e.status)
    except Exception:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception("device register failed")
        return _err("server_error", 500)
    session["native_device_id"] = dev.device_id        # lets logout remove exactly this install
    return jsonify({"ok": True, "created": created, "device": device_service.public_dict(dev)}), 201 if created else 200


@bp.patch("/<device_id>")
@auth_required
@limiter.limit("30 per minute")
def update(device_id):
    if (blocked := _closed()) is not None:
        return blocked
    try:
        dev = device_service.update(current_user, device_id, _body())
    except DeviceError as e:
        return _err(e.code, e.status)
    except Exception:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception("device update failed")
        return _err("server_error", 500)
    return jsonify({"ok": True, "device": device_service.public_dict(dev)})


@bp.delete("/<device_id>")
@auth_required
@limiter.limit("20 per minute")
def unregister(device_id):
    try:
        removed = device_service.unregister(current_user, device_id)
    except DeviceError as e:
        return _err(e.code, e.status)
    if session.get("native_device_id") == device_id:
        session.pop("native_device_id", None)
    return jsonify({"ok": True, "removed": removed})
