from flask import current_app, flash, redirect, render_template, request, session, url_for
from flask_login import login_user
from flask_login import current_user

from ...extensions import db, limiter
from ...i18n import get_language
from ...middleware.guards import auth_required
from ...services import auth_service, otp_service, profile_service
from ...services.errors import EmailDeliveryError, FieldErrors, OtpCooldown, OtpError
from . import bp


def _max_mb() -> int:
    return max(1, current_app.config["UPLOAD_MAX_BYTES"]["image"] // (1024 * 1024))


@bp.get("")
@auth_required
def index():
    return render_template("settings/index.html", errors={})


@bp.post("/privacy")
@auth_required
def privacy():
    try:
        profile_service.set_privacy(current_user, request.form.get("account_type", ""))
        flash("flash.privacy_saved", "success")
    except FieldErrors as exc:
        return render_template("settings/index.html", errors=exc.errors), 400
    return redirect(url_for("settings.index"))


@bp.route("/profile", methods=["GET", "POST"])
@auth_required
@limiter.limit("30 per hour", methods=["POST"])
def edit_profile():
    errors = {}
    if request.method == "POST":
        try:
            profile_service.update_profile(
                current_user, request.form, request.files.get("avatar"), request.files.get("cover")
            )
        except FieldErrors as exc:
            errors = exc.errors
        else:
            flash("flash.profile_saved", "success")
            return redirect(url_for("main.home"))
    return render_template(
        "settings/edit_profile.html", errors=errors, form=request.form, max_mb=_max_mb()
    )


@bp.post("/notifications")
@auth_required
def notifications():
    current_user.notify_messages = request.form.get("notify_messages") == "on"
    current_user.notify_announcements = request.form.get("notify_announcements") == "on"
    db.session.commit()
    flash("flash.notifications_saved", "success")
    return redirect(url_for("settings.index"))


# ---- change password: current password -> code by email -> new password ----
def _pending_change() -> bool:
    return session.get("pending_purpose") == "change_password" and session.get("pending_email") == current_user.email


def _clear_pending():
    session.pop("pending_email", None)
    session.pop("pending_purpose", None)


@bp.route("/password", methods=["GET", "POST"])
@auth_required
@limiter.limit("10 per hour", methods=["POST"])
def password():
    errors = {}
    if request.method == "POST":
        try:
            auth_service.start_password_change(
                current_user, request.form.get("current_password", ""), get_language())
        except FieldErrors as exc:
            errors = exc.errors
        except OtpCooldown:
            pass  # a code was sent moments ago; go on to the code page
        except (OtpError, EmailDeliveryError) as exc:
            flash([exc.key, exc.values], "error")
            return render_template("settings/password.html", errors=errors)
        if not errors:
            session["pending_email"], session["pending_purpose"] = current_user.email, "change_password"
            flash("flash.code_sent", "success")
            return redirect(url_for("settings.password_confirm"))
    return render_template("settings/password.html", errors=errors)


@bp.route("/password/confirm", methods=["GET", "POST"])
@auth_required
@limiter.limit("15 per hour", methods=["POST"])
def password_confirm():
    if not _pending_change():
        return redirect(url_for("settings.password"))
    error, errors = None, {}
    if request.method == "POST":
        try:
            auth_service.change_password(
                current_user, request.form.get("code", ""), request.form.get("password", ""),
                request.form.get("confirm_password", ""))
        except FieldErrors as exc:
            errors = exc.errors
        except OtpError as exc:
            error = (exc.key, exc.values)
        else:
            _clear_pending()
            # Every other signed-in browser/phone is now signed out (the session id contains a password
            # fingerprint). This one stays signed in with a fresh session.
            keep = {k: session[k] for k in ("lang",) if k in session}
            session.clear()
            session.update(keep)
            session.permanent = True
            login_user(current_user._get_current_object())
            flash("flash.password_updated", "success")
            return redirect(url_for("settings.index"))
    return render_template(
        "settings/password_confirm.html", errors=errors, error=error, email=auth_service.mask_email(current_user.email),
        retry_after=otp_service.seconds_until_resend(current_user.email, "change_password"),
        minutes=current_app.config["OTP_EXPIRY_MINUTES"], code_length=current_app.config["OTP_LENGTH"],
    )


@bp.post("/password/resend")
@auth_required
@limiter.limit("10 per hour")
def password_resend():
    if not _pending_change():
        return redirect(url_for("settings.password"))
    try:
        auth_service.send_code(current_user.email, "change_password", get_language())
        flash("flash.code_sent", "success")
    except OtpCooldown as exc:
        flash(["otp.cooldown", {"seconds": exc.seconds}], "error")
    except (OtpError, EmailDeliveryError) as exc:
        flash([exc.key, exc.values], "error")
    return redirect(url_for("settings.password_confirm"))
