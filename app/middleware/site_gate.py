"""Applies the website-wide switches (maintenance, registration, login, chat, requests, sending) to every request.

Admins are never blocked by these switches, so an admin can always sign in and switch things back on.
Sign-in, password reset, logout, the health check, static files and the service worker stay reachable
during maintenance (that is how an admin gets in).
"""
from flask import jsonify, make_response, render_template, request
from flask_login import current_user

from ..i18n import t
from ..services import settings_service
from ..utils.errors import wants_json

_MAINT_OK = {"main.health", "main.service_worker", "main.set_language", "main.i18n_bundle", "chat.push_ack"}
# Chat endpoints that are not "chatting": badge counters, push set-up and the Notice Box keep working.
_CHAT_EXEMPT = {
    "chat.summary", "chat.push_ack", "chat.push_config", "chat.push_vapid_key", "chat.push_subscribe",
    "chat.push_unsubscribe", "chat.push_sync", "chat.notices_mark_displayed", "chat.notices_read_all",
    "chat.notices_clear_all",
}


def _rules(ep: str, method: str) -> list[tuple[str, str]]:
    out = []
    if ep not in _CHAT_EXEMPT and (ep.startswith("chat.") or ep == "people.message"):
        out.append(("chat_enabled", "site.chat_closed"))
    if ep == "chat.send" and method == "POST":
        out.append(("sending_enabled", "site.sending_closed"))
    if ep in ("people.send_request", "people.accept"):
        out.append(("message_requests_enabled", "site.requests_closed"))
    if ep == "notices.announcement":
        out.append(("announcements_enabled", "site.announcements_closed"))
    if ep == "auth.register":
        out.append(("registration_open", "site.registration_closed"))
    if ep == "auth.sso_login":
        out.append(("login_open", "site.login_closed"))
    return out


def _blocked(key: str, custom: str = "", retry: bool = False):
    message = custom or t(key)
    if wants_json():
        res = jsonify({"ok": False, "error": {"code": key, "message": message}})
    else:
        res = make_response(render_template(
            "errors/unavailable.html",
            title=t("site.maintenance.title" if retry else "site.unavailable.title"), message=message))
    res.status_code = 503
    res.headers["Cache-Control"] = "no-store"
    if retry:
        res.headers["Retry-After"] = "300"
    return res


def register_site_gate(app) -> None:
    @app.before_request
    def _site_gate():
        ep = request.endpoint
        if not ep or ep == "static":
            return None
        if current_user.is_authenticated and current_user.is_admin:
            return None
        s = settings_service.all_settings()
        if (s["maintenance_mode"] and ep not in _MAINT_OK
                and not (ep.startswith("auth.") and ep != "auth.register")):
            return _blocked("site.maintenance", s["maintenance_message"], retry=True)
        for setting, key in _rules(ep, request.method):
            if not s[setting]:
                return _blocked(key)
        return None
