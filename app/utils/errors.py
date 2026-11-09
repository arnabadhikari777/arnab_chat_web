"""One place that turns every failure into a friendly page or JSON answer."""
from flask import jsonify, render_template, request
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException

from ..extensions import db
from ..i18n import t

_KNOWN = {400, 401, 403, 404, 405, 413, 429, 500}


def wants_json() -> bool:
    return request.path.startswith("/api/") or (
        request.accept_mimetypes.best == "application/json"
    )


def error_response(code: int, message_key: str | None = None):
    code = code if code in _KNOWN else 500
    title = t(f"error.{code}.title")
    message = t(message_key or f"error.{code}.message")
    if wants_json():
        return jsonify({"ok": False, "error": {"code": code, "message": message}}), code
    return (
        render_template("errors/error.html", code=code, title=title, message=message),
        code,
    )


def register_error_handlers(app) -> None:
    @app.errorhandler(CSRFError)
    def _csrf(_err):
        return error_response(400, "error.csrf")

    @app.errorhandler(HTTPException)
    def _http(err):
        return error_response(err.code or 500)

    @app.errorhandler(Exception)
    def _unexpected(err):
        app.logger.exception("Unhandled error on %s %s", request.method, request.path)
        db.session.rollback()
        return error_response(500)
