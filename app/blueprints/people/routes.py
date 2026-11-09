from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...middleware.guards import auth_required
from ...services import message_request_service as requests_svc
from ...services import people_service, report_service
from ...services.errors import FieldErrors, ServiceError
from . import bp


def _page_number() -> int:
    try:
        return int(request.args.get("page", 1))
    except ValueError:
        return 1


def _other_or_404(username: str):
    user = people_service.get_visible_user(username)
    if user is None:
        abort(404)
    return user


@bp.get("/people")
@auth_required
def index():
    q = request.args.get("q", "")
    page = _page_number()
    users, has_next = people_service.list_people(current_user, q, page)
    return render_template(
        "people/list.html",
        users=users, has_next=has_next, page=max(1, page), q=people_service.clean_search(q),
    )


@bp.get("/u/<username>")
@auth_required
def profile(username):
    user = _other_or_404(username)
    rel = None if user.id == current_user.id else requests_svc.relationship(current_user, user)
    return render_template("people/profile.html", person=user, rel=rel, is_me=user.id == current_user.id)


@bp.post("/u/<username>/message")
@auth_required
def message(username):
    user = _other_or_404(username)
    try:
        conv = requests_svc.start_conversation(current_user, user)
    except ServiceError as exc:
        flash([exc.key, exc.values], "error")
        return redirect(url_for("people.profile", username=user.username))
    return redirect(url_for("chat.room", conversation_id=conv.id))


@bp.post("/u/<username>/request")
@auth_required
def send_request(username):
    user = _other_or_404(username)
    try:
        requests_svc.send_request(current_user, user)
        flash("flash.request_sent", "success")
    except ServiceError as exc:
        flash([exc.key, exc.values], "error")
    return redirect(url_for("people.profile", username=user.username))


def _answer(request_id: int, action: str):
    try:
        if action == "accept":
            conv = requests_svc.accept_request(current_user, request_id)
            flash("flash.request_accepted", "success")
            return redirect(url_for("chat.room", conversation_id=conv.id))
        req = requests_svc.reject_request(current_user, request_id)
        flash("flash.request_rejected", "success")
        return redirect(url_for("people.index"))
    except ServiceError as exc:
        if exc.key == "request.not_found":
            abort(404)  # not yours, or does not exist: same answer
        flash([exc.key, exc.values], "error")
        return redirect(url_for("people.index"))


@bp.post("/requests/<int:request_id>/accept")
@auth_required
def accept(request_id):
    return _answer(request_id, "accept")


@bp.post("/requests/<int:request_id>/reject")
@auth_required
def reject(request_id):
    return _answer(request_id, "reject")


@bp.route("/u/<username>/report", methods=["GET", "POST"])
@auth_required
def report(username):
    user = _other_or_404(username)
    if user.id == current_user.id:
        abort(404)
    errors = {}
    if request.method == "POST":
        try:
            report_service.create_report(
                current_user, user, request.form.get("reason", ""), request.form.get("details", ""))
        except FieldErrors as exc:
            errors = exc.errors
        except ServiceError as exc:
            flash([exc.key, exc.values], "error")
            return redirect(url_for("people.profile", username=user.username))
        else:
            flash("flash.report_sent", "success")
            return redirect(url_for("people.profile", username=user.username))
    return render_template("people/report.html", person=user, errors=errors, form=request.form,
                           reasons=report_service.REASONS)
