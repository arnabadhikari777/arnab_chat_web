"""Shared extension objects. Created here, attached to the app in create_app()."""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()
migrate = Migrate(render_as_batch=True)  # batch mode = safe ALTER TABLE on SQLite
login_manager = LoginManager()
csrf = CSRFProtect()


def _rate_key() -> str:
    """Signed-in people are limited per account, everybody else per IP address."""
    try:
        from flask_login import current_user

        if current_user.is_authenticated:
            return f"u:{current_user.id}"
    except Exception:  # noqa: BLE001 - outside a request
        pass
    return get_remote_address()


limiter = Limiter(key_func=_rate_key)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _record):
    """SQLite ignores foreign keys unless this is switched on per connection."""
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")   # wait for a write lock instead of failing at once
        cursor.close()
