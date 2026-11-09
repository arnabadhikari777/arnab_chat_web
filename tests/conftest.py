import pytest

from app import create_app
from app.extensions import db
from app.services import cloudinary_service, email_service


@pytest.fixture()
def app():
    app = create_app("testing")

    # Tests keep one app context open for the whole test, but a real server gives
    # every request a fresh one. Without this, Flask-Login would remember the user of
    # the previous request and several browsers in one test would be mixed up.
    @app.before_request
    def _fresh_request_state():
        from flask import g

        g.pop("_login_user", None)
        db.session.expire_all()

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def make_user(username, email=None, **extra):
    from app.models import User

    user = User(
        full_name=username.title(),
        email=email or f"{username}@example.com",
        username=username,
        password_hash="x",
        **extra,
    )
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture(autouse=True)
def fake_services(app, monkeypatch):
    """No real emails and no real Cloudinary in any test."""
    app.config.update(OTP_RESEND_COOLDOWN_SECONDS=0, CLOUDINARY_CLOUD_NAME="c",
                      CLOUDINARY_API_KEY="k", CLOUDINARY_API_SECRET="s")
    sent, uploads, deleted = [], [], []

    def fake_send(to, subject, body):
        sent.append({"to": to, "subject": subject, "body": body})

    def fake_upload(stream, **options):
        uploads.append(options)
        return {"public_id": f"pid{len(uploads)}",
                "secure_url": f"https://res.cloudinary.com/x/{len(uploads)}.png"}

    monkeypatch.setattr(email_service, "send_email", fake_send)
    monkeypatch.setattr(cloudinary_service.cloudinary.uploader, "upload", fake_upload)
    monkeypatch.setattr(cloudinary_service.cloudinary.uploader, "destroy",
                        lambda pid, **kw: deleted.append(pid))
    return type("Fakes", (), {"sent": sent, "uploads": uploads, "deleted": deleted})


def verified(username, password="secret123", **extra):
    """A verified, ready-to-log-in user."""
    from app.models import User

    user = User(full_name=username.replace("_", " ").title(), email=f"{username}@example.com",
                username=username, is_email_verified=True, **extra)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def login_as(app, user, password="secret123"):
    """A new browser that is logged in as this user."""
    browser = app.test_client()
    res = browser.post("/login", data={"identifier": user.username, "password": password})
    assert res.status_code == 302, "login failed in test helper"
    return browser


@pytest.fixture(autouse=True)
def clean_typing_state():
    """Typing lives in memory for the whole process; start every test with none."""
    from app.realtime import get_backend  # noqa: F401  (imported lazily: needs the package)
    from app import realtime

    realtime._backend._typing.clear()
    yield
