from flask import abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user

from ...extensions import db
from ...models import Report, User
from ...extensions import limiter
from ...services import admin_service as svc
from ...services import db_admin_service as dbs
from ...services import security_service
from ...services import settings_service
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
    return render_template("admin/dashboard.html", s=svc.stats(), ps=svc.push_stats(), site=settings_service.all_settings(),
                           section="dashboard")


# ------------------------------------------------------------------ website settings
@bp.route("/settings", methods=["GET", "POST"])
@limiter.limit("30 per hour", methods=["POST"])
def settings():
    errors = {}
    if request.method == "POST":
        try:
            changed = settings_service.update(current_user, request.form)
            flash("flash.admin_saved" if changed else "flash.admin_nothing_changed", "success")
            return redirect(url_for("admin.settings"))
        except FieldErrors as exc:
            errors = exc.errors
    return render_template("admin/settings.html", v=settings_service.all_settings(), errors=errors,
                           form=request.form if errors else None, section="settings")


# ------------------------------------------------------------------ users
@bp.get("/users")
def users():
    q, status = request.args.get("q", ""), request.args.get("status", "")
    rows, has_next = svc.list_users(q, status, _page())
    return render_template("admin/users.html", users=rows, has_next=has_next, page=_page(),
                           q=q, status=status, section="users")


@bp.get("/users/<int:user_id>")
def user(user_id):
    target = _user_or_404(user_id)
    return render_template("admin/user.html", target=target, d=svc.user_detail(target),
                           devices=svc.user_devices(target), section="users")


@bp.post("/users/<int:user_id>/status")
def user_status(user_id):
    target = _user_or_404(user_id)
    try:
        raw = (request.form.get("days") or "").strip()
        days = int(raw) if raw.isdigit() else (None if not raw else 0)     # 0 is refused below (not 1-3650)
        svc.set_status(current_user, target, request.form.get("status", ""), days)
        flash("flash.admin_saved", "success")
    except ServiceError as exc:
        flash([exc.key, exc.values], "error")
    return redirect(url_for("admin.user", user_id=target.id))


@bp.post("/users/<int:user_id>/push/<int:sub_id>/revoke")
@limiter.limit("60 per hour")
def user_push_revoke(user_id, sub_id):
    target = _user_or_404(user_id)
    try:
        svc.revoke_push(current_user, target, sub_id)
        flash("flash.admin_saved", "success")
    except ServiceError as exc:
        flash([exc.key, exc.values], "error")
    return redirect(url_for("admin.user", user_id=target.id))


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
        except ServiceError as exc:
            flash([exc.key, exc.values], "error")
    items = Announcement.query.order_by(Announcement.id.desc()).limit(50).all()
    return render_template("admin/announcements.html", items=items, errors=errors, form=form,
                           section="announcements")


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


@bp.route("/announcements/<int:announcement_id>/edit", methods=["GET", "POST"])
@limiter.limit("60 per hour", methods=["POST"])
def announcement_edit(announcement_id):
    from ...models import Announcement
    ann = db.session.get(Announcement, announcement_id)
    if ann is None:
        abort(404)
    errors = {}
    if request.method == "POST":
        try:
            svc.update_announcement(current_user, announcement_id, request.form)
            flash("flash.admin_saved", "success")
            return redirect(url_for("admin.announcements"))
        except FieldErrors as exc:
            errors = exc.errors
    return render_template("admin/announcement_edit.html", ann=ann, errors=errors,
                           form=request.form if errors else ann.__dict__, section="announcements")


@bp.post("/announcements/<int:announcement_id>/delete")
@limiter.limit("30 per hour")
def announcement_delete(announcement_id):
    try:
        svc.delete_announcement(current_user, announcement_id)
        flash("flash.admin_saved", "success")
    except ServiceError as exc:
        flash([exc.key, exc.values], "error")
    return redirect(url_for("admin.announcements"))


# ------------------------------------------------------------------ database
@bp.get("/database")
def database():
    return render_template("admin/database.html", info=dbs.info(), backups=dbs.list_backups(),
                           verified=None, section="database")


@bp.post("/database/backup")
@limiter.limit("10 per hour")
def database_backup():
    try:
        name = dbs.create_backup()
        svc.audit(current_user, "database.backup", "database", None, {"file": name})
        db.session.commit()
        flash("flash.admin_saved", "success")
    except (ServiceError, OSError) as exc:
        flash(getattr(exc, "key", "admin.db.unsupported"), "error")
    return redirect(url_for("admin.database"))


@bp.get("/database/backups/<name>/download")
@limiter.limit("20 per hour")
def database_download(name):
    try:
        path = dbs.backup_path(name)
    except ServiceError:
        abort(404)
    svc.audit(current_user, "database.backup_download", "database", None, {"file": name})
    db.session.commit()
    res = send_file(path, as_attachment=True, download_name=name, mimetype="application/octet-stream")
    res.headers["Cache-Control"] = "no-store"
    return res


@bp.post("/database/backups/<name>/verify")
@limiter.limit("20 per hour")
def database_verify(name):
    try:
        verified = dict(dbs.verify_file(dbs.backup_path(name)), name=name)
    except ServiceError as exc:
        flash(exc.key, "error")
        return redirect(url_for("admin.database"))
    svc.audit(current_user, "database.backup_verify", "database", None, {"file": name, "ok": verified["ok"]})
    db.session.commit()
    return render_template("admin/database.html", info=dbs.info(), backups=dbs.list_backups(),
                           verified=verified, section="database")


@bp.post("/database/backups/<name>/delete")
@limiter.limit("20 per hour")
def database_delete(name):
    try:
        dbs.delete_backup(name)
        svc.audit(current_user, "database.backup_delete", "database", None, {"file": name})
        db.session.commit()
        flash("flash.admin_saved", "success")
    except ServiceError as exc:
        flash(exc.key, "error")
    return redirect(url_for("admin.database"))


@bp.post("/database/cleanup")
@limiter.limit("10 per hour")
def database_cleanup():
    """Delete READ notices older than N days, or old sign-in log entries. Nothing else can be deleted here."""
    kind, raw = request.form.get("kind", ""), (request.form.get("days") or "").strip()
    try:
        days = int(raw)
        if kind == "notices":
            n = dbs.cleanup_read_notices(days)
        elif kind == "events" and 30 <= days <= 3650:
            n = security_service.prune(days)
        else:
            raise ServiceError("admin.db.bad_days")
        svc.audit(current_user, f"database.cleanup_{kind}", "database", None, {"days": days, "deleted": n})
        db.session.commit()
        flash("flash.admin_saved", "success")
    except (ValueError, ServiceError) as exc:
        flash(getattr(exc, "key", "admin.db.bad_days"), "error")
    return redirect(url_for("admin.database"))


# ------------------------------------------------------------------ security
@bp.get("/security")
def security():
    from ...models import AdminAuditLog, PushDelivery
    logins, failed = security_service.recent("login"), security_service.recent("login_failed")
    ids = [e.user_id for e in logins if e.user_id]
    names = {u.id: u.username for u in User.query.filter(User.id.in_(ids or [0])).all()}
    push_bad = (PushDelivery.query.filter(PushDelivery.status.in_(("failed", "expired")))
                .order_by(PushDelivery.updated_at.desc()).limit(30).all())
    recent_admin = AdminAuditLog.query.order_by(AdminAuditLog.id.desc()).limit(10).all()
    return render_template("admin/security.html", logins=logins, failed=failed, names=names, push_bad=push_bad,
                           recent_admin=recent_admin, ps=svc.push_stats(), section="security")
