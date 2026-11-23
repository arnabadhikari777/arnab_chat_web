from flask import abort, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...middleware.guards import auth_required, safe_next
from ...models import Announcement
from ...services import notification_service as svc
from . import bp


@bp.get("/notices")
@auth_required
def index():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    rows, has_next = svc.list_notices(current_user.id, page)
    items = [{"note": n, **svc.describe(n)} for n in rows]
    return render_template("notices/index.html", items=items, page=page, has_next=has_next,
                           unread=svc.unread_count(current_user.id))


@bp.post("/notices/read-all")
@auth_required
def read_all():
    svc.mark_all_read(current_user.id)
    return redirect(url_for("notices.index"))


@bp.get("/notices/<int:notification_id>/go")
@auth_required
def go(notification_id):
    """Open a notice: mark it read, then jump to the page it is about."""
    note = svc.mark_read(current_user.id, notification_id)
    if note is None:
        abort(404)
    return redirect(safe_next(note.link, url_for("notices.index")))


@bp.get("/announcements/<int:announcement_id>")
@auth_required
def announcement(announcement_id):
    ann = db.session.get(Announcement, announcement_id)
    if ann is None:
        abort(404)
    return render_template("notices/announcement.html", ann=ann)
