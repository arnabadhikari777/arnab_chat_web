from flask import Blueprint, abort
from flask_login import current_user

from ...extensions import login_manager

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
def _admins_only():
    """Runs before EVERY admin route, so a new route can never forget the check."""
    if not current_user.is_authenticated:
        return login_manager.unauthorized()
    if not current_user.is_admin:
        abort(403)


from . import routes  # noqa: E402,F401
