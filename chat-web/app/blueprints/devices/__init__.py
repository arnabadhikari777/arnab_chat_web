from flask import Blueprint

bp = Blueprint("devices", __name__, url_prefix="/api/devices")

from . import routes  # noqa: E402, F401
