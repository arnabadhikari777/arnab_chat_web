"""Applies the website-wide switches to every request. Admins are never blocked."""
from flask import jsonify, make_response, render_template, request
from flask_login import current_user

from ..i18n import t
from ..services import site_settings
from ..utils.errors import wants_json

_MAINT_OK = {
    "main.health", "main.service_worker", "main.set_language", "main.i18n_bundle",
    "chat.push_ack", "static",
}
_CHAT_EXEMPT = {
    "chat.summary", "chat.push_ack", "chat.push_config", "chat.push_vapid_key", "chat.push_subscribe",
    "chat.push_unsubscribe", "chat.push_sync", "chat.notices_mark_displayed", "chat.notices_read_all",
    "chat.notices_clear_all",
}


def _rules(ep: str, method: str) -> list[tuple[str, str]]:
    """(setting, message key) pairs; every setting listed must be ON for the request to pass.

    chat_enabled      = the chat area exists at all (full shutdown). Off blocks reading AND sending.
    messaging_enabled = new messages. Off blocks only sending; history, polling and read receipts keep working.
    """
    out = []
    if ep not in _CHAT_EXEMPT and (ep.startswith("chat.") or ep.startswith("calling.") or ep == "people.message"):
        out.append(("chat_enabled", "site.chat_closed"))
    if ep == "chat.send" and method == "POST":
        out.append(("messaging_enabled", "site.sending_closed"))
    if ep in ("people.send_request", "people.accept"):
        out.append(("requests_enabled", "site.requests_closed"))
    if ep == "notices.announcement":
        out.append(("announcements_enabled", "site.announcements_closed"))
    # auth.register is NOT gated here: the route itself shows the "sign-ups are closed" notice on the sign-in page.
    # Sign-in is NOT gated here: the visitor is anonymous at this point, so an admin could not tell apart from a
    # normal user. auth.login, auth.api_login and auth.sso_login decide per account (admins always get in).
    return out


def _blocked(key: str, custom: str = "", retry: bool = False):
    message = custom or t(key)
    # Maintenance has a stable machine code so apps can recognise it and retry later; other switches use their message key.
    code = "maintenance" if retry else key
    if wants_json():
        res = jsonify({"ok": False, "error": {"code": code, "message": message}})
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
        if current_user.is_authenticated and getattr(current_user, "is_admin", False):
            return None
        if site_settings.enabled("maintenance_mode") and ep not in _MAINT_OK:
            if ep.startswith("auth.") and ep != "auth.register":
                return None
            msg = site_settings.get("maintenance_message") or ""
            return _blocked("site.maintenance", msg, retry=True)
        for setting, key in _rules(ep, request.method):
            if not site_settings.enabled(setting):
                return _blocked(key)
        return None
