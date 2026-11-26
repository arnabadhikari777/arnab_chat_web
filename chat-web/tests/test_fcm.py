"""Native Android push (FCM HTTP v1): credentials, OAuth, payloads, delivery rules, invalid tokens, call events.

Google is never contacted: ``fcm_service._post`` is replaced by a recorder. What these tests CANNOT prove is real delivery
to a real phone (needs a Firebase project and a device: see docs/PHASE3_REPORT.md).
"""
import base64
import json
import logging

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.extensions import db
from app.models import CallHistory, Conversation, NativeDevice
from app.services import calling_service, conversation_service, device_service, fcm_service, push_service
from app.utils.time import utcnow
from tests.conftest import login_as, verified

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = _KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
ACCOUNT = {"type": "service_account", "project_id": "goppo-test", "client_email": "fcm@goppo-test.iam.gserviceaccount.com",
           "private_key": PEM, "token_uri": "https://oauth2.googleapis.com/token"}
B64 = base64.b64encode(json.dumps(ACCOUNT).encode()).decode()


def tok(n):
    return f"fcm:{n}" + "X" * 40


class Google:
    """Fake Google. results[token] = (http status, body); default is success."""

    def __init__(self):
        self.oauth = []
        self.sends = []
        self.results = {}
        self.oauth_status = 200

    def post(self, url, *, headers=None, data=None, json_body=None, timeout=6.0):
        if url == fcm_service.TOKEN_URI:
            self.oauth.append(data)
            return (self.oauth_status, {"access_token": f"ya29.fake{len(self.oauth)}", "expires_in": 3600}) if self.oauth_status == 200 else (self.oauth_status, {"error": "invalid_grant"})
        assert url == "https://fcm.googleapis.com/v1/projects/goppo-test/messages:send"
        msg = json_body["message"]
        self.sends.append({"token": msg["token"], "data": msg["data"], "android": msg["android"], "auth": headers["Authorization"]})
        return self.results.get(msg["token"], (200, {"name": "projects/goppo-test/messages/1"}))

    def to(self, token):
        return [s for s in self.sends if s["token"] == token]

    def types(self):
        return [s["data"]["type"] for s in self.sends]


@pytest.fixture()
def google(app, monkeypatch):
    app.config.update(FCM_SERVICE_ACCOUNT_B64=B64, CALLING_HMAC_SECRET="s" * 40, CALLING_SERVER_URL="https://calls.example.com",
                      CALLING_RING_SECONDS=60)
    fcm_service.reset_token_cache()
    g = Google()
    monkeypatch.setattr(fcm_service, "_post", g.post)
    return g


def add_device(user, n, did=None):
    dev, _ = device_service.register(user, {"device_id": did or f"device-0000000{n}", "platform": "android", "push_token": tok(n)})
    return dev


@pytest.fixture()
def duo(app, google):
    alice, bob = verified("alice"), verified("bob")
    a, b = login_as(app, alice), login_as(app, bob)
    a.post("/u/bob/message")
    conv = Conversation.query.one()
    return type("Duo", (), dict(a=a, b=b, alice=alice, bob=bob, conv=conv.id, url=f"/api/conversations/{conv.id}"))


def say(browser, duo, text, uuid="11111111-1111-4111-8111-111111111111"):
    return browser.post(f"{duo.url}/messages", json={"uuid": uuid, "body": text})


def call(duo, kind="voice"):
    r = duo.a.post("/api/calling/initiate", json={"peer_id": duo.bob.id, "call_type": kind})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["call_id"]


# ================================================================ credentials and OAuth
def test_not_configured_means_no_network_and_an_honest_status(app, monkeypatch):
    calls = []
    monkeypatch.setattr(fcm_service, "_post", lambda *a, **k: calls.append(a) or (200, {}))
    assert fcm_service.configured() is False and "FCM_SERVICE_ACCOUNT" in fcm_service.status().reason
    alice, bob = verified("alice"), verified("bob")
    add_device(bob, 1)
    assert fcm_service.deliver_notification(None) == 0 and calls == []


