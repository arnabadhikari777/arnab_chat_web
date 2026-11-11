"""Security hardening and CPU/performance behaviour added in the audit."""
import io
from datetime import timedelta

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import Message
from app.services import cloudinary_service, email_service, presence_service
from app.services.errors import EmailDeliveryError, UploadError
from app.services.upload_validator import split_filename, validate_upload
from app.utils.time import utcnow
from tests.conftest import login_as, verified
from tests.test_messaging import duo, send  # noqa: F401  (fixture + helper)

REAL_SEND_EMAIL = email_service.send_email   # captured before the autouse fixture replaces it


# ---------------------------------------------------------------- sessions & caching
def test_password_change_signs_out_other_sessions(app):
    user = verified("sam")
    old_browser = login_as(app, user)
    assert old_browser.get("/api/summary").status_code == 200
    user.set_password("a-brand-new-pass-1")
    db.session.commit()
    assert old_browser.get("/api/summary").status_code == 401


def test_api_and_pages_are_not_cacheable(app):
    browser = login_as(app, verified("sam"))
    assert "no-store" in browser.get("/api/summary").headers["Cache-Control"]
    assert "no-store" in browser.get("/messages").headers["Cache-Control"]


def test_service_worker_is_served_from_the_root_and_never_caches_pages(client):
    res = client.get("/sw.js")
    assert res.status_code == 200
    assert res.headers["Service-Worker-Allowed"] == "/"
    code = res.get_data(as_text=True)
    assert 'req.mode === "navigate"' in code
    assert '  "/",\n' not in code                     # no page is precached
    assert "clear-caches" in code                    # logout wipes the cache


# ---------------------------------------------------------------- CPU: no pointless writes
def _count_updates(app):
    statements = []

    def grab(conn, cursor, statement, *a):
        if statement.lstrip().upper().startswith(("UPDATE", "INSERT")):
            statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", grab)
    return statements, lambda: event.remove(db.engine, "before_cursor_execute", grab)


def test_chat_list_does_not_write_when_nothing_is_waiting(app, duo):
    statements, stop = _count_updates(app)
    try:
        duo.a.get("/api/chats")
        duo.a.get("/api/chats")
    finally:
        stop()
    assert not [s for s in statements if "messages" in s.lower()]


def test_chat_list_no_longer_marks_waiting_messages_delivered(app, duo):
    """Changed on purpose: opening the app is not proof that a device received anything."""
    send(duo.a, duo, "hi")
    assert Message.query.one().status == "sent"
    duo.b.get("/api/chats")
    db.session.expire_all()
    assert Message.query.one().status == "sent"


def test_empty_acknowledgements_do_nothing(app, duo):
    assert duo.b.post(f"{duo.url}/delivered", json={"ids": []}).get_json()["updated"] == 0
    assert duo.b.post(f"{duo.url}/seen", json={}).get_json()["updated"] == 0


def test_chat_list_unread_and_preview_are_correct_for_many_chats(app, duo):
    send(duo.a, duo, "one", "uuid-0001-aaaa")
    send(duo.a, duo, "two", "uuid-0002-aaaa")
    chats = duo.b.get("/api/chats").get_json()
    assert chats["total_unread"] == 2
    assert chats["chats"][0]["unread"] == 2 and chats["chats"][0]["last"]["body"] == "two"
    assert duo.a.get("/api/chats").get_json()["chats"][0]["unread"] == 0


def test_poll_tells_the_browser_when_more_is_waiting(app, duo):
    assert duo.a.get(f"{duo.url}/poll").get_json()["more"] is False


def test_suspended_person_cannot_be_messaged_through_the_api(app, duo):
    duo.bob.status = "suspended"
    db.session.commit()
    assert send(duo.a, duo).status_code == 404


# ---------------------------------------------------------------- presence
def test_presence_timeout_is_never_shorter_than_the_write_interval(app):
    app.config.update(PRESENCE_TIMEOUT_SECONDS=5, PRESENCE_WRITE_INTERVAL_SECONDS=30)
    user = verified("sam")
    user.last_seen_at = utcnow() - timedelta(seconds=40)
    assert presence_service.is_online(user) is True          # 40 s < 30 + 15
    user.last_seen_at = utcnow() - timedelta(seconds=50)
    assert presence_service.is_online(user) is False


