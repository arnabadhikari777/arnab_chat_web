import logging
from logging.handlers import RotatingFileHandler

from ..config import BASE_DIR


def configure_logging(app) -> None:
    """Console + rotating file log. Stack traces go here, never to users."""
    level = getattr(logging, app.config.get("LOG_LEVEL", "INFO"), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    app.logger.setLevel(level)
    if getattr(app, "_logging_ready", False):
        return

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    app.logger.addHandler(console)

    if not app.config.get("TESTING"):
        log_dir = BASE_DIR / "logs"
        log_dir.mkdir(exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "app.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        app.logger.addHandler(file_handler)

    app._logging_ready = True
