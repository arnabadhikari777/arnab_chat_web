"""Admin control: website switches, maintenance, timed suspension, notes, sign-out-everywhere, announcements."""
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import (AdminAuditLog, Announcement, Message, ModerationNote, Notification, SiteSetting, User)
from app.services import site_settings
from app.utils.time import utcnow
from tests.conftest import login_as, verified

SWITCH_KEYS = list(site_settings.SWITCHES)


@pytest.fixture()
def world(app):
    boss, alice, bob = verified("boss"), verified("alice"), verified("bob")
    w = type("W", (), dict(boss=boss, alice=alice, bob=bob, admin=login_as(app, boss),
                           a=login_as(app, alice), b=login_as(app, bob)))
    from app.services import conversation_service
    w.conv = conversation_service.get_or_create(alice.id, bob.id).id
    db.session.commit()
    return w


def save_settings(browser, **over):
    form = {k: "1" for k, d in site_settings.SWITCHES.items() if d}      # everything at its default ...
    form.update({"maintenance_message": "", "site_announcement": ""})
    for k, v in over.items():
        if v in (False, None):
            form.pop(k, None)
        else:
            form[k] = "1" if v is True else v
    return browser.post("/admin/settings", data=form)


def send(browser, w, text="hello", uuid="uuid-0001-aaaa"):
    return browser.post(f"/api/conversations/{w.conv}/messages", json={"uuid": uuid, "body": text})


# ================================================================ settings page
def test_defaults_when_nothing_is_stored(app):
    assert site_settings.enabled("messaging_enabled") and not site_settings.enabled("maintenance_mode")
    assert site_settings.get("site_announcement") == ""


def test_only_admins_can_open_or_change_settings(app, world):
    assert world.admin.get("/admin/settings").status_code == 200
    assert world.a.get("/admin/settings").status_code == 403
    assert world.a.post("/admin/settings", data={"maintenance_mode": "1"}).status_code == 403
    assert SiteSetting.query.count() == 0


def test_saving_switches_is_stored_and_audited_without_copying_texts(app, world):
    assert save_settings(world.admin, messaging_enabled=False, site_announcement="Secret text").status_code == 302
    assert site_settings.enabled("messaging_enabled") is False
    assert site_settings.get("site_announcement") == "Secret text"
    log = AdminAuditLog.query.filter_by(action="settings.update").one()
    assert log.details["changed"]["messaging_enabled"] == {"from": True, "to": False}
    assert "Secret text" not in str(log.details)


def test_saving_the_same_values_again_writes_no_audit_entry(app, world):
    save_settings(world.admin)
    assert AdminAuditLog.query.filter_by(action="settings.update").count() == 0


def test_too_long_text_is_rejected_and_nothing_is_saved(app, world):
    res = save_settings(world.admin, site_announcement="x" * 301, messaging_enabled=False)
    assert res.status_code == 200
    assert site_settings.enabled("messaging_enabled") is True


def test_settings_page_shows_current_values(app, world):
    save_settings(world.admin, voice_enabled=False)
    html = world.admin.get("/admin/settings").get_data(as_text=True)
    assert 'id="s-voice_enabled"' in html and 'id="s-voice_enabled" name="voice_enabled" value="1" checked' not in html
    assert 'id="s-messaging_enabled" name="messaging_enabled" value="1" checked' in html


# ================================================================ each switch really works
def test_messaging_off_blocks_text_but_not_after_switching_back(app, world):
    save_settings(world.admin, messaging_enabled=False)
    res = send(world.a, world)
    assert res.status_code == 400 and res.get_json()["error"]["code"] == "site.messaging_off"
    assert Message.query.count() == 0
    save_settings(world.admin)
    assert send(world.a, world).status_code == 201


def test_uploads_off_and_voice_off_are_separate(app, world):
    import io
    save_settings(world.admin, uploads_enabled=False)
    file = {"uuid": "uuid-0002-aaaa", "file": (io.BytesIO(b"%PDF-1.4 test"), "a.pdf")}
    res = world.a.post(f"/api/conversations/{world.conv}/messages", data=file, content_type="multipart/form-data")
    assert res.get_json()["error"]["code"] == "site.uploads_off"
    save_settings(world.admin, voice_enabled=False)
    voice = {"uuid": "uuid-0003-aaaa", "voice": "1", "file": (io.BytesIO(b"OggS" + b"0" * 40), "v.ogg")}
    res = world.a.post(f"/api/conversations/{world.conv}/messages", data=voice, content_type="multipart/form-data")
    assert res.get_json()["error"]["code"] == "site.voice_off"
    assert Message.query.count() == 0


def test_requests_off_blocks_new_requests(app, world):
    private = verified("priv", is_public=False)
    save_settings(world.admin, requests_enabled=False)
    world.a.post(f"/u/{private.username}/request")
    from app.models import MessageRequest
    assert MessageRequest.query.count() == 0


