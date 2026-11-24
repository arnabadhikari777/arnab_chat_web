"""Android device registration API (Phase 1) and its separation from browser Web Push."""
import pytest

from app.extensions import db
from app.models import Notification, NativeDevice, PushSubscription
from app.services import push_service
from tests.conftest import login_as, verified

TOKEN = "fcm:" + "T" * 60
DID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture()
def me(app):
    user = verified("alice")
    return user, login_as(app, user)


def reg(c, **kw):
    body = {"device_id": DID, "platform": "android", "push_token": TOKEN, "app_version": "1.0.0",
            "os_version": "14", "device_model": "Pixel 7"}
    body.update(kw)
    return c.post("/api/devices", json=body)


def test_login_required(client):
    assert client.post("/api/devices", json={}).status_code == 401
    assert client.get("/api/devices").status_code == 401
    assert client.get("/api/devices/config").status_code == 401
    assert client.delete(f"/api/devices/{DID}").status_code == 401


def test_register_creates_then_updates_and_never_returns_the_token(me):
    user, c = me
    r = reg(c)
    assert r.status_code == 201 and r.json["created"] is True
    assert TOKEN not in r.get_data(as_text=True)
    assert r.json["device"]["has_push_token"] is True
    r2 = reg(c, app_version="1.0.1")
    assert r2.status_code == 200 and r2.json["created"] is False
    assert NativeDevice.query.count() == 1
    assert NativeDevice.query.one().app_version == "1.0.1"
    assert TOKEN not in c.get("/api/devices").get_data(as_text=True)


def test_token_rotation_keeps_one_row(me):
    _, c = me
    reg(c)
    new = "fcm:" + "N" * 60
    r = c.patch(f"/api/devices/{DID}", json={"push_token": new})
    assert r.status_code == 200
    d = NativeDevice.query.one()
    assert d.push_token == new


def test_one_token_belongs_to_one_row(me):
    _, c = me
    reg(c)
    reg(c, device_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")      # same token, new install id
    rows = {d.device_id: d for d in NativeDevice.query.all()}
    assert rows[DID].push_token is None and rows[DID].status == "invalid"
    assert rows["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"].push_token == TOKEN


def test_install_moves_to_the_user_who_signs_in(app, me):
    _, a = me
    reg(a)
    bob = verified("bob")
    b = login_as(app, bob)
    reg(b)
    d = NativeDevice.query.one()
    assert d.user_id == bob.id
    assert a.get("/api/devices").json["devices"] == []


def test_other_users_cannot_update_or_delete_my_device(app, me):
    _, a = me
    reg(a)
    b = login_as(app, verified("bob"))
    assert b.patch(f"/api/devices/{DID}", json={"app_version": "9"}).status_code == 404
    assert b.delete(f"/api/devices/{DID}").json["removed"] == 0
    assert NativeDevice.query.count() == 1


def test_unregister_and_logout_remove_the_device(me):
    _, c = me
    reg(c)
    assert c.delete(f"/api/devices/{DID}").json["removed"] == 1
    reg(c)
    c.post("/logout")
    assert NativeDevice.query.count() == 0


@pytest.mark.parametrize("body", [
    {"device_id": "short"}, {"device_id": "bad id with spaces!!"},
    {"push_token": "x"}, {"push_token": "<script>" + "a" * 30},
    {"platform": "ios"}, {"app_version": "v" * 40}, {"device_model": "<b>x</b>"},
])
def test_bad_input_is_refused(me, body):
    _, c = me
    r = reg(c, **body)
    assert r.status_code == 400 and r.json["ok"] is False
    assert NativeDevice.query.count() == 0


def test_device_count_is_bounded(app, me):
    _, c = me
    app.config["NATIVE_MAX_DEVICES_PER_USER"] = 2
    for i in range(4):
        reg(c, device_id=f"device-number-{i}", push_token="fcm:" + str(i) * 40)
    assert NativeDevice.query.count() == 2


def test_switch_closes_registration(app, me):
    _, c = me
    app.config["NATIVE_DEVICES_ENABLED"] = False
    assert reg(c).status_code == 503
    assert c.get("/api/devices/config").json["enabled"] is False


def test_config_has_no_secrets(me):
    _, c = me
    j = c.get("/api/devices/config").json
    assert set(j) == {"ok", "enabled", "platforms", "push_delivery", "max_devices"}
    assert j["push_delivery"] is False


def test_csrf_is_enforced_when_enabled(app, me):
    _, c = me
    app.config["WTF_CSRF_ENABLED"] = True
    assert reg(c).status_code == 400          # no token -> CSRFError


def test_native_devices_and_web_push_are_separate_tables(app, me):
    user, c = me
    reg(c)
    assert PushSubscription.query.count() == 0
    c.post("/api/push/unsubscribe", json={})
    assert NativeDevice.query.count() == 1


def test_native_payload_has_no_message_text_and_no_secret(app, me):
    user, _ = me
    note = Notification(user_id=user.id, type="new_message", payload={"conversation_id": 1}, dedupe_key="msg:1")
    db.session.add(note); db.session.commit()
    with app.test_request_context():
        p = push_service.build_native_payload(note, user)
    assert p["tag"] == "msg:1" and p["notification_id"] == note.id and "renotify" not in p
    assert not any(k in p for k in ("push_token", "endpoint", "p256dh", "auth", "text", "message"))


def test_pages_load_the_platform_layer_first_and_stay_csp_clean(me):
    _, c = me
    r = c.get("/", follow_redirects=True)
    html = r.get_data(as_text=True)
    assert html.index("js/platform.js") < html.index("js/app.js") < html.index("js/calling.js") < html.index("js/device.js")
    assert "<script>" not in html and "onclick=" not in html
    csp = r.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    assert "camera=(self)" in r.headers["Permissions-Policy"] and "microphone=(self)" in r.headers["Permissions-Policy"]


def test_logged_out_pages_also_get_the_platform_layer(client):
    html = client.get("/login").get_data(as_text=True)
    assert "js/platform.js" in html and "js/device.js" not in html


def test_production_cookie_flags_are_unchanged(monkeypatch):
    from app.config import ProductionConfig
    assert ProductionConfig.SESSION_COOKIE_HTTPONLY is True
    assert ProductionConfig.SESSION_COOKIE_SAMESITE == "Lax"
    assert ProductionConfig.REMEMBER_COOKIE_HTTPONLY is True
