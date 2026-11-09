import io

import pytest

from app.extensions import db, limiter
from app.models import OtpVerification, User
from app.services import cloudinary_service, email_service
from app.services.errors import EmailDeliveryError
from app.utils.time import utcnow
from datetime import timedelta
from tests.conftest import make_user

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200


# ------------------------------------------------------------------ fixtures
@pytest.fixture()
def fakes(fake_services):
    return fake_services


def last_code(fakes):
    return fakes.sent[-1]["body"].split("is ")[1][:6]


def form(**over):
    data = {
        "full_name": "Arnab Adhikari", "email": "Arnab@Example.com", "username": "Arnab_1",
        "password": "secret123", "confirm_password": "secret123", "account_type": "public",
        "photo": (io.BytesIO(PNG), "me.png"),
    }
    data.update(over)
    return {k: v for k, v in data.items() if v is not None}


def register(client, **over):
    return client.post("/register", data=form(**over), content_type="multipart/form-data")


def register_and_verify(client, fakes, **over):
    register(client, **over)
    return client.post("/verify-email", data={"code": last_code(fakes)})


# ------------------------------------------------------------------ registration
def test_register_creates_unverified_user_and_sends_otp(client, fakes):
    res = register(client)
    assert res.status_code == 302 and "/verify-email" in res.headers["Location"]
    user = User.query.one()
    assert user.username == "arnab_1" and user.email == "arnab@example.com"
    assert user.is_email_verified is False and user.is_public is True
    assert user.password_hash != "secret123" and user.check_password("secret123")
    assert user.avatar_url.startswith("https://res.cloudinary.com/")
    assert fakes.uploads[0]["folder"] == "goppo/avatars"      # folder comes from config
    assert len(fakes.sent) == 1 and fakes.sent[0]["to"] == "arnab@example.com"
    assert len(last_code(fakes)) == 6 and last_code(fakes).isdigit()


def test_private_account_choice_is_saved(client, fakes):
    register(client, account_type="private")
    assert User.query.one().is_public is False


def test_otp_is_stored_hashed(client, fakes):
    register(client)
    code = last_code(fakes)
    row = OtpVerification.query.one()
    assert code not in row.code_hash and len(row.code_hash) == 64


@pytest.mark.parametrize("field,value,key", [
    ("full_name", "A", "Enter your name"),
    ("email", "not-an-email", "valid email"),
    ("username", "ab", "3 to 20"),
    ("username", "bad name!", "3 to 20"),
    ("username", "admin", "3 to 20"),
    ("password", "short1", "at least 8"),
    ("password", "allletters", "at least 8"),
    ("password", "12345678", "at least 8"),
    ("confirm_password", "different1", "do not match"),
    ("account_type", None, "Public or Private"),
    ("photo", None, "Choose a profile photo"),
])
def test_registration_validation(client, fakes, field, value, key):
    res = register(client, **{field: value})
    assert res.status_code == 200 and key.encode() in res.data
    assert User.query.count() == 0 and fakes.uploads == []


def test_form_values_are_kept_after_error(client, fakes):
    res = register(client, username="ab")
    assert b'value="Arnab Adhikari"' in res.data


def test_username_must_be_unique_case_insensitive(client, fakes):
    make_user("taken", is_email_verified=True)
    res = register(client, username="TAKEN")
    assert b"username is taken" in res.data and User.query.count() == 1


def test_verified_email_cannot_register_again(client, fakes):
    make_user("old", email="arnab@example.com", is_email_verified=True)
    res = register(client)
    assert b"already registered" in res.data


def test_html_in_name_is_escaped(client, fakes):
    register_and_verify(client, fakes, full_name="<script>alert(1)</script>")
    assert b"<script>alert(1)</script>" not in client.get("/").data


# ------------------------------------------------------------------ photo checks
def test_photo_wrong_extension(client, fakes):
    res = register(client, photo=(io.BytesIO(b"hello"), "me.exe"))
    assert b"file type is not allowed" in res.data and fakes.uploads == []


def test_photo_fake_png_is_rejected(client, fakes):
    res = register(client, photo=(io.BytesIO(b"this is text, not a picture"), "me.png"))
    assert b"not a real picture" in res.data and fakes.uploads == []


def test_photo_too_big_uses_env_limit(app, client, fakes):
    app.config["UPLOAD_MAX_BYTES"] = {**app.config["UPLOAD_MAX_BYTES"], "image": 100}
    res = register(client, photo=(io.BytesIO(PNG), "me.png"))
    assert b"too big" in res.data and fakes.uploads == []


def test_photo_empty_file(client, fakes):
    res = register(client, photo=(io.BytesIO(b""), "me.png"))
    assert b"empty" in res.data


def test_cloudinary_failure_creates_no_user(client, fakes, monkeypatch):
    def boom(stream, **o):
        raise RuntimeError("network down")

    monkeypatch.setattr(cloudinary_service.cloudinary.uploader, "upload", boom)
    res = register(client)
    assert res.status_code == 200 and b"could not upload" in res.data
    assert User.query.count() == 0 and b"network down" not in res.data


def test_cloudinary_not_configured(app, client, fakes):
    app.config["CLOUDINARY_API_SECRET"] = ""
    res = register(client)
    assert b"not set up yet" in res.data and User.query.count() == 0


# ------------------------------------------------------------------ re-register / cleanup
def test_same_unverified_email_replaces_old_attempt(client, fakes):
    register(client)
    register(client, username="new_name")
    users = User.query.all()
    assert len(users) == 1 and users[0].username == "new_name"
    assert fakes.deleted == ["pid1"]  # the old photo was cleaned up


def test_fresh_unverified_username_is_reserved_but_stale_is_freed(app, client, fakes):
    make_user("held", email="other@example.com")
    res = register(client, username="held")
    assert b"username is taken" in res.data
    User.query.filter_by(username="held").one().created_at = utcnow() - timedelta(hours=3)
    db.session.commit()
    assert register(client, username="held").status_code == 302


# ------------------------------------------------------------------ email delivery failure
def test_email_failure_keeps_account_and_allows_immediate_retry(client, fakes, monkeypatch):
    def fail(*a, **k):
        raise EmailDeliveryError()

    monkeypatch.setattr(email_service, "send_email", fail)
    res = register(client)
    assert res.status_code == 302
    assert b"could not send the email" in client.get("/verify-email").data
    assert OtpVerification.query.count() == 0          # no dead code left behind
    monkeypatch.setattr(email_service, "send_email", lambda to, s, b: fakes.sent.append({"body": b}))
    client.post("/verify-email/resend")
    assert len(fakes.sent) == 1


# ------------------------------------------------------------------ verification
def test_verify_success_logs_in(client, fakes):
    res = register_and_verify(client, fakes)
    assert res.status_code == 302
    assert User.query.one().is_email_verified is True
    assert b"Arnab Adhikari" in client.get("/").data


def test_wrong_code_counts_down_then_locks(client, fakes):
    register(client)
    right = last_code(fakes)
    wrong = "000000" if right != "000000" else "111111"
    first = client.post("/verify-email", data={"code": wrong})
    assert b"4 tries left" in first.data
    for _ in range(3):
        client.post("/verify-email", data={"code": wrong})
    assert b"Too many wrong tries" in client.post("/verify-email", data={"code": wrong}).data
    # even the right code no longer works once locked
    assert b"Too many wrong tries" in client.post("/verify-email", data={"code": right}).data
    assert User.query.one().is_email_verified is False


def test_code_expires(client, fakes):
    register(client)
    row = OtpVerification.query.one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    res = client.post("/verify-email", data={"code": last_code(fakes)})
    assert b"wrong or has expired" in res.data


def test_code_works_only_once(client, fakes):
    register(client)
    code = last_code(fakes)
    client.post("/verify-email", data={"code": code})
    client.post("/logout")
    from app.services import otp_service
    with client.session_transaction() as s:
        s["pending_email"], s["pending_purpose"] = "arnab@example.com", "register"
    assert b"wrong or has expired" in client.post("/verify-email", data={"code": code}).data


def test_new_code_cancels_old_code(client, fakes):
    register(client)
    old = last_code(fakes)
    client.post("/verify-email/resend")
    new = last_code(fakes)
    assert len(fakes.sent) == 2
    if old != new:
        assert b"wrong" in client.post("/verify-email", data={"code": old}).data
    assert client.post("/verify-email", data={"code": new}).status_code == 302


def test_resend_cooldown(app, client, fakes):
    app.config["OTP_RESEND_COOLDOWN_SECONDS"] = 60
    register(client)
    res = client.post("/verify-email/resend", follow_redirects=True)
    assert b"Please wait" in res.data and len(fakes.sent) == 1


def test_hourly_send_limit(app, client, fakes):
    app.config["OTP_MAX_SENDS_PER_HOUR"] = 3
    register(client)
    client.post("/verify-email/resend")
    client.post("/verify-email/resend")
    res = client.post("/verify-email/resend", follow_redirects=True)
    assert b"Too many codes" in res.data and len(fakes.sent) == 3


def test_verify_page_needs_pending_signup(client):
    assert "/login" in client.get("/verify-email").headers["Location"]


# ------------------------------------------------------------------ login
def login(client, identifier="arnab_1", password="secret123", **extra):
    return client.post("/login", data={"identifier": identifier, "password": password, **extra})


def verified_user(client, fakes):
    register_and_verify(client, fakes)
    client.post("/logout")


def test_login_with_username_and_email(client, fakes):
    verified_user(client, fakes)
    assert login(client).status_code == 302
    client.post("/logout")
    assert login(client, "ARNAB@example.com").status_code == 302
    assert b"Arnab Adhikari" in client.get("/").data


def test_wrong_password_and_unknown_user_look_the_same(client, fakes):
    verified_user(client, fakes)
    a = login(client, password="wrongpass1")
    b = login(client, identifier="nobody")
    assert a.status_code == b.status_code == 200
    assert b"email, username or password is wrong" in a.data and b"email, username or password is wrong" in b.data


def test_unverified_user_cannot_log_in(client, fakes):
    register(client)
    client.get("/logout")
    res = login(client)
    assert res.status_code == 302 and "/verify-email" in res.headers["Location"]
    assert client.get("/").status_code == 302          # still not signed in
    assert len(fakes.sent) >= 1


@pytest.mark.parametrize("status,text", [("suspended", b"suspended"), ("banned", b"not available")])
def test_blocked_accounts_cannot_log_in(client, fakes, status, text):
    verified_user(client, fakes)
    User.query.one().status = status
    db.session.commit()
    assert text in login(client).data
    assert client.get("/").status_code == 302


def test_suspending_a_logged_in_user_ends_their_session(client, fakes):
    register_and_verify(client, fakes)
    assert client.get("/").status_code == 200
    User.query.one().status = "suspended"
    db.session.commit()
    assert client.get("/").status_code == 302


def test_logout_needs_post_and_ends_session(client, fakes):
    register_and_verify(client, fakes)
    assert client.get("/logout").status_code == 405
    client.post("/logout")
    assert client.get("/").status_code == 302


def test_logged_in_user_skips_auth_pages(client, fakes):
    register_and_verify(client, fakes)
    for path in ("/login", "/register", "/forgot-password"):
        assert client.get(path).headers["Location"].endswith("/")


def test_open_redirect_is_blocked(client, fakes):
    verified_user(client, fakes)
    res = login(client, next="https://evil.example/steal")
    assert "evil.example" not in res.headers["Location"]
    client.post("/logout")
    res = login(client, next="//evil.example")
    assert "evil.example" not in res.headers["Location"]


def test_next_path_on_same_site_is_used(client, fakes):
    verified_user(client, fakes)
    assert login(client, next="/design").headers["Location"].endswith("/design")


# ------------------------------------------------------------------ password reset
def test_reset_flow_changes_password(client, fakes):
    verified_user(client, fakes)
    client.post("/forgot-password", data={"email": "arnab@example.com"})
    assert len(fakes.sent) == 2 and "reset" in fakes.sent[-1]["body"]
    code = last_code(fakes)
    res = client.post("/reset-password", data={"code": code, "password": "newpass456", "confirm_password": "newpass456"})
    assert res.status_code == 302 and "/login" in res.headers["Location"]
    assert b"wrong" in login(client).data                        # old password dead
    assert login(client, password="newpass456").status_code == 302


def test_unknown_email_looks_the_same_and_sends_nothing(client, fakes):
    res = client.post("/forgot-password", data={"email": "ghost@example.com"})
    assert res.status_code == 302 and "/reset-password" in res.headers["Location"]
    assert fakes.sent == []
    res = client.post("/reset-password", data={"code": "123456", "password": "newpass456", "confirm_password": "newpass456"})
    assert b"wrong or has expired" in res.data


def test_unverified_account_gets_no_reset_code(client, fakes):
    register(client)
    sent_before = len(fakes.sent)
    client.post("/forgot-password", data={"email": "arnab@example.com"})
    assert len(fakes.sent) == sent_before


def test_reset_wrong_code_and_weak_password(client, fakes):
    verified_user(client, fakes)
    client.post("/forgot-password", data={"email": "arnab@example.com"})
    code = last_code(fakes)
    bad_pw = client.post("/reset-password", data={"code": code, "password": "weak", "confirm_password": "weak"})
    assert b"at least 8" in bad_pw.data
    wrong = "000000" if code != "000000" else "111111"
    assert b"tries left" in client.post("/reset-password", data={"code": wrong, "password": "newpass456", "confirm_password": "newpass456"}).data
    mismatch = client.post("/reset-password", data={"code": code, "password": "newpass456", "confirm_password": "other789"})
    assert b"do not match" in mismatch.data


def test_reset_code_cannot_be_reused(client, fakes):
    verified_user(client, fakes)
    client.post("/forgot-password", data={"email": "arnab@example.com"})
    code = last_code(fakes)
    data = {"code": code, "password": "newpass456", "confirm_password": "newpass456"}
    client.post("/reset-password", data=data)
    with client.session_transaction() as s:
        s["pending_email"], s["pending_purpose"] = "arnab@example.com", "reset_password"
    assert b"wrong or has expired" in client.post("/reset-password", data=data).data


def test_register_code_cannot_reset_password(client, fakes):
    """A code issued for one purpose never works for another."""
    register(client)
    code = last_code(fakes)
    client.post("/verify-email", data={"code": code})
    client.post("/logout")
    client.post("/forgot-password", data={"email": "arnab@example.com"})
    res = client.post("/reset-password", data={"code": code, "password": "newpass456", "confirm_password": "newpass456"})
    assert b"wrong" in res.data or b"tries left" in res.data


# ------------------------------------------------------------------ admin role
def test_admin_email_becomes_admin_after_verification(client, fakes):
    register_and_verify(client, fakes, email="boss@example.com", username="boss")
    assert User.query.filter_by(username="boss").one().role == "admin"


def test_admin_role_not_given_before_verification(client, fakes):
    register(client, email="boss@example.com", username="boss")
    assert User.query.one().role == "user"


def test_normal_email_is_not_admin(client, fakes):
    register_and_verify(client, fakes)
    assert User.query.one().role == "user"


def test_admin_role_follows_the_env_list_at_login(app, client, fakes):
    register_and_verify(client, fakes, email="boss@example.com", username="boss")
    client.post("/logout")
    app.config["ADMIN_EMAILS"] = frozenset()
    login(client, "boss")
    assert User.query.filter_by(username="boss").one().role == "user"


def test_admin_required_guard(app, client, fakes):
    from app.middleware.guards import admin_required

    @app.get("/admin-test")
    @admin_required
    def admin_test():
        return "secret"

    assert client.get("/admin-test").status_code == 302            # anonymous -> login
    register_and_verify(client, fakes)
    assert client.get("/admin-test").status_code == 403             # normal user
    client.post("/logout")
    register_and_verify(client, fakes, email="boss@example.com", username="boss")
    assert client.get("/admin-test").data == b"secret"              # admin


# ------------------------------------------------------------------ security
def test_csrf_is_enforced(app, client, fakes):
    app.config["WTF_CSRF_ENABLED"] = True
    res = client.post("/login", data={"identifier": "x", "password": "y"})
    assert res.status_code == 400


def test_login_is_rate_limited(monkeypatch):
    from app import config, create_app

    monkeypatch.setattr(config.TestingConfig, "RATELIMIT_ENABLED", True)
    app = create_app("testing")          # a separate app where the limiter is switched on
    with app.app_context():
        db.create_all()
        client = app.test_client()
        codes = [client.post("/login", data={"identifier": "x", "password": "y"}).status_code
                 for _ in range(12)]
        assert codes[:10] == [200] * 10 and codes[10] == 429


def test_language_switch_sets_cookie_and_blocks_bad_redirect(client):
    res = client.get("/lang/bn?next=//evil.example")
    assert "evil.example" not in res.headers["Location"]
    assert "lang=bn" in res.headers["Set-Cookie"]
    assert "ইমেইল বা ইউজারনেম".encode() in client.get("/login").data
    assert client.get("/lang/xx").status_code == 404


def test_login_page_in_bengali_and_english_have_no_missing_keys(client):
    for lang in ("en", "bn"):
        client.set_cookie("lang", lang)
        for path in ("/login", "/register", "/forgot-password"):
            html = client.get(path).data.decode()
            assert "auth." not in html.replace("auth.js", "") and "field." not in html and "hint." not in html