def test_people_page_off_redirects_home(app, world):
    save_settings(world.admin, people_enabled=False)
    res = world.a.get("/people")
    assert res.status_code == 302 and "/people" not in res.headers["Location"]
    save_settings(world.admin)
    assert world.a.get("/people").status_code == 200


def test_profile_editing_off(app, world):
    save_settings(world.admin, profile_edit_enabled=False)
    assert world.a.get("/settings/profile").status_code == 302
    assert world.a.post("/settings/profile", data={"full_name": "New Name"}).status_code == 302
    assert User.query.filter_by(username="alice").one().full_name == "Alice"


def test_registration_off_closes_signup_pages(app, world, client):
    save_settings(world.admin, registration_enabled=False)
    assert client.get("/register").status_code == 302
    assert client.post("/register", data={"username": "newbie"}).status_code == 302
    assert User.query.filter_by(username="newbie").count() == 0


def test_login_off_blocks_normal_users_but_never_admins(app, world, client):
    save_settings(world.admin, login_enabled=False)
    res = client.post("/login", data={"identifier": "alice", "password": "secret123"})
    assert res.status_code == 200 and b"paused" in res.data
    assert client.post("/login", data={"identifier": "boss", "password": "secret123"}).status_code == 302


# ================================================================ maintenance
def test_maintenance_blocks_normal_users_pages_and_api_but_not_admins(app, world, client):
    save_settings(world.admin, maintenance_mode=True, maintenance_message="Back at noon")
    page = world.a.get("/messages")
    assert page.status_code == 503 and b"Back at noon" in page.data
    api = world.a.get("/api/chats")
    assert api.status_code == 503 and api.get_json()["error"]["code"] == "maintenance"
    assert world.admin.get("/admin").status_code == 200
    assert world.admin.get("/messages").status_code == 200
    assert client.get("/login").status_code == 200                      # the login page stays reachable


def test_maintenance_stops_normal_login_but_admin_can_sign_in(app, world, client):
    save_settings(world.admin, maintenance_mode=True)
    res = client.post("/login", data={"identifier": "alice", "password": "secret123"})
    assert res.status_code == 200 and b"maintenance" in res.data.lower()
    assert client.post("/login", data={"identifier": "boss", "password": "secret123"}).status_code == 302


def test_delivery_receipts_still_work_during_maintenance(app, world, client):
    save_settings(world.admin, maintenance_mode=True)
    assert client.post("/api/push/ack", json={"token": "x"}).status_code == 400      # not 503


def test_switching_maintenance_off_restores_the_site(app, world):
    save_settings(world.admin, maintenance_mode=True)
    assert world.a.get("/messages").status_code == 503
    save_settings(world.admin)
    assert world.a.get("/messages").status_code == 200


def test_site_notice_bar_is_shown_to_everyone(app, world):
    save_settings(world.admin, site_announcement="Planned break tonight")
    assert b"Planned break tonight" in world.a.get("/messages").data
    save_settings(world.admin)
    assert b"Planned break tonight" not in world.a.get("/messages").data


# ================================================================ push switch
def test_push_switch_is_reported_to_the_browser_and_blocks_subscribing(app, world):
    assert world.a.get("/api/push/config").get_json()["enabled"] is True
    save_settings(world.admin, push_enabled=False)
    assert world.a.get("/api/push/config").get_json()["enabled"] is False
    from app.services import push_service
    assert push_service._push_switch_on() is False


# ================================================================ timed suspension, notes, sign-out
def test_suspension_with_days_ends_by_itself(app, world):
    world.admin.post(f"/admin/users/{world.bob.id}/status", data={"status": "suspended", "days": "3"})
    bob = User.query.filter_by(username="bob").one()
    assert bob.status == "suspended" and bob.suspended_until is not None
    assert not bob.is_active
    bob.suspended_until = utcnow() - timedelta(minutes=1)
    db.session.commit()
    assert bob.is_active
    assert login_as(app, bob) is not None                 # can sign in again
    assert User.query.filter_by(username="bob").one().status == "active"
    assert User.query.filter_by(username="bob").one().suspended_until is None


def test_suspension_without_days_stays_until_lifted(app, world):
    world.admin.post(f"/admin/users/{world.bob.id}/status", data={"status": "suspended"})
    assert User.query.filter_by(username="bob").one().suspended_until is None
    assert not User.query.filter_by(username="bob").one().is_active
    world.admin.post(f"/admin/users/{world.bob.id}/status", data={"status": "active"})
    assert User.query.filter_by(username="bob").one().is_active


@pytest.mark.parametrize("days", ["0", "-1", "366", "abc"])
def test_bad_suspension_days_are_refused(app, world, days):
    world.admin.post(f"/admin/users/{world.bob.id}/status", data={"status": "suspended", "days": days})
    assert User.query.filter_by(username="bob").one().status == "active"


