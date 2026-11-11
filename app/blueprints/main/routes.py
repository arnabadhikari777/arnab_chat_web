from flask import abort, current_app, jsonify, make_response, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user
from sqlalchemy import text

from ...extensions import db
from ...i18n import bundle, get_language
from ...middleware.guards import safe_next
from . import bp


@bp.get("/")
def home():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    return render_template("dashboard.html")


@bp.get("/lang/<code>")
def set_language(code):
    """Switch between English and Bengali. Remembered in a cookie (and on the account)."""
    if code not in current_app.config["SUPPORTED_LANGUAGES"]:
        abort(404)
    response = redirect(safe_next(request.args.get("next"), url_for("main.home")))
    response.set_cookie(
        "lang", code, max_age=365 * 24 * 3600, samesite="Lax",
        secure=current_app.config["SESSION_COOKIE_SECURE"], httponly=True,
    )
    if current_user.is_authenticated:
        current_user.language = code
        db.session.commit()
    return response


@bp.get("/health")
def health():
    """Used to check the app and database are alive."""
    try:
        db.session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Health check: database failed")
        db_ok = False
    return jsonify({"ok": db_ok, "database": db_ok}), (200 if db_ok else 503)


@bp.get("/i18n/bundle.json")
def i18n_bundle():
    return jsonify({"lang": get_language(), "messages": bundle()})


@bp.get("/design")
def design_system():
    """Living style guide. Only available while developing."""
    if current_app.config.get("APP_ENV") == "production":
        abort(404)
    return render_template("design.html")


@bp.get("/sw.js")
def service_worker():
    """The service worker is served from the site root so it can control every page (scope "/")."""
    response = make_response(send_from_directory(
        current_app.static_folder + "/js", "sw.js", mimetype="application/javascript"))
    response.headers["Cache-Control"] = "no-cache"          # always re-check for a new version
    response.headers["Service-Worker-Allowed"] = "/"
    return response
