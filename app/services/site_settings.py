"""Website-wide switches that an admin can change without touching code or the hosting account.

Stored in the database (table site_settings), read at most once per request. A missing row means the
default, so a fresh or older database behaves exactly like before. If the table does not exist yet
(migration not run) every switch simply stays at its default.

Safeguards: maintenance mode and "login disabled" never lock out admins.
"""
from flask import has_request_context, request
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import SiteSetting
from .errors import FieldErrors

SWITCHES = {                      # key: default
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
}
TEXTS = {"maintenance_message": 500, "site_announcement": 300}   # key: max length
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
    if _CACHE_KEY not in request.environ:
        request.environ[_CACHE_KEY] = _load()
    return request.environ[_CACHE_KEY]


def get(key: str):
    raw = _values().get(key)
    if key in SWITCHES:
        return SWITCHES[key] if raw is None else raw == "1"
    return raw or ""


def enabled(key: str) -> bool:
    return bool(get(key))


def current() -> dict:
    return {key: get(key) for key in (*SWITCHES, *TEXTS)}


def update(admin, form) -> dict:
    """Save the settings form. Returns {key: {"from":..., "to":...}} for what really changed."""
    from .admin_service import audit

    stored = _load()
    changes, rows = {}, {}
    for key, default in SWITCHES.items():
        new = "1" if form.get(key) == "1" else "0"
        old = stored.get(key, "1" if default else "0")
        if new != old:
            changes[key] = {"from": old == "1", "to": new == "1"}
            rows[key] = new
    errors = {}
    for key, limit in TEXTS.items():
        new = (form.get(key) or "").strip()
        if len(new) > limit:
            errors[key] = ("err.setting_too_long", {"max": limit})
            continue
        if new != stored.get(key, ""):
            changes[key] = {"from": "text", "to": "text"}      # the text itself is not copied into the log
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