@pytest.mark.parametrize("raw,why", [
    ("not json", "valid JSON"),
    (json.dumps({**ACCOUNT, "type": "authorized_user"}), "service-account"),
    (json.dumps({**ACCOUNT, "private_key": "nope"}), "PEM"),
    (json.dumps({**ACCOUNT, "token_uri": "https://evil.example/token"}), "OAuth"),
    (json.dumps({k: v for k, v in ACCOUNT.items() if k != "client_email"}), "client_email"),
])
def test_bad_service_accounts_are_rejected_without_echoing_secrets(app, raw, why):
    app.config.update(FCM_SERVICE_ACCOUNT_JSON=raw)
    st = fcm_service.status()
    assert st.configured is False and why in st.reason
    assert PEM[:40] not in st.reason and "evil.example" not in st.reason


def test_all_three_ways_to_supply_the_account(app, tmp_path):
    p = tmp_path / "sa.json"
    p.write_text(json.dumps(ACCOUNT))
    for cfg in ({"FCM_SERVICE_ACCOUNT_B64": B64}, {"FCM_SERVICE_ACCOUNT_JSON": json.dumps(ACCOUNT)}, {"FCM_SERVICE_ACCOUNT_FILE": str(p)}):
        app.config.update({"FCM_SERVICE_ACCOUNT_B64": "", "FCM_SERVICE_ACCOUNT_JSON": "", "FCM_SERVICE_ACCOUNT_FILE": "", **cfg})
        st = fcm_service.status()
        assert st.configured and st.project_id == "goppo-test"


def test_jwt_assertion_is_a_valid_rs256_token_for_google(app, google):
    acc, _ = fcm_service._account()
    jwt = fcm_service.make_assertion(acc, now=1_700_000_000)
    head, claims, sig = jwt.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    assert json.loads(base64.urlsafe_b64decode(pad(head))) == {"alg": "RS256", "typ": "JWT"}
    c = json.loads(base64.urlsafe_b64decode(pad(claims)))
    assert c == {"iss": ACCOUNT["client_email"], "scope": fcm_service.SCOPE, "aud": fcm_service.TOKEN_URI,
                 "iat": 1_700_000_000, "exp": 1_700_003_600}
    _KEY.public_key().verify(base64.urlsafe_b64decode(pad(sig)), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())


