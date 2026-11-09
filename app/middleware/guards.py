"""Backend access checks. Never rely on hiding buttons in the page."""
from functools import wraps
from urllib.parse import urlparse

from flask import abort, jsonify, redirect, request, url_for

from ..extensions import login_manager
from ..i18n import t


def safe_next(target: str | None, fallback: str = "/") -> str:
    """Only allow redirects to a path on this same site (blocks open redirects)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return fallback
    parsed = urlparse(target)
    return target if not parsed.scheme and not parsed.netloc else fallback


def _wants_json() -> bool:
    return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"


def register_guards(app) -> None:
    login_manager.session_protection = "strong"
    login_manager.login_view = "auth.login"

    @login_manager.unauthorized_handler
    def _unauthorized():
        if _wants_json():
            return jsonify({"ok": False, "error": {"code": 401, "message": t("error.401.message")}}), 401
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))


def auth_required(view):
    """Logged in + verified + active. (The user loader already drops anyone else.)"""

    @wraps(view)
    def wrapper(*args, **kwargs):
        from flask_login import current_user

        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    @wraps(view)
    @auth_required
    def wrapper(*args, **kwargs):
        from flask_login import current_user

        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapper
