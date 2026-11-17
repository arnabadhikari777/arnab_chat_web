"""SSRF guard: the server only ever contacts real browser push services on public addresses."""
import pytest

from app.extensions import db
from app.models import PushSubscription
from app.services import push_endpoint, push_service
from tests.conftest import verified
from tests.test_push import configured, sub_json  # noqa: F401

PUBLIC = lambda host: ["142.250.1.1"]  # noqa: E731


@pytest.fixture()
def dns_on(app):
    app.config["PUSH_ENDPOINT_CHECK_DNS"] = True


@pytest.mark.parametrize("url", [
    "https://fcm.googleapis.com/fcm/send/abc",
    "https://updates.push.services.mozilla.com/wpush/v2/abc",
    "https://web.push.apple.com/abc",
    "https://wns2-par02p.notify.windows.com/?token=abc",
])
def test_real_push_services_are_accepted(app, dns_on, url):
    assert push_endpoint.check_endpoint(url, resolver=PUBLIC) == url


@pytest.mark.parametrize("url,code", [
    ("http://fcm.googleapis.com/x", "scheme"),
    ("https://127.0.0.1/x", "ip_literal"),
    ("https://[::1]/x", "ip_literal"),
    ("https://169.254.169.254/latest/meta-data", "ip_literal"),
    ("https://localhost/x", "host_not_allowed"),
    ("https://evil.example/x", "host_not_allowed"),
    ("https://fcm.googleapis.com.evil.example/x", "host_not_allowed"),
    ("https://evilfcm.googleapis.com/x", "host_not_allowed"),
    ("https://notfcm.googleapis.com@evil.example/x", "userinfo"),
    ("https://user:pw@fcm.googleapis.com/x", "userinfo"),
    ("https://fcm.googleapis.com:8443/x", "port"),
    ("https://fcm.googleapis.com/" + "a" * 1100, "length"),
    ("", "length"),
])
def test_dangerous_endpoints_are_rejected(app, dns_on, url, code):
    with pytest.raises(push_endpoint.EndpointRejected) as err:
        push_endpoint.check_endpoint(url, resolver=PUBLIC)
    assert err.value.code == code


@pytest.mark.parametrize("addresses", [
    ["10.0.0.5"], ["192.168.1.1"], ["172.16.0.9"], ["127.0.0.1"], ["169.254.169.254"], ["100.64.0.1"],
    ["::1"], ["fe80::1%eth0"], ["::ffff:127.0.0.1"], ["fc00::1"], ["0.0.0.0"], ["142.250.1.1", "127.0.0.1"], [],
])
def test_a_good_name_that_resolves_to_a_private_address_is_rejected(app, dns_on, addresses):
    with pytest.raises(push_endpoint.EndpointRejected) as err:
        push_endpoint.check_endpoint("https://fcm.googleapis.com/x", resolver=lambda host: addresses)
    assert err.value.code == "private_address"


def test_a_name_that_does_not_resolve_is_rejected(app, dns_on):
    def boom(host):
        raise OSError("no such host")
    with pytest.raises(push_endpoint.EndpointRejected) as err:
        push_endpoint.check_endpoint("https://fcm.googleapis.com/x", resolver=boom)
    assert err.value.code == "dns"


def test_extra_hosts_can_be_added_by_the_owner_only_through_config(app, dns_on):
    with pytest.raises(push_endpoint.EndpointRejected):
        push_endpoint.check_endpoint("https://push.myphonemaker.example/x", resolver=PUBLIC)
    app.config["PUSH_ENDPOINT_EXTRA_HOSTS"] = ["push.myphonemaker.example"]
    assert push_endpoint.check_endpoint("https://push.myphonemaker.example/x", resolver=PUBLIC)


@pytest.mark.parametrize("endpoint", ["https://127.0.0.1/x", "https://evil.example/x", "https://169.254.169.254/x"])
def test_subscribe_refuses_and_stores_nothing(app, configured, endpoint):
    from tests.conftest import login_as
    browser = login_as(app, verified("alice"))
    res = browser.post("/api/push/subscribe", json={"endpoint": endpoint, "keys": {"p256dh": "p" * 87, "auth": "a" * 22}})
    assert res.status_code == 400
    assert PushSubscription.query.count() == 0


def test_subscribe_still_accepts_a_real_endpoint(app, configured):
    from tests.conftest import login_as
    browser = login_as(app, verified("alice"))
    assert browser.post("/api/push/subscribe", json=sub_json(1)).status_code == 200
    assert PushSubscription.query.count() == 1


def test_sending_rechecks_the_endpoint_and_never_contacts_a_bad_one(app, configured, monkeypatch):
    """A row that is already in the database (saved before this guard existed) is still never contacted."""
    user = verified("alice")
    sub = PushSubscription(user_id=user.id, endpoint="https://169.254.169.254/latest/meta-data", p256dh="p", auth="a")
    db.session.add(sub)
    db.session.commit()
    called = []
    monkeypatch.setitem(__import__("sys").modules, "pywebpush",
                        type("M", (), {"webpush": lambda **k: called.append(k), "WebPushException": Exception}))
    assert push_service._send_one(sub, {"title": "x"}, "msg:1") == ("expired", "endpoint not allowed")
    assert called == []
