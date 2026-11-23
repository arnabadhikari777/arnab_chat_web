"""Website-wide switches (Admin Panel > Settings) and how they are enforced."""
import pytest

from app.extensions import db
from app.models import AdminAuditLog, Announcement, Message, PushSubscription, SiteSetting, User
from tests.conftest import login_as, verified
from tests.test_push import configured, sent, sub_json  # noqa: F401


@pytest.fixture()
def site(app):
    boss = verified("boss", role="admin")
    alice, bob = verified("alice"), verified("bob", is_public=False)
    return type("S", (), dict(boss=boss, alice=alice, bob=bob, admin=login_as(app, boss),
                               a=login_as(app, alice), b=login_as(app, bob)))


ON = {"registration_open": "on", "login_open": "on", "chat_enabled": "on", "message_requests_enabled": "on",
      "sending_enabled": "on", "push_enabled": "on", "announcements_enabled": "on"}


def save(site, **changes):
    """Post the settings form like the browser does: checked boxes are sent, unchecked ones are absent."""
    form = {**ON, "site_name": "", "maintenance_message": "", "public_contact": ""}
    for key, value in changes.items():
        if value is False:
            form.pop(key, None)
        elif value is True:
            form[key] = "on"
        else:
            form[key] = value
    return site.admin.post("/admin/settings", data=form)


# ------------------------------------------------------------------ access and audit
def test_settings_page_is_for_admins_only(app, client, site):
    assert site.admin.get("/admin/settings").status_code == 200
    assert site.a.get("/admin/settings").status_code in (403, 404)
    assert site.a.post("/admin/settings", data={"maintenance_mode": "on"}).status_code in (403, 404)
    assert client.get("/admin/settings").status_code in (302, 401, 403)
    assert SiteSetting.query.count() == 0


def test_defaults_keep_everything_open(app, site):
    assert site.a.get("/messages").status_code == 200
    assert client_get_register(app).status_code == 200


def client_get_register(app):
    return app.test_client().get("/register")


def test_saving_writes_only_what_changed_and_audits_it(app, site):
    assert save(site, maintenance_mode=True, site_name="Test Chat").status_code == 302
    rows = {r.key: r.value for r in SiteSetting.query.all()}
    assert rows == {"maintenance_mode": "1", "site_name": "Test Chat"}
    log = AdminAuditLog.query.filter_by(action="settings.update").one()
    assert log.admin_id == site.boss.id
    assert log.details["changes"]["maintenance_mode"] == {"from": False, "to": True}


def test_saving_the_same_values_logs_nothing(app, site):
    save(site)
    assert AdminAuditLog.query.filter_by(action="settings.update").count() == 0


def test_unknown_fields_cannot_be_written(app, site):
    site.admin.post("/admin/settings", data={**ON, "role": "admin", "bogus": "x", "SECRET_KEY": "x"})
    assert SiteSetting.query.filter(SiteSetting.key.notin_(list(ON) + ["maintenance_mode"])).count() == 0
    assert db.session.get(User, site.alice.id).role != "admin"


def test_text_limits_and_line_breaks_are_refused(app, site):
    res = save(site, site_name="x" * 41)
    assert res.status_code == 200 and SiteSetting.query.count() == 0
    assert save(site, maintenance_message="a\nb").status_code == 200


def test_csrf_is_required(app, site):
    app.config["WTF_CSRF_ENABLED"] = True
    assert site.admin.post("/admin/settings", data={"maintenance_mode": "on"}).status_code == 400
    assert SiteSetting.query.count() == 0


def test_site_name_shows_in_pages_and_falls_back_when_empty(app, site):
    save(site, site_name="Campus Talk")
    assert "Campus Talk" in site.a.get("/messages").get_data(as_text=True)
    save(site, site_name="")
    assert "Campus Talk" not in site.a.get("/messages").get_data(as_text=True)


# ------------------------------------------------------------------ maintenance
def test_maintenance_blocks_people_but_not_admins(app, site):
    save(site, maintenance_mode=True, maintenance_message="Back at noon")
    page = site.a.get("/messages")
    assert page.status_code == 503 and "Back at noon" in page.get_data(as_text=True)
    assert page.headers["Retry-After"] == "300"
    api = site.a.get("/api/chats")
    assert api.status_code == 503 and api.get_json()["ok"] is False
    assert site.admin.get("/messages").status_code == 200
    assert site.admin.get("/admin").status_code == 200


def test_during_maintenance_sign_in_health_and_service_worker_still_work(app, site):
    save(site, maintenance_mode=True)
    anon = app.test_client()
    assert anon.get("/login").status_code == 200
    assert anon.get("/health").status_code == 200
    assert anon.get("/sw.js").status_code == 200
    assert anon.get("/forgot-password").status_code == 200
    assert anon.post("/login", data={"identifier": "boss", "password": "secret123"}).status_code == 302
    assert anon.get("/admin").status_code == 200                  # an admin who signs in during maintenance is not locked out


def test_maintenance_off_again_restores_the_site(app, site):
    save(site, maintenance_mode=True)
    save(site, maintenance_mode=False)
    assert site.a.get("/messages").status_code == 200


