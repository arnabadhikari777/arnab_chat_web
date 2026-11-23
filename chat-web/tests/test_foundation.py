import json
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.config import env_list, env_mb
from app.extensions import db
from app.i18n import load_messages, t
from app.models import Conversation, Message, MessageRequest, Notification, User
from tests.conftest import make_user


# ---------- app, health, headers ----------
def test_home_redirects_visitors_to_login(client):
    res = client.get("/")
    assert res.status_code == 302 and "/login" in res.headers["Location"]


def test_health_checks_database(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json() == {"ok": True, "database": True}


def test_security_headers_present(client):
    res = client.get("/login")
    assert "default-src 'self'" in res.headers["Content-Security-Policy"]
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"


def test_design_page_available_in_development(client):
    assert client.get("/design").status_code == 200


def test_missing_secret_key_stops_the_app(monkeypatch):
    from app import config, create_app

    monkeypatch.setattr(config.TestingConfig, "SECRET_KEY", "")
    with pytest.raises(RuntimeError):
        create_app("testing")


# ---------- error handling ----------
def test_404_html_page_is_friendly(client):
    res = client.get("/nope")
    assert res.status_code == 404
    assert b"Page not found" in res.data
    assert b"Traceback" not in res.data


def test_404_json_for_api_paths(client):
    res = client.get("/api/nope")
    assert res.status_code == 404
    body = res.get_json()
    assert body["ok"] is False and body["error"]["code"] == 404


def test_unexpected_error_hides_details(app, client):
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.get("/boom")
    def boom():
        raise ValueError("secret internal detail")

    res = client.get("/boom")
    assert res.status_code == 500
    assert b"secret internal detail" not in res.data


# ---------- config parsing ----------
def test_env_list_cleans_values(monkeypatch):
    monkeypatch.setenv("X_EXT", " JPG, .png ,, webp ")
    assert env_list("X_EXT") == ["jpg", "png", "webp"]


def test_env_mb_returns_bytes_and_falls_back(monkeypatch):
    monkeypatch.setenv("X_MB", "3")
    assert env_mb("X_MB", 5) == 3 * 1024 * 1024
    monkeypatch.setenv("X_MB", "abc")
    assert env_mb("X_MB", 5) == 5 * 1024 * 1024


def test_upload_limits_come_from_config(app):
    assert app.config["UPLOAD_MAX_BYTES"]["image"] > 0
    assert "pdf" in app.config["UPLOAD_ALLOWED_EXTENSIONS"]["document"]


# ---------- translations ----------
def test_english_and_bengali_have_same_keys():
    assert set(load_messages("en")) == set(load_messages("bn"))


def test_bengali_translation_and_fallback(app):
    assert t("nav.messages", lang="bn") == "মেসেজ"
    assert t("nav.messages", lang="xx") == "Messages"  # unknown language -> English
    assert t("no.such.key", lang="en") == "no.such.key"


def test_translation_placeholders(app):
    assert t("status.last_seen", lang="en", time="9:00") == "Last seen 9:00"


def test_i18n_bundle_endpoint(client):
    data = client.get("/i18n/bundle.json").get_json()
    assert data["lang"] == "en" and data["messages"]["nav.people"] == "People"


def test_language_cookie_is_used(client):
    client.set_cookie("lang", "bn")
    assert client.get("/i18n/bundle.json").get_json()["lang"] == "bn"


# ---------- database rules ----------
def test_username_is_unique_and_lowercase(app):
    make_user("Arnab", email="a@example.com")
    assert User.query.first().username == "arnab"
    with pytest.raises(IntegrityError):
        make_user("arnab", email="other@example.com")
    db.session.rollback()


def test_email_is_unique(app):
    make_user("one", email="same@example.com")
    with pytest.raises(IntegrityError):
        make_user("two", email="SAME@example.com")
    db.session.rollback()


def test_username_cannot_change(app):
    user = make_user("fixed")
    with pytest.raises(ValueError):
        user.username = "changed"


def test_password_is_hashed(app):
    user = make_user("pw")
    user.set_password("correct horse")
    assert user.password_hash != "correct horse"
    assert user.check_password("correct horse") and not user.check_password("wrong")


def test_suspended_user_is_not_active(app):
    assert make_user("ok").is_active
    assert not make_user("bad", status="suspended").is_active


def test_one_conversation_per_pair(app):
    a, b = make_user("a1"), make_user("b1")
    db.session.add(Conversation(user_low_id=a.id, user_high_id=b.id))
    db.session.commit()
    db.session.add(Conversation(user_low_id=a.id, user_high_id=b.id))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_conversation_ids_must_be_ordered(app):
    a, b = make_user("a2"), make_user("b2")
    db.session.add(Conversation(user_low_id=b.id, user_high_id=a.id))  # wrong order
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_duplicate_client_uuid_is_rejected(app):
    a, b = make_user("a3"), make_user("b3")
    conv = Conversation(user_low_id=a.id, user_high_id=b.id)
    db.session.add(conv)
    db.session.commit()
    db.session.add(Message(conversation_id=conv.id, sender_id=a.id, body="hi", client_uuid="u-1"))
    db.session.commit()
    db.session.add(Message(conversation_id=conv.id, sender_id=a.id, body="hi", client_uuid="u-1"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_only_one_pending_request_but_new_one_allowed_after_reject(app):
    a, b = make_user("a4"), make_user("b4")
    first = MessageRequest(sender_id=a.id, receiver_id=b.id)
    db.session.add(first)
    db.session.commit()

    db.session.add(MessageRequest(sender_id=a.id, receiver_id=b.id))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()

    first.status = "rejected"
    db.session.commit()
    db.session.add(MessageRequest(sender_id=a.id, receiver_id=b.id))
    db.session.commit()  # allowed now


def test_cannot_request_yourself(app):
    a = make_user("a5")
    db.session.add(MessageRequest(sender_id=a.id, receiver_id=a.id))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_notification_dedupe_key(app):
    a = make_user("a6")
    db.session.add(Notification(user_id=a.id, type="request", dedupe_key="request:1"))
    db.session.commit()
    db.session.add(Notification(user_id=a.id, type="request", dedupe_key="request:1"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_foreign_keys_enforced(app):
    db.session.add(Message(conversation_id=999, sender_id=999, body="x", client_uuid="z"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
