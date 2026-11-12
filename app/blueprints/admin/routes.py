from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...models import Report, User
from ...services import admin_service as svc
from ...services import site_settings
from ...services.errors import FieldErrors, ServiceError
from . import bp


def _page() -> int:
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1


def _user_or_404(user_id: int) -> User:
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    return user


@bp.get("")
def dashboard():
    return render_template("admin/dashboard.html", s=svc.stats(), events=svc.recent_events(), section="dashboard")


# ------------------------------------------------------------------ users
@bp.get("/users")
def users():
    q, status = request.args.get("q", ""), request.args.get("status", "")
    privacy = request.args.get("privacy", "")
    rows, has_next = svc.list_users(q, status, _page(), privacy)
    return render_template("admin/users.html", users=rows, has_next=has_next, page=_page(),
                           q=q, status=status, privacy=privacy, section="users")


@bp.get("/users/<int:user_id>")
def user(user_id):
    target = _user_or_404(user_id)
    return render_template("admin/user.html", target=target, d=svc.user_detail(target), section="users",
                           notes=svc.notes_for(target), history=svc.history_for(target))


@bp.post("/users/<int:user_id>/status")
def user_status(user_id):
    target = _user_or_404(user_id)
    try:
        svc.set_status(current_user, target, request.form.get("status", ""), request.form.get("days"))
        flash("flash.admin_saved", "success")
    except ServiceError as exc:
        flash([exc.key, exc.values], "error")
    return redirect(url_for("admin.user", user_id=target.id))


@bp.post("/users/<int:user_id>/notes")
def user_note(user_id):
    target = _user_or_404(user_id)
    try:
        svc.add_note(current_user, target, request.form.get("note", ""))
        flash("flash.admin_saved", "success")
    except ServiceError as exc:
        flash([exc.key, exc.values], "error")
    return redirect(url_for("admin.user", user_id=target.id))


@bp.post("/users/<int:user_id>/force-logout")
def user_force_logout(user_id):
    target = _user_or_404(user_id)
    try:
        svc.force_logout(current_user, target)
        flash("flash.admin_forced_logout", "success")
    except ServiceError as exc:
        flash([exc.key, exc.values], "error")
    return redirect(url_for("admin.user", user_id=target.id))


# ------------------------------------------------------------------ staff levels
@bp.route("/staff", methods=["GET", "POST"])
def staff():
    if request.method == "POST":
        try:
            svc.set_staff_level(current_user, request.form.get("username", ""), request.form.get("level", ""))
            flash("flash.admin_saved", "success")
        except ServiceError as exc:
            flash([exc.key, exc.values], "error")
        return redirect(url_for("admin.staff"))
    return render_template("admin/staff.html", people=svc.list_staff(), section="staff")


# ------------------------------------------------------------------ website settings
@bp.route("/settings", methods=["GET", "POST"])
def settings():
    errors = {}
    if request.method == "POST":
        try:
            site_settings.update(current_user, request.form)
            flash("flash.admin_saved", "success")
            return redirect(url_for("admin.settings"))
        except FieldErrors as exc:
            errors = exc.errors
    return render_template("admin/settings.html", cfg=site_settings.current(), errors=errors,
                           switches=list(site_settings.SWITCHES), form=request.form, section="settings")

# ------------------------------------------------------------------ announcements
@bp.route("/announcements", methods=["GET", "POST"])
def announcements():
    from ...models import Announcement

    errors, form = {}, request.form
    if request.method == "POST":
        try:
            svc.create_announcement(current_user, request.form)
            flash("flash.announcement_sent", "success")
            return redirect(url_for("admin.announcements"))
        except FieldErrors as exc:
            errors = exc.errors
    items = Announcement.query.order_by(Announcement.id.desc()).limit(50).all()
    return render_template("admin/announcements.html", items=items, errors=errors, form=form,
                           section="announcements")


@bp.route("/announcements/<int:announcement_id>/edit", methods=["GET", "POST"])
def announcement_edit(announcement_id):
    from ...models import Announcement

    ann = db.session.get(Announcement, announcement_id)
    if ann is None:
        abort(404)
    errors, form = {}, request.form if request.method == "POST" else {
        "title": ann.title, "body": ann.body, "title_bn": ann.title_bn or "", "body_bn": ann.body_bn or ""}
    if request.method == "POST":
        try:
            svc.update_announcement(current_user, announcement_id, request.form)
            flash("flash.admin_saved", "success")
            return redirect(url_for("admin.announcements"))
        except FieldErrors as exc:
            errors = exc.errors
    return render_template("admin/announcement_edit.html", ann=ann, errors=errors, form=form, section="announcements")


@bp.post("/announcements/<int:announcement_id>/delete")
def announcement_delete(announcement_id):
    try:
        svc.delete_announcement(current_user, announcement_id)
        flash("flash.admin_saved", "success")
    except ServiceError:
        abort(404)
    return redirect(url_for("admin.announcements"))


@bp.post("/announcements/<int:announcement_id>/toggle")
def announcement_toggle(announcement_id):
    try:
        svc.toggle_announcement(current_user, announcement_id)
    except ServiceError:
        abort(404)
    return redirect(url_for("admin.announcements"))


# ------------------------------------------------------------------ reports
@bp.get("/reports")
def reports():
    status = request.args.get("status", "")
    rows, has_next = svc.list_reports(status, _page())
    users = {u.id: u for u in User.query.filter(User.id.in_(
        [r.reporter_id for r in rows] + [r.reported_user_id for r in rows] or [0])).all()}
    return render_template("admin/reports.html", reports=rows, users=users, has_next=has_next,
                           page=_page(), status=status, section="reports")


@bp.get("/reports/<int:report_id>")
def report(report_id):
    rep = db.session.get(Report, report_id)
    if rep is None:
        abort(404)
    return render_template("admin/report.html", r=rep, reporter=db.session.get(User, rep.reporter_id),
                           reported=db.session.get(User, rep.reported_user_id), section="reports")


@bp.post("/reports/<int:report_id>/status")
def report_status(report_id):
    try:
        svc.set_report_status(current_user, report_id, request.form.get("status", ""))
        flash("flash.admin_saved", "success")
    except ServiceError as exc:
        if exc.key == "admin.not_found":
            abort(404)
        flash([exc.key, exc.values], "error")
    return redirect(url_for("admin.report", report_id=report_id))


# ------------------------------------------------------------------ audit log
@bp.get("/audit")
def audit():
    rows, has_next = svc.list_audit(_page())
    admins = {u.id: u for u in User.query.filter(User.id.in_([r.admin_id for r in rows if r.admin_id] or [0])).all()}
    return render_template("admin/audit.html", rows=rows, admins=admins, has_next=has_next,
                           page=_page(), section="audit")
