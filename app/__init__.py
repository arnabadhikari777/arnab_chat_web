from flask import Flask

from .config import BASE_DIR, get_config
from .extensions import csrf, db, limiter, login_manager, migrate
from .i18n import init_i18n
from .utils.errors import register_error_handlers
from .utils.logging_setup import configure_logging
from .utils.security import register_security_headers


# Reachable even in maintenance mode: assets, health check, translations, the service worker and the
# delivery receipt, plus login/logout (so an admin can always sign in).
_MAINTENANCE_OPEN = {"static", "main.health", "main.i18n_bundle", "main.service_worker", "chat.push_ack",
                     "auth.login", "auth.logout"}
_NO_PRESENCE = {"static", "main.health", "main.i18n_bundle", "main.service_worker", "chat.push_ack"}


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))

    if not app.config.get("SECRET_KEY"):
        raise RuntimeError(
            "SECRET_KEY is missing. Copy .env.example to .env and set SECRET_KEY."
        )

    proxies = int(app.config.get("TRUSTED_PROXY_COUNT") or 0)
    if proxies:
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies)

    (BASE_DIR / "instance").mkdir(exist_ok=True)
    configure_logging(app)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)

    from . import models  # noqa: F401  (registers tables with SQLAlchemy)

    @login_manager.user_loader
    def _load_user(user_id: str):
        # A suspended/banned or unverified account is signed out on its next request.
        # The id looks like "12.<fingerprint>": a password change invalidates older sessions.
        import hmac

        raw_id, _, fingerprint = str(user_id).partition(".")
        if not raw_id.isdigit() or not fingerprint:
            return None
        user = db.session.get(models.User, int(raw_id))
        if (user and user.is_active and user.is_email_verified
                and hmac.compare_digest(user.session_fingerprint(), fingerprint)):
            return user
        return None

    from .cli import register_cli
    from .middleware.guards import register_guards

    register_guards(app)
    register_cli(app)
    init_i18n(app)
    register_error_handlers(app)
    register_security_headers(app)

    from .blueprints.main import bp as main_bp

    from .blueprints.auth import bp as auth_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)

    from .blueprints.chat import bp as chat_bp
    from .blueprints.people import bp as people_bp
    from .blueprints.settings import bp as settings_bp

    app.register_blueprint(people_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(settings_bp)

    from .blueprints.admin import bp as admin_bp
    from .blueprints.notices import bp as notices_bp

    app.register_blueprint(notices_bp)
    app.register_blueprint(admin_bp)

    from flask import request
    from flask_login import current_user

    from .services import presence_service

    @app.before_request
    def _site_gate():
        """Ends a finished timed suspension, and enforces maintenance mode (admins are never locked out)."""
        from flask import jsonify, render_template

        from .i18n import t
        from .services import site_settings

        if current_user.is_authenticated and current_user.lift_expired_suspension():
            db.session.commit()
        endpoint = request.endpoint
        if endpoint is None or endpoint in _MAINTENANCE_OPEN or not site_settings.enabled("maintenance_mode"):
            return None
        if current_user.is_authenticated and current_user.is_admin:
            return None
        message = site_settings.get("maintenance_message") or t("site.maintenance_default")
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": {"code": "maintenance", "message": message}}), 503
        return render_template("errors/error.html", title=t("site.maintenance_title"), message=message), 503

    @app.context_processor
    def _site_banner():
        from .services import site_settings
        return {"site_banner": site_settings.get("site_announcement")}

    @app.before_request
    def _presence_heartbeat():
        """Pages and polls show the person as online (the database is written at most every 30 s)."""
        if (request.endpoint and request.endpoint not in _NO_PRESENCE
                and current_user.is_authenticated):
            presence_service.touch(current_user)

    return app
