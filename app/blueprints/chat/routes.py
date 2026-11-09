from flask import abort, current_app, render_template
from flask_login import current_user

from ...extensions import db
from ...middleware.guards import auth_required
from ...models import User
from ...services import conversation_service
from . import bp


@bp.get("/messages")
@auth_required
def inbox():
    """The chat list. The list itself is drawn by chatlist.js from /api/chats."""
    return render_template("chat/inbox.html")


@bp.get("/chat/<int:conversation_id>")
@auth_required
def room(conversation_id):
    """Only the two people in a conversation can open it. Anyone else gets a plain 404."""
    conv = conversation_service.get_for_member(current_user.id, conversation_id)
    if conv is None:
        abort(404)
    other = db.session.get(User, conv.other_user_id(current_user.id))
    if other is None or other.status != "active":
        abort(404)
    cfg = current_app.config
    return render_template(
        "chat/room.html", conversation=conv, other=other,
        max_chars=cfg["MESSAGE_MAX_CHARS"], max_voice=cfg["MAX_VOICE_SECONDS"],
        max_bytes={k: v for k, v in cfg["UPLOAD_MAX_BYTES"].items()},
    )
