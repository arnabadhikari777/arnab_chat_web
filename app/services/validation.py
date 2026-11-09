import re

from .errors import FieldErrors

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")
RESERVED_USERNAMES = {"admin", "administrator", "root", "support", "system", "moderator", "help"}


def clean_email(value: str) -> str:
    return (value or "").strip().lower()


def check_email(value: str):
    if not value or len(value) > 255 or not EMAIL_RE.match(value):
        return ("err.email_invalid", {})
    return None


def check_password(value: str):
    value = value or ""
    if len(value) < 8 or len(value) > 128 or not any(c.isalpha() for c in value) \
            or not any(c.isdigit() for c in value):
        return ("err.password_weak", {})
    return None


def validate_registration(form, app_name: str = "") -> dict:
    """Return cleaned values or raise FieldErrors with every problem found."""
    errors: dict = {}
    full_name = " ".join((form.get("full_name") or "").split())
    email = clean_email(form.get("email"))
    username = (form.get("username") or "").strip().lower()
    password = form.get("password") or ""
    confirm = form.get("confirm_password") or ""
    account_type = form.get("account_type") or ""

    if not 2 <= len(full_name) <= 60:
        errors["full_name"] = ("err.full_name", {})
    if (e := check_email(email)):
        errors["email"] = e
    reserved = RESERVED_USERNAMES | ({app_name.lower()} if app_name else set())
    if not USERNAME_RE.match(username) or username in reserved:
        errors["username"] = ("err.username_invalid", {})
    if (e := check_password(password)):
        errors["password"] = e
    elif password != confirm:
        errors["confirm_password"] = ("err.password_mismatch", {})
    if account_type not in ("public", "private"):
        errors["account_type"] = ("err.account_type", {})

    if errors:
        raise FieldErrors(errors)
    return {
        "full_name": full_name,
        "email": email,
        "username": username,
        "password": password,
        "is_public": account_type == "public",
    }
