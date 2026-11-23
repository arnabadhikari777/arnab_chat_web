from flask import Blueprint

bp = Blueprint("calling", __name__, url_prefix="/api/calling")

from . import routes  # noqa: E402, F401
