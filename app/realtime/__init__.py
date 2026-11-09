from flask import current_app

from .polling import PollingBackend

_backend = PollingBackend()


def get_backend():
    """Return the active real-time backend (only polling exists for now)."""
    mode = current_app.config.get("REALTIME_MODE", "polling")
    if mode != "polling" and not getattr(current_app, "_warned_realtime", False):
        current_app.logger.warning("REALTIME_MODE=%s is not available yet; using polling.", mode)
        current_app._warned_realtime = True
    return _backend
