from flask import Flask

from .config import BASE_DIR, get_config
from .extensions import csrf, db, limiter, login_manager, migrate
from .i18n import init_i18n
from .utils.errors import register_error_handlers
from .utils.logging_setup import configure_logging
from .utils.security import register_security_headers


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))

    if not app.config.get("SECRET_KEY"):
        raise RuntimeError(
            "SECRET_KEY is missing. Copy .env.example to .env and set SECRET_KEY."
        )

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
        user = db.session.get(models.User, int(user_id))
        if user and user.is_active and user.is_email_verified:
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
    def _presence_heartbeat():
        """Every page and poll shows the person as online (throttled to one write / 10 s)."""
        if request.endpoint and request.endpoint != "static" and current_user.is_authenticated:
            presence_service.touch(current_user)

    return app