def test_access_token_is_cached_between_sends(app, google, duo):
    add_device(duo.bob, 1)
    say(duo.a, duo, "one", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    say(duo.a, duo, "two", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    assert len(google.sends) == 2 and len(google.oauth) == 1
    assert google.oauth[0]["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    assert all(s["auth"] == "Bearer ya29.fake1" for s in google.sends)


def test_oauth_failure_sends_nothing_and_never_raises(app, google, duo):
    google.oauth_status = 400
    add_device(duo.bob, 1)
    assert say(duo.a, duo, "hello").status_code in (200, 201)
    assert google.sends == []


def test_an_expired_cached_token_is_refreshed_once(app, google, duo):
    add_device(duo.bob, 1)
    google.results[tok(1)] = (401, {"error": {"status": "UNAUTHENTICATED"}})
    fcm_service.access_token(fcm_service._account()[0])
    say(duo.a, duo, "hi")
    assert len(google.oauth) == 2 and len(google.to(tok(1))) == 2          # retried once with the new token
    assert google.to(tok(1))[1]["auth"] == "Bearer ya29.fake2"


# ================================================================ message notifications
def test_message_goes_to_the_recipients_devices_only_without_private_text(app, google, duo):
    bob1, bob2, al = add_device(duo.bob, 1), add_device(duo.bob, 2), add_device(duo.alice, 3)
    r = say(duo.a, duo, "my secret bank pin is 4321")
    assert r.status_code in (200, 201)
    assert {s["token"] for s in google.sends} == {tok(1), tok(2)}            # not the sender's phone
    d = google.sends[0]["data"]
    assert d["type"] == "new_message" and d["conversation_id"] == str(duo.conv) and d["sender_name"]
    assert d["path"] == f"/chat/{duo.conv}" and d["count"] == "1" and d["ack"]
    assert "4321" not in json.dumps(google.sends) and "secret" not in json.dumps(google.sends)
    assert all(isinstance(v, str) for v in d.values())                       # FCM data values must be strings
    assert google.sends[0]["android"] == {"priority": "HIGH", "ttl": "86400s", "collapse_key": f"msg:{duo.conv}"}


def test_message_priority_can_be_normal_and_count_grows_in_the_same_chat(app, google, duo):
    app.config["FCM_MESSAGE_PRIORITY"] = "normal"
    add_device(duo.bob, 1)
    say(duo.a, duo, "a", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    say(duo.a, duo, "b", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    assert [s["data"]["count"] for s in google.sends] == ["1", "2"]
    assert {s["android"]["collapse_key"] for s in google.sends} == {f"msg:{duo.conv}"}      # the phone keeps ONE per chat
    assert google.sends[0]["android"]["priority"] == "NORMAL"


def test_the_receipt_token_in_the_payload_marks_the_message_delivered(app, google, duo):
    add_device(duo.bob, 1)
    say(duo.a, duo, "hi")
    ack = google.sends[0]["data"]["ack"]
    assert push_service.acknowledge_receipt(ack) is not None
    r = duo.b.post("/api/push/ack", json={"token": ack})                      # what the phone's app does, no cookie needed
    assert r.status_code == 200 and r.get_json()["ok"] is True


def test_preferences_and_the_admin_switch_are_respected(app, google, duo, monkeypatch):
    add_device(duo.bob, 1)
    duo.bob.notify_messages = False
    db.session.commit()
    say(duo.a, duo, "muted", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert google.sends == []
    duo.bob.notify_messages = True
    db.session.commit()
    monkeypatch.setattr(fcm_service, "_switch_on", lambda: False)
    say(duo.a, duo, "switch off", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    assert google.sends == []


def test_a_native_failure_never_breaks_sending_a_message(app, google, duo, monkeypatch):
    add_device(duo.bob, 1)
    monkeypatch.setattr(fcm_service, "_post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert say(duo.a, duo, "still saved").status_code in (200, 201)


def test_web_push_and_native_push_are_separate_channels(app, google, duo, monkeypatch):
    from app.models import PushSubscription
    from app.services import vapid

    priv, pub = vapid.generate_keypair()
    app.config.update(VAPID_PRIVATE_KEY=priv, VAPID_PUBLIC_KEY=pub, VAPID_CONTACT="mailto:o@gmail.com")
    web = []
    monkeypatch.setattr(push_service, "_send_one", lambda sub, payload, gk: web.append(sub.id) or ("accepted", None))
    duo.b.post("/api/push/subscribe", json={"endpoint": "https://fcm.googleapis.com/fcm/send/dev-1", "keys": {"p256dh": "p" * 87, "auth": "a" * 22}})
    add_device(duo.bob, 1)
    say(duo.a, duo, "both")
    assert len(web) == 1 and len(google.sends) == 1                           # once per channel, never twice on one channel


# ================================================================ devices and invalid tokens
def test_a_dead_token_is_invalidated_and_cleared_while_other_devices_still_get_it(app, google, duo, caplog):
    bad, good = add_device(duo.bob, 1), add_device(duo.bob, 2)
    google.results[tok(1)] = (404, {"error": {"status": "UNREGISTERED", "message": "gone"}})
    with caplog.at_level(logging.DEBUG):
        say(duo.a, duo, "x")
    db.session.refresh(bad), db.session.refresh(good)
    assert bad.status == "invalid" and bad.push_token is None and bad.token_hash is None
    assert good.status == "active" and good.last_success_at is not None and good.failure_count == 0
    assert tok(1) not in caplog.text and tok(2) not in caplog.text and "ya29" not in caplog.text and PEM[:30] not in caplog.text
    say(duo.a, duo, "y", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    assert len(google.to(tok(1))) == 1 and len(google.to(tok(2))) == 2        # the dead one is not tried again


@pytest.mark.parametrize("status", ["INVALID_ARGUMENT", "SENDER_ID_MISMATCH"])
def test_other_permanent_errors_also_invalidate(app, google, duo, status):
    dev = add_device(duo.bob, 1)
    google.results[tok(1)] = (400, {"error": {"status": status}})
    say(duo.a, duo, "x")
    db.session.refresh(dev)
    assert dev.status == "invalid" and dev.push_token is None


def test_temporary_failures_are_counted_not_deleted_until_the_limit(app, google, duo):
    app.config["FCM_MAX_FAILURES"] = 3
    dev = add_device(duo.bob, 1)
    google.results[tok(1)] = (503, {"error": {"status": "UNAVAILABLE"}})
    for i in range(2):
        say(duo.a, duo, "m", f"{i}{i}{i}{i}{i}{i}{i}{i}-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    db.session.refresh(dev)
    assert dev.status == "active" and dev.failure_count == 2 and dev.push_token
    say(duo.a, duo, "m", "ccccccc3-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    db.session.refresh(dev)
    assert dev.status == "invalid" and dev.push_token is None
    google.results[tok(1)] = (200, {})
    device_service.register(duo.bob, {"device_id": dev.device_id, "platform": "android", "push_token": tok(1)})   # app re-registers
    db.session.refresh(dev)
    assert dev.status == "active" and dev.failure_count == 0


def test_success_resets_the_failure_counter(app, google, duo):
    dev = add_device(duo.bob, 1)
    dev.failure_count = 4
    db.session.commit()
    say(duo.a, duo, "x")
    db.session.refresh(dev)
    assert dev.failure_count == 0 and dev.last_success_at is not None


def test_signing_in_as_someone_else_on_the_same_install_moves_the_device(app, google, duo):
    add_device(duo.alice, 1, did="same-install-0001")
    add_device(duo.bob, 1, did="same-install-0001")                            # same install, other account
    say(duo.a, duo, "for bob")
    assert NativeDevice.query.count() == 1 and len(google.sends) == 1
    say(duo.b, duo, "for alice", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    assert len(google.sends) == 1                                              # alice no longer has a registered phone


def test_logout_stops_native_push_for_that_install(app, google, duo):
    duo.b.post("/api/devices", json={"device_id": "install-bob-0001", "platform": "android", "push_token": tok(9)})
    duo.b.get("/logout") if duo.b.get("/logout").status_code != 405 else duo.b.post("/logout")
    say(duo.a, duo, "after logout")
    assert google.sends == []


def test_tokens_never_appear_in_api_responses(app, google, duo):
    r = duo.b.post("/api/devices", json={"device_id": "install-bob-0001", "platform": "android", "push_token": tok(9)})
    assert tok(9) not in r.get_data(as_text=True)
    assert tok(9) not in duo.b.get("/api/devices").get_data(as_text=True)
    assert duo.b.get("/api/devices/config").get_json()["push_delivery"] is True


# ================================================================ incoming calls
def test_incoming_call_is_a_high_priority_short_lived_data_message(app, google, duo):
    add_device(duo.bob, 1), add_device(duo.bob, 2)
    call_id = call(duo, "video")
    assert len(google.sends) == 2 and {s["token"] for s in google.sends} == {tok(1), tok(2)}
    s = google.sends[0]
    assert s["android"] == {"priority": "HIGH", "ttl": "60s", "collapse_key": f"call:{call_id}"}
    d = s["data"]
    assert d["type"] == "incoming_call" and d["call_id"] == call_id and d["call_type"] == "video"
    assert d["caller_name"] and d["caller_id"] == str(duo.alice.id) and d["path"].startswith(f"/chat/{duo.conv}")
    assert int(d["expires_at"]) > int(d["sent_at"]) and "notification" not in json.dumps(s["android"])   # data-only


def test_one_call_is_announced_once_per_device_even_if_the_notice_is_replayed(app, google, duo):
    add_device(duo.bob, 1)
    call_id = call(duo)
    assert len(google.sends) == 1
    from app.models import Notification
    note = Notification.query.filter_by(dedupe_key=f"call:{call_id}").one()
    # An old notification is never replayed as a ringing phone (same 90 s rule as Web Push).
    note.created_at = utcnow().replace(year=2000)
    db.session.commit()
    assert fcm_service.deliver_notification(note) == 0 and len(google.sends) == 1


def test_calls_to_people_without_an_android_app_do_not_touch_google(app, google, duo):
    call(duo)
    assert google.sends == [] and google.oauth == []


def test_caller_cancelling_stops_the_ringing_phones(app, google, duo):
    add_device(duo.bob, 1)
    call_id = call(duo)
    google.sends.clear()
    assert duo.a.post("/api/calling/status", json={"call_id": call_id, "status": "cancelled"}).status_code == 200
    assert google.types() == ["call_cancelled"] and google.sends[0]["data"]["reason"] == "cancelled"
    assert google.sends[0]["android"]["priority"] == "HIGH"


def test_answering_on_one_phone_tells_the_others_to_stop(app, google, duo):
    add_device(duo.bob, 1), add_device(duo.bob, 2)
    call_id = call(duo)
    google.sends.clear()
    assert duo.b.post("/api/calling/accept", json={"call_id": call_id}).status_code == 200
    assert google.types() == ["call_cancelled", "call_cancelled"] and {s["data"]["reason"] for s in google.sends} == {"answered_elsewhere"}
    google.sends.clear()
    duo.b.post("/api/calling/accept", json={"call_id": call_id})                # a repeat does not announce again
    assert google.sends == []


def test_an_unanswered_call_becomes_missed_and_native_gets_both_events(app, google, duo):
    add_device(duo.bob, 1)
    call_id = call(duo)
    google.sends.clear()
    rec = CallHistory.query.filter_by(call_id=call_id).one()
    rec.started_at = utcnow().replace(year=2000)
    db.session.commit()
    with app.test_request_context():                  # in production this always runs inside a request (url_for)
        calling_service.expire_stale((duo.bob.id,))
    assert sorted(google.types()) == ["call_cancelled", "missed_call"]
    missed = next(s for s in google.sends if s["data"]["type"] == "missed_call")["data"]
    assert missed["call_id"] == call_id and missed["caller_name"] and missed["body"]
    google.sends.clear()
    with app.test_request_context():
        calling_service.expire_stale((duo.bob.id,))
    assert google.sends == []                                                  # one missed call = one announcement


# ================================================================ authorization (server side validation of Accept)
def test_only_the_callee_can_accept_and_stale_calls_cannot_be_joined(app, google, duo):
    carol = verified("carol")
    conversation_service.get_or_create(duo.alice.id, carol.id)
    db.session.commit()
    c = login_as(app, carol)
    add_device(duo.bob, 1)
    call_id = call(duo)
    google.sends.clear()
    assert c.post("/api/calling/accept", json={"call_id": call_id}).status_code == 404         # a stranger
    assert duo.a.post("/api/calling/accept", json={"call_id": call_id}).status_code == 404      # the caller is not the receiver
    assert c.post("/api/calling/token", json={"call_id": call_id}).status_code == 404
    assert c.get(f"/api/calling/state/{call_id}").status_code == 404
    assert google.sends == []                                                                    # none of that announced anything
    duo.a.post("/api/calling/status", json={"call_id": call_id, "status": "cancelled"})
    r = duo.b.post("/api/calling/accept", json={"call_id": call_id})                            # stale notification
    assert r.status_code == 409 and r.get_json()["call_status"] == "cancelled"
    assert duo.b.post("/api/calling/token", json={"call_id": call_id}).status_code == 409


def test_an_expired_invitation_cannot_be_replayed(app, google, duo):
    add_device(duo.bob, 1)
    call_id = call(duo)
    rec = CallHistory.query.filter_by(call_id=call_id).one()
    rec.started_at = utcnow().replace(year=2000)
    db.session.commit()
    r = duo.b.post("/api/calling/accept", json={"call_id": call_id})
    assert r.status_code == 409 and r.get_json()["call_status"] == "missed"


def test_the_native_state_check_only_needs_a_session_cookie(app, google, duo):
    """The Android app validates a call with GET /state before accepting; GET needs no CSRF token."""
    call_id = call(duo)
    r = duo.b.get(f"/api/calling/state/{call_id}")
    assert r.status_code == 200 and r.get_json()["call"]["status"] in ("initiated", "ringing")
    assert r.get_json()["call"]["call_id"] == call_id
