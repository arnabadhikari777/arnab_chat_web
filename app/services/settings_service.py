"""Website-wide switches. Defaults keep the site fully open, so an empty table changes nothing.

Read once per request (one small SELECT, remembered in the request's environ). Written only by admins,
and every change is written to the audit log with the old and new value.
"""
from flask import current_app, has_request_context, request

from ..extensions import db
from ..models import SiteSetting
from .errors import FieldErrors

BOOLS = {
    "maintenance_mode": False,       # only admins can use the site
    "registration_open": True,
    "login_open": True,              # admins can always sign in
    "chat_enabled": True,
    "message_requests_enabled": True,
    "sending_enabled": True,
    "push_enabled": True,
    "announcements_enabled": True,
}
TEXTS = {            # key: (default, max length)
    "site_name": ("", 40),
    "maintenance_message": ("", 300),
    "public_contact": ("", 120),
}
DEFAULTS = {**BOOLS, **{k: v[0] for k, v in TEXTS.items()}}
_ENV_KEY = "goppo.site_settings"


def _load() -> dict:
    values = dict(DEFAULTS)
    for row in SiteSetting.query.all():
        if row.key in BOOLS:
            values[row.key] = row.value == "1"
        elif row.key in TEXTS:
            values[row.key] = row.value or ""
    return values


def all_settings() -> dict:
    if not has_request_context():
        return _load()
    cached = request.environ.get(_ENV_KEY)
    if cached is None:
        cached = request.environ[_ENV_KEY] = _load()
    return cached


def get(key: str):
    return all_settings()[key]


def enabled(key: str) -> bool:
    return bool(all_settings().get(key, True))


def site_name() -> str:
    try:
        return get("site_name") or current_app.config["APP_NAME"]
    except Exception:  # noqa: BLE001  (an error page must still render if the database is unhappy)
        db.session.rollback()
        return current_app.config["APP_NAME"]


def update(admin, form) -> dict:
    """Save posted values. Only the keys listed above are read, so nothing else in the form can be written.
    Returns {key: {"from": old, "to": new}} for what really changed (also what goes to the audit log)."""
    from .admin_service import audit

    errors, new = {}, {}
    for key in BOOLS:
        new[key] = form.get(key) in ("on", "1", "true")
    for key, (_default, limit) in TEXTS.items():
        text = (form.get(key) or "").strip()
        if len(text) > limit or any(ord(c) < 32 for c in text):
            errors[key] = ("err.setting_text", {"max": limit})
        new[key] = text
    if errors:
        raise FieldErrors(errors)

    old = _load()
    changed = {k: {"from": old[k], "to": v} for k, v in new.items() if old[k] != v}
    if not changed:
        return {}
    for key in changed:
        row = db.session.get(SiteSetting, key)
        if row is None:
            row = SiteSetting(key=key)
            db.session.add(row)
        value = new[key]
        row.value = ("1" if value else "0") if key in BOOLS else value
        row.updated_by = admin.id
    audit(admin, "settings.update", "settings", None, {"changes": changed})
    db.session.commit()
    if has_request_context():
        request.environ.pop(_ENV_KEY, None)
    return changed
