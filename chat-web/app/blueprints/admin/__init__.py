from flask import Blueprint, abort, request
from flask_login import current_user

from ...extensions import login_manager
from ...services import permissions

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
def _admins_only():
    """Runs before EVERY admin route, so a new route can never forget the check."""
    if not current_user.is_authenticated:
        return login_manager.unauthorized()
    if not current_user.is_admin:
        abort(403)
    if not permissions.has(current_user, permissions.required_for(request.endpoint)):
        abort(403)


@bp.app_context_processor
def _admin_helpers():
    return {"can": lambda permission: permissions.has(current_user, permission)}


from . import routes  # noqa: E402,F401