def test_finished_suspensions_are_cleaned_in_admin_lists(app, world):
    bob = User.query.filter_by(username="bob").one()
    bob.status, bob.suspended_until = "suspended", utcnow() - timedelta(hours=1)
    db.session.commit()
    assert b"@bob" not in world.admin.get("/admin/users?status=suspended").data
    assert User.query.filter_by(username="bob").one().status == "active"


def test_notes_are_saved_audited_without_text_and_visible_only_to_admins(app, world):
    assert world.admin.post(f"/admin/users/{world.bob.id}/notes", data={"note": "Warned about spam"}).status_code == 302
    assert ModerationNote.query.one().note == "Warned about spam"
    assert "Warned about spam" not in str(AdminAuditLog.query.filter_by(action="user.note").one().details)
    assert b"Warned about spam" in world.admin.get(f"/admin/users/{world.bob.id}").data
    assert world.a.post(f"/admin/users/{world.bob.id}/notes", data={"note": "hack"}).status_code == 403
    assert ModerationNote.query.count() == 1


def test_empty_or_huge_notes_are_refused(app, world):
    world.admin.post(f"/admin/users/{world.bob.id}/notes", data={"note": " "})
    world.admin.post(f"/admin/users/{world.bob.id}/notes", data={"note": "x" * 501})
    assert ModerationNote.query.count() == 0


def test_user_page_shows_moderation_history(app, world):
    world.admin.post(f"/admin/users/{world.bob.id}/status", data={"status": "banned"})
    assert b"user.status" in world.admin.get(f"/admin/users/{world.bob.id}").data


def test_force_logout_ends_every_session_of_that_person(app, world):
    assert world.b.get("/messages").status_code == 200
    world.admin.post(f"/admin/users/{world.bob.id}/force-logout")
    assert world.b.get("/messages").status_code == 302                      # sent to the login page
    assert login_as(app, User.query.filter_by(username="bob").one()) is not None      # password still works


def test_force_logout_does_not_touch_other_people_admins_or_self(app, world):
    world.admin.post(f"/admin/users/{world.bob.id}/force-logout")
    assert world.a.get("/messages").status_code == 200
    world.admin.post(f"/admin/users/{world.boss.id}/force-logout")
    assert world.admin.get("/admin").status_code == 200
    assert world.a.post(f"/admin/users/{world.bob.id}/force-logout").status_code == 403


def test_normal_sessions_survive_deploy_because_epoch_zero_keeps_the_old_session_id(app, world):
    user = User.query.filter_by(username="alice").one()
    assert user.session_epoch == 0
    import hmac
    from hashlib import sha256
    old = hmac.new(app.config["SECRET_KEY"].encode(), user.password_hash.encode(), sha256).hexdigest()[:16]
    assert user.session_fingerprint() == old


def test_users_can_be_filtered_by_privacy(app, world):
    verified("hidden", is_public=False)
    html = world.admin.get("/admin/users?privacy=private").get_data(as_text=True)
    assert "@hidden" in html and "@alice" not in html


# ================================================================ announcements
def make_announcement(w):
    w.admin.post("/admin/announcements", data={"title": "Hello all", "body": "First text"})
    return Announcement.query.one()


def test_announcement_can_be_edited(app, world):
    ann = make_announcement(world)
    res = world.admin.post(f"/admin/announcements/{ann.id}/edit", data={"title": "New title", "body": "Second text"})
    assert res.status_code == 302
    assert Announcement.query.one().title == "New title"
    assert world.admin.get(f"/admin/announcements/{ann.id}/edit").status_code == 200


def test_invalid_announcement_edit_changes_nothing(app, world):
    ann = make_announcement(world)
    assert world.admin.post(f"/admin/announcements/{ann.id}/edit", data={"title": "x", "body": "y"}).status_code == 200
    assert Announcement.query.one().title == "Hello all"


def test_deleting_an_announcement_also_removes_it_from_notice_boxes(app, world):
    ann = make_announcement(world)
    assert Notification.query.filter_by(type="announcement").count() >= 2
    world.admin.post(f"/admin/announcements/{ann.id}/delete")
    assert Announcement.query.count() == 0 and Notification.query.filter_by(type="announcement").count() == 0
    assert world.a.get(f"/announcements/{ann.id}").status_code == 404
    assert AdminAuditLog.query.filter_by(action="announcement.delete").count() == 1


def test_normal_users_cannot_edit_or_delete_announcements(app, world):
    ann = make_announcement(world)
    assert world.a.post(f"/admin/announcements/{ann.id}/edit", data={"title": "Hacked!", "body": "Hacked!!"}).status_code == 403
    assert world.a.post(f"/admin/announcements/{ann.id}/delete").status_code == 403
    assert Announcement.query.one().title == "Hello all"


# ================================================================ dashboard
def test_dashboard_shows_push_numbers_and_recent_activity(app, world):
    save_settings(world.admin, voice_enabled=False)
    html = world.admin.get("/admin").get_data(as_text=True)
    for label in ("Push devices", "Phone receipts", "Unread notices", "settings.update"):
        assert label in html
