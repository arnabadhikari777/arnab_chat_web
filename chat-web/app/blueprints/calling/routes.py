"""Calling API. CSRF is enforced (the browser sends X-CSRFToken); every endpoint needs a logged-in, verified user."""
from flask import current_app, jsonify, request
from flask_login import current_user

from ...extensions import limiter
from ...middleware.guards import auth_required
from ...services import calling_service
from ...services.calling_service import CallingError
from . import bp


def _err(e: CallingError):
    body = {"ok": False, "error": str(e)}
    body.update(e.extra)
    return jsonify(body), e.status


def _json():
    return request.get_json(silent=True) or {}


@bp.post("/initiate")
@auth_required
@limiter.limit("10 per minute")
def initiate():
    data = _json()
    try:
        peer_id = int(data.get("peer_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "peer_id required"}), 400
    try:
        return jsonify({"ok": True, **calling_service.initiate_call(current_user, peer_id, str(data.get("call_type") or "voice").lower())})
    except CallingError as e:
        return _err(e)
    except Exception:  # noqa: BLE001
        current_app.logger.exception("initiate call failed")
        return jsonify({"ok": False, "error": "server error"}), 500


@bp.post("/token")
@auth_required
@limiter.limit("30 per minute")
def issue_token():
    """New short-lived token for a live call (cold start / reconnect). Role is decided by the server."""
    call_id = str(_json().get("call_id") or "")
    if not call_id:
        return jsonify({"ok": False, "error": "call_id required"}), 400
    try:
        return jsonify({"ok": True, **calling_service.issue_token(current_user, call_id)})
    except CallingError as e:
        return _err(e)


@bp.post("/accept")
@auth_required
@limiter.limit("20 per minute")
def accept():
    call_id = str(_json().get("call_id") or "")
    if not call_id:
        return jsonify({"ok": False, "error": "call_id required"}), 400
    try:
        return jsonify({"ok": True, **calling_service.accept_call(current_user, call_id)})
    except CallingError as e:
        return _err(e)


@bp.post("/status")
@auth_required
@limiter.limit("60 per minute")
def update_status():
    data = _json()
    call_id, status = str(data.get("call_id") or ""), str(data.get("status") or "")
    if not call_id or not status:
        return jsonify({"ok": False, "error": "call_id and status required"}), 400
    try:
        record = calling_service.update_call_status(current_user, call_id, status)
        return jsonify({"ok": True, "call": record.to_dict()})
    except CallingError as e:
        return _err(e)


@bp.get("/incoming")
@auth_required
@limiter.limit("60 per minute")
def incoming():
    return jsonify({"ok": True, "call": calling_service.incoming_for(current_user)})


@bp.get("/state/<call_id>")
@auth_required
@limiter.limit("90 per minute")
def state(call_id):
    try:
        return jsonify({"ok": True, "call": calling_service.call_state(current_user, call_id)})
    except CallingError as e:
        return _err(e)


@bp.get("/history")
@auth_required
@limiter.limit("30 per minute")
def history():
    return jsonify({"ok": True, "calls": calling_service.list_calls_for_user(current_user, request.args.get("limit", 50, type=int))})


@bp.get("/config")
@auth_required
def calling_config():
    cfg = current_app.config
    return jsonify({"ok": True, "enabled": bool(cfg.get("CALLING_SERVER_URL") and cfg.get("CALLING_HMAC_SECRET")),
                    "calling_server_url": cfg.get("CALLING_SERVER_URL", ""), "health_path": "/health", "ready_path": "/ready",
                    "wake_timeout_ms": int(cfg.get("CALLING_WAKE_TIMEOUT_MS", 90000)),
                    "health_poll_ms": int(cfg.get("CALLING_HEALTH_POLL_MS", 2000)),
                    "ring_seconds": int(cfg.get("CALLING_RING_SECONDS", 60))})