# ------------------------------------------------------------------ registration and login
def test_registration_toggle(app, site):
    save(site, registration_open=False)
    anon = app.test_client()
    assert anon.get("/register").status_code == 503
    assert anon.post("/register", data={"full_name": "N", "email": "n@example.com", "username": "newbie",
                                        "password": "secret123", "confirm_password": "secret123"}).status_code == 503
    assert User.query.filter_by(username="newbie").count() == 0
    save(site, registration_open=True)
    assert app.test_client().get("/register").status_code == 200


def test_login_toggle_blocks_people_but_never_admins(app, site):
    save(site, login_open=False)
    anon = app.test_client()
    res = anon.post("/login", data={"identifier": "alice", "password": "secret123"})
    assert res.status_code == 200 and anon.get("/messages").status_code in (302, 401)
    assert app.test_client().post("/login", data={"identifier": "boss", "password": "secret123"}).status_code == 302
    assert site.a.get("/api/summary").status_code == 200           # already signed in: stays signed in


# ------------------------------------------------------------------ chat, requests, sending
def test_chat_toggle(app, site):
    site.a.post("/u/bob/message")
    save(site, chat_enabled=False)
    assert site.a.get("/api/chats").status_code == 503
    assert site.a.get("/messages").status_code == 503
    assert site.a.get("/api/summary").status_code == 200             # badges and the Notice Box keep working
    assert site.a.post("/api/notices/read-all", json={}).status_code == 200
    assert app.test_client().post("/api/push/ack", json={}).status_code == 400   # not 503
    assert site.admin.get("/api/chats").status_code == 200
    save(site, chat_enabled=True)
    assert site.a.get("/api/chats").status_code == 200


def test_sending_toggle_blocks_new_messages_only(app, site):
    from app.models import Conversation
    verified("carol")                                                # public account: a chat can be opened at once
    a = site.a
    a.post("/u/carol/message")
    cid = Conversation.query.one().id
    assert a.post(f"/api/conversations/{cid}/messages", json={"uuid": "uuid-0001-aaaa", "body": "one"}).status_code == 201
    save(site, sending_enabled=False)
    res = a.post(f"/api/conversations/{cid}/messages", json={"uuid": "uuid-0002-aaaa", "body": "two"})
    assert res.status_code == 503 and res.get_json()["error"]["code"] == "site.sending_closed"
    assert Message.query.count() == 1
    assert a.get(f"/api/conversations/{cid}/messages").status_code == 200   # loading history still works
    assert a.get(f"/chat/{cid}").status_code == 200                  # reading still works
    save(site, sending_enabled=True)
    assert a.post(f"/api/conversations/{cid}/messages", json={"uuid": "uuid-0003-aaaa", "body": "three"}).status_code == 201


def test_message_request_toggle(app, site):
    save(site, message_requests_enabled=False)
    res = site.a.post("/u/bob/request")                               # bob is private
    assert res.status_code == 503
    from app.models import MessageRequest
    assert MessageRequest.query.count() == 0
    save(site, message_requests_enabled=True)
    assert site.a.post("/u/bob/request").status_code == 302
    assert MessageRequest.query.count() == 1


# ------------------------------------------------------------------ push and announcements
def test_push_switch_stops_config_registration_and_sending(app, site, configured, sent):
    assert site.b.get("/api/push/config").get_json()["configured"] is True
    save(site, push_enabled=False)
    body = site.b.get("/api/push/config").get_json()
    assert body["configured"] is False and body["publicKey"] is None
    assert site.b.post("/api/push/subscribe", json=sub_json(1)).status_code == 503
    save(site, push_enabled=True)
    assert site.b.post("/api/push/subscribe", json=sub_json(1)).status_code == 200
    save(site, push_enabled=False)
    site.a.post("/u/bob/request")
    assert sent.calls == []                                           # nothing was pushed while switched off


def test_announcement_switch(app, site):
    save(site, announcements_enabled=False)
    res = site.admin.post("/admin/announcements", data={"title": "Hello all", "body": "Some text here"})
    assert res.status_code == 200 and Announcement.query.count() == 0
    save(site, announcements_enabled=True)
    assert site.admin.post("/admin/announcements", data={"title": "Hello all", "body": "Some text here"}).status_code == 302
    assert Announcement.query.count() == 1


# ------------------------------------------------------------------ accounts and admins
def test_a_suspended_person_loses_access_on_the_very_next_request(app, site):
    assert site.a.get("/api/summary").status_code == 200
    site.admin.post(f"/admin/users/{site.alice.id}/status", data={"status": "suspended"})
    assert site.a.get("/api/summary").status_code == 401
    assert site.a.get("/messages").status_code in (302, 401)


def test_an_admin_cannot_suspend_themselves_or_another_admin(app, site):
    other = verified("boss2", role="admin")
    site.admin.post(f"/admin/users/{site.boss.id}/status", data={"status": "suspended"})
    site.admin.post(f"/admin/users/{other.id}/status", data={"status": "banned"})
    db.session.expire_all()
    assert db.session.get(User, site.boss.id).status == "active"
    assert db.session.get(User, other.id).status == "active"
    assert site.admin.get("/admin").status_code == 200
