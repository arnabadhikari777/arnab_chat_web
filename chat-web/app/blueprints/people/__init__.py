from flask import Blueprint

bp = Blueprint("people", __name__)

from . import routes  # noqa: E402,F401
