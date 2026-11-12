from flask import current_app, flash, redirect, render_template, request, session, url_for, abort
import time
import hmac
import hashlib
import json
import base64
from flask_login import current_user, login_user, logout_user

from ...extensions import limiter
from ...i18n import get_language
from ...middleware.guards import safe_next
from ...services import auth_service, otp_service, security_service
from ...services.errors import (
    EmailDeliveryError,
    FieldErrors,
    NeedsVerification,
    OtpCooldown,
    OtpError,
    ServiceError,
)
from . import bp


def _max_photo_mb() -> int:
    return max(1, current_app.config["UPLOAD_MAX_BYTES"]["image"] // (1024 * 1024))


def _rotate_session() -> None:
    """Start from a clean session at every login (blocks session fixation). Only the language choice and
    messages waiting to be shown are carried over; the CSRF token is created again."""
    keep = {k: session[k] for k in ("lang", "_flashes") if k in session}
    session.clear()
    session.update(keep)


def _login_account_key() -> str:
    """Rate-limit key per account name, so one account cannot be hammered from many IP addresses."""
    ident = (request.form.get("identifier") or "").strip().lower()
    return "login:" + hashlib.sha256(ident.encode()).hexdigest()[:24]


def _start_session(user, next_url: str | None = None):
    _rotate_session()
    session.permanent = True
    login_user(user)
    security_service.record("login", user.id)
    return redirect(safe_next(next_url, url_for("main.home")))


def _set_pending(email: str, purpose: str) -> None:
    session["pending_email"] = email
    session["pending_purpose"] = purpose


def _clear_pending() -> None:
    session.pop("pending_email", None)
    session.pop("pending_purpose", None)


def _pending(purpose: str):
    if session.get("pending_purpose") == purpose:
        return session.get("pending_email")
    return None


# ---------------------------------------------------------------- register
@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    form, errors = {}, {}
    if request.method == "POST":
        form = request.form
        try:
            user = auth_service.register_user(request.form, request.files.get("photo"), get_language())
        except FieldErrors as exc:
            errors = exc.errors
        except ServiceError as exc:  # upload failed / not configured
            errors = {"photo": (exc.key, exc.values)}
        else:
            _set_pending(user.email, "register")
            try:
                auth_service.send_code(user.email, "register", user.language)
                flash("flash.code_sent", "success")
            except OtpCooldown:
                flash("flash.code_sent", "success")  # a valid code was sent moments ago
            except (OtpError, EmailDeliveryError) as exc:
                flash(exc.key, "error")
            return redirect(url_for("auth.verify_email"))
    return render_template("auth/register.html", form=form, errors=errors, max_mb=_max_photo_mb())


# ---------------------------------------------------------------- verify email
def _code_page(email, purpose, template, error=None, **extra):
    return render_template(
        template,
        email=auth_service.mask_email(email),
        retry_after=otp_service.seconds_until_resend(email, purpose),
        minutes=current_app.config["OTP_EXPIRY_MINUTES"],
        code_length=current_app.config["OTP_LENGTH"],
        error=error,
        **extra,
    )


@bp.route("/verify-email", methods=["GET", "POST"])
@limiter.limit("15 per hour", methods=["POST"])
def verify_email():
    email = _pending("register")
    if not email:
        return redirect(url_for("auth.login"))
    error = None
    if request.method == "POST":
        try:
            user = auth_service.verify_registration(email, request.form.get("code", ""))
        except OtpError as exc:
            error = (exc.key, exc.values)
        else:
            flash("flash.verified", "success")
            return _start_session(user)
    return _code_page(email, "register", "auth/verify.html", error)


def _resend(purpose: str, back_endpoint: str):
    email = _pending(purpose)
    if not email:
        return redirect(url_for("auth.login"))
    try:
        if purpose == "reset_password":
            auth_service.start_password_reset(email, get_language())
            flash("flash.reset_sent", "success")
        else:
            auth_service.send_code(email, purpose, get_language())
            flash("flash.code_sent", "success")
    except OtpCooldown as exc:
        flash(["otp.cooldown", {"seconds": exc.seconds}], "error")
    except (OtpError, EmailDeliveryError) as exc:
        flash([exc.key, exc.values], "error")
    return redirect(url_for(back_endpoint))


@bp.post("/verify-email/resend")
@limiter.limit("10 per hour")
def resend_verification():
    return _resend("register", "auth.verify_email")


# ---------------------------------------------------------------- login / logout
@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute;60 per hour", methods=["POST"])
@limiter.limit(lambda: current_app.config["LOGIN_ACCOUNT_LIMIT"], key_func=_login_account_key, methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    next_url = request.args.get("next") or request.form.get("next")
    error, identifier = None, ""
    if request.method == "POST":
        identifier = request.form.get("identifier", "")
        try:
            user = auth_service.log_in(identifier, request.form.get("password", ""))
        except NeedsVerification as exc:
            _set_pending(exc.email, "register")
            try:
                auth_service.send_code(exc.email, "register", get_language())
            except (OtpError, EmailDeliveryError):
                pass  # the verify page has a resend button
            flash("auth.verify_first", "error")
            return redirect(url_for("auth.verify_email"))
        except ServiceError as exc:
            error = (exc.key, exc.values)
            security_service.record("login_failed", None, identifier.strip().lower())
        else:
            from ...services import settings_service
            if settings_service.enabled("login_open") or user.is_admin:   # admins can always sign in
                return _start_session(user, next_url)
            error = ("site.login_closed", {})
    return render_template("auth/login.html", error=error, identifier=identifier, next_url=next_url or "")


@bp.post("/logout")
def logout():
    if current_user.is_authenticated:
        try:
            from ...services import push_service
            push_service.forget_session_subscription(current_user, session.get("push_sub_id"))
        except Exception:  # noqa: BLE001
            current_app.logger.exception("could not remove push subscription on logout")
    logout_user()
    keep = {k: session[k] for k in ("lang",) if k in session}
    session.clear()                      # nothing of the old session survives a logout
    session.update(keep)
    flash("flash.logged_out", "success")
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------- forgot / reset
@bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    errors, email = {}, ""
    if request.method == "POST":
        email = request.form.get("email", "")
        try:
            clean = auth_service.start_password_reset(email, get_language())
        except FieldErrors as exc:
            errors = exc.errors
        else:
            _set_pending(clean, "reset_password")
            flash("flash.reset_sent", "success")
            return redirect(url_for("auth.reset_password"))
    return render_template("auth/forgot.html", errors=errors, email=email)


@bp.route("/reset-password", methods=["GET", "POST"])
@limiter.limit("15 per hour", methods=["POST"])
def reset_password():
    email = _pending("reset_password")
    if not email:
        return redirect(url_for("auth.forgot_password"))
    error, errors = None, {}
    if request.method == "POST":
        try:
            auth_service.reset_password(
                email,
                request.form.get("code", ""),
                request.form.get("password", ""),
                request.form.get("confirm_password", ""),
            )
        except FieldErrors as exc:
            errors = exc.errors
        except OtpError as exc:
            error = (exc.key, exc.values)
        else:
            _clear_pending()
            flash("flash.password_changed", "success")
            return redirect(url_for("auth.login"))
    return _code_page(email, "reset_password", "auth/reset.html", error, errors=errors)


@bp.post("/reset-password/resend")
@limiter.limit("10 per hour")
def resend_reset():
    return _resend("reset_password", "auth.reset_password")


# ---------------------------------------------------------------- Math Class SSO
def _verify_sso_token(token: str):
    secret = current_app.config.get("SSO_SHARED_SECRET") or ""
    if not secret or not token or "." not in token:
        return None
    try:
        payload_b64, sig = token.rsplit(".", 1)
        expected = hmac.new(
            secret.encode(),
            payload_b64.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        if int(payload.get("exp") or 0) < int(time.time()):
            return None
        if not payload.get("email"):
            return None
        return payload
    except Exception:
        return None


@bp.route("/sso")
@bp.route("/auth/sso")   # Math Class used to send students to /auth/sso -> that address gave "Page not found"
def sso_login():
    """Math Class redirects here with ?token=... — log the student in without password."""
    if not current_app.config.get("SSO_ENABLED"):
        abort(404)

    token = (request.args.get("token") or "").strip()
    payload = _verify_sso_token(token)
    if not payload:
        flash("flash.login_required", "error")
        return redirect(url_for("auth.login"))

    email = payload["email"].strip().lower()
    name = (payload.get("name") or "").strip() or email.split("@")[0]
    lang = get_language()

    try:
        user = auth_service.find_or_create_sso_user(email, name, lang)
    except Exception as exc:
        current_app.logger.exception("SSO user failed: %s", exc)
        flash("flash.login_required", "error")
        return redirect(url_for("auth.login"))

    if not user.is_active:
        flash("flash.login_required", "error")
        return redirect(url_for("auth.login"))

    _rotate_session()
    session.permanent = True
    login_user(user)
    security_service.record("login", user.id, "sso")
    return redirect(url_for("main.home"))