def test_presence_is_written_at_most_once_per_interval(app):
    user = verified("sam")
    user.last_seen_at = utcnow() - timedelta(seconds=5)
    stamp = user.last_seen_at
    presence_service.touch(user)
    db.session.refresh(user)
    assert user.last_seen_at == stamp
    user.last_seen_at = utcnow() - timedelta(seconds=120)
    db.session.commit()
    presence_service.touch(user)
    db.session.refresh(user)
    assert (utcnow() - user.last_seen_at).total_seconds() < 5


# ---------------------------------------------------------------- uploads
def test_unicode_file_names_keep_their_extension():
    assert split_filename("ছবি.png") == ("file.png", "png")
    assert split_filename("../../etc/passwd.PDF") == ("passwd.pdf", "pdf")
    assert split_filename("a.php.png")[1] == "png"


def _file(data, name, mimetype=None):
    from werkzeug.datastructures import FileStorage

    return FileStorage(stream=io.BytesIO(data), filename=name, content_type=mimetype)


@pytest.mark.parametrize("data", [b"<html><script>alert(1)</script>", b"MZ\x90\x00" + b"0" * 50,
                                  b"#!/bin/sh\nrm -rf /", b"text\x00binary", b"<svg onload=x>"])
def test_text_files_must_really_be_text(app, data):
    with pytest.raises(UploadError):
        validate_upload(_file(data, "notes.txt"), "document")


def test_plain_text_is_accepted(app):
    assert validate_upload(_file(b"hello world", "notes.txt"), "document").mime == "text/plain"


def test_dangerous_content_types_are_refused_even_with_a_good_name(app):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    with pytest.raises(UploadError):
        validate_upload(_file(png, "a.png", "image/svg+xml"), "image")
    with pytest.raises(UploadError):
        validate_upload(_file(png, "a.png", "text/html"), "image")
    assert validate_upload(_file(png, "a.png", "image/png"), "image").kind == "image"


def test_upload_body_is_streamed_with_exact_length():
    data = bytes(range(256)) * 1000
    stream = cloudinary_service._MultipartStream({"folder": "x"}, "file", 'a"b.bin', io.BytesIO(data), len(data))
    body = b"".join(iter(stream))
    assert len(body) == len(stream) and data in body and b'filename="ab.bin"' in body


# ---------------------------------------------------------------- email
class _Resp:
    def __init__(self, code=202, text=""):
        self.status_code, self.text = code, text


def test_http_email_provider_sends_and_reports_failure(app, monkeypatch):
    app.config.update(MAIL_PROVIDER="sendgrid", MAIL_API_KEY="k", MAIL_USERNAME="me@example.com")
    calls = []
    monkeypatch.setattr(email_service.requests, "post", lambda url, **kw: calls.append((url, kw)) or _Resp(202))
    REAL_SEND_EMAIL("to@example.com", "Hi", "Body")
    assert calls[0][0] == "https://api.sendgrid.com/v3/mail/send"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer k"

    monkeypatch.setattr(email_service.requests, "post", lambda url, **kw: _Resp(401, "bad key"))
    with pytest.raises(EmailDeliveryError):
        REAL_SEND_EMAIL("to@example.com", "Hi", "Body")


def test_failed_email_is_never_reported_as_sent(app, monkeypatch):
    app.config.update(MAIL_PROVIDER="smtp", MAIL_USERNAME="me@example.com", MAIL_PASSWORD="x")

    def boom(cfg):
        raise OSError("network down")

    monkeypatch.setattr(email_service, "_smtp_connect", boom)
    monkeypatch.setattr(email_service.time, "sleep", lambda s: None)
    with pytest.raises(EmailDeliveryError):
        REAL_SEND_EMAIL("to@example.com", "Hi", "Body")


def test_unconfigured_email_fails_loudly(app):
    app.config.update(MAIL_PROVIDER="smtp", MAIL_USERNAME="", MAIL_PASSWORD="")
    with pytest.raises(EmailDeliveryError):
        REAL_SEND_EMAIL("to@example.com", "Hi", "Body")
