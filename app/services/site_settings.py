"""Website-wide switches that an admin can change without touching code or the hosting account.

Stored in the database (table site_settings), read at most once per request. A missing row means the
default, so a fresh or older database behaves exactly like before. If the table does not exist yet
(migration not run) every switch simply stays at its default.

Safeguards: maintenance mode and "login disabled" never lock out admins.
"""
from flask import current_app, has_request_context, request
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import SiteSetting
from .errors import FieldErrors

SWITCHES = {
    "registration_enabled": True,
    "login_enabled": True,
    "messaging_enabled": True,
    "requests_enabled": True,
    "people_enabled": True,
    "profile_edit_enabled": True,
    "uploads_enabled": True,
    "voice_enabled": True,
    "push_enabled": True,
    "maintenance_mode": False,
    "registration_open": True,
    "login_open": True,
    "chat_enabled": True,
    "message_requests_enabled": True,
    "sending_enabled": True,
    "announcements_enabled": True,
}

_ALIASES = {
    "registration_open": "registration_enabled",
    "login_open": "login_enabled",
    "chat_enabled": "messaging_enabled",
    "message_requests_enabled": "requests_enabled",
    "sending_enabled": "messaging_enabled",
}

TEXTS = {
    "maintenance_message": 500,
    "site_announcement": 300,
    "site_name": 40,
    "public_contact": 120,
}
_CACHE_KEY = "goppo.site_settings"


def _load() -> dict:
    try:
        return {row.key: row.value for row in SiteSetting.query.all()}
    except SQLAlchemyError:
        db.session.rollback()
        return {}


def _values() -> dict:
    if not has_request_context():
        return _load()
    cached = request.environ.get(_CACHE_KEY)
    if cached is None:
        cached = request.environ[_CACHE_KEY] = _load()
    return cached


def get(key: str):
    raw = _values().get(key)
    if raw is None and key in _ALIASES:
        raw = _values().get(_ALIASES[key])
    if key in SWITCHES or key in _ALIASES:
        default = SWITCHES.get(key, SWITCHES.get(_ALIASES.get(key, ""), True))
        return default if raw is None else raw == "1"
    return raw or ""


def enabled(key: str) -> bool:
    return bool(get(key))


def current() -> dict:
    keys = list(dict.fromkeys([*SWITCHES, *TEXTS]))
    return {key: get(key) for key in keys}


def site_name() -> str:
    try:
        return get("site_name") or current_app.config.get("APP_NAME", "Goppo")
    except Exception:  # noqa: BLE001
        db.session.rollback()
        return current_app.config.get("APP_NAME", "Goppo")


def update(admin, form) -> dict:
    """Save the settings form. Returns {key: {"from":..., "to":...}} for what really changed."""
    from .admin_service import audit

    stored = _load()
    changes, rows = {}, {}
    form_has_switch = any(k in form for k in SWITCHES) or any(a in form for a in _ALIASES)

    for key, default in SWITCHES.items():
        if key in _ALIASES:
            continue  # store only under canonical names
        if form_has_switch:
            new = "1" if form.get(key) in ("1", "on", "true") or any(
                form.get(a) in ("1", "on", "true") for a, c in _ALIASES.items() if c == key
            ) else "0"
        else:
            continue
        old = stored.get(key, "1" if default else "0")
        if old not in ("0", "1"):
            old = "1" if default else "0"
        if new != old:
            changes[key] = {"from": old == "1", "to": new == "1"}
            rows[key] = new

    errors = {}
    for key, limit in TEXTS.items():
        if key not in form:
            continue
        new = (form.get(key) or "").strip()
        if len(new) > limit:
            errors[key] = ("err.setting_too_long", {"max": limit})
            continue
        if new != stored.get(key, ""):
            changes[key] = {"from": "text", "to": "text"}
            rows[key] = new
    if errors:
        raise FieldErrors(errors)

    for key, value in rows.items():
        row = db.session.get(SiteSetting, key)
        if row is None:
            db.session.add(SiteSetting(key=key, value=value, updated_by=admin.id))
        else:
            row.value, row.updated_by = value, admin.id
    if changes:
        audit(admin, "settings.update", "settings", None, {"changed": changes})
    db.session.commit()
    if has_request_context():
        request.environ.pop(_CACHE_KEY, None)
    return changes
