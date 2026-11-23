from flask import Blueprint

bp = Blueprint("chat", __name__)

from . import api, routes  # noqa: E402,F401
