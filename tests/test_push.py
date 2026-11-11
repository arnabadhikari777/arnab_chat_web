"""Web Push, the Notice Box bell and offline recovery.

Real push services are never contacted: push_service._send_one is replaced by a recorder.
(Real delivery to a real phone can only be checked on the live site: see docs/PUSH_NOTIFICATIONS.md.)
"""
import re

import pytest

from app.extensions import db
from app.models import Conversation, Notification, PushDelivery, PushSubscription
from app.services import push_service, vapid
from tests.conftest import login_as, verified

PRIVATE, PUBLIC = vapid.generate_keypair()
EP = "https://fcm.googleapis.com/fcm/send/device-{}"


def sub_json(n=1):
    return {"endpoint": EP.format(n), "keys": {"p256dh": "p" * 87, "auth": "a" * 22}}


@pytest.fixture()
def configured(app):
    app.config.update(VAPID_PRIVATE_KEY=PRIVATE, VAPID_PUBLIC_KEY=PUBLIC, VAPID_CONTACT="mailto:owner@gmail.com")


@pytest.fixture()
def duo(app):
    alice, bob = verified("alice"), verified("bob")
    a, b = login_as(app, alice), login_as(app, bob)
    a.post("/u/bob/message")
    conv = Conversation.query.one()
    return type("Duo", (), dict(a=a, b=b, alice=alice, bob=bob, conv=conv.id, url=f"/api/conversations/{conv.id}"))


@pytest.fixture()
def sent(monkeypatch):
    """Record every push instead of sending it. Set sent.result to change the outcome."""
    calls = []
    state = type("S", (), {"calls": calls, "result": ("accepted", None)})

    def fake(sub, payload, group_key):
        calls.append({"sub": sub.id, "user": sub.user_id, "payload": payload, "topic": group_key})
        return state.result
    monkeypatch.setattr(push_service, "_send_one", fake)
    return state


def say(browser, duo, text, uuid):
    return browser.post(f"{duo.url}/messages", json={"uuid": uuid, "body": text})


# ================================================================ configuration endpoint
def test_config_reports_not_configured_and_hides_details_from_normal_people(app, duo):
    body = duo.a.get("/api/push/config").get_json()
    assert body["configured"] is False and body["publicKey"] is None
    assert "missing" not in body and "problems" not in body


def test_admin_sees_exactly_which_variables_are_missing(app):
    boss = verified("boss", role="admin")
    body = login_as(app, boss).get("/api/push/config").get_json()
    assert body["missing"] == ["VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY", "VAPID_CONTACT"]


def test_a_missing_contact_alone_means_not_configured(app, duo):
    app.config.update(VAPID_PRIVATE_KEY=PRIVATE, VAPID_PUBLIC_KEY=PUBLIC, VAPID_CONTACT="")
    assert duo.a.get("/api/push/config").get_json()["configured"] is False


def test_configured_returns_public_key_and_never_the_private_key(app, duo, configured):
    res = duo.a.get("/api/push/config")
    assert res.get_json() == {"ok": True, "configured": True, "publicKey": PUBLIC}
    assert PRIVATE not in res.get_data(as_text=True)
    assert res.headers["Cache-Control"] == "no-store"


def test_old_key_endpoint_still_answers(app, duo, configured):
    assert duo.a.get("/api/push/vapid-public-key").get_json()["publicKey"] == PUBLIC


def test_config_needs_login(client):
    assert client.get("/api/push/config").status_code in (302, 401)


# ================================================================ subscriptions
def test_subscribe_saves_json_body(app, duo, configured):
    res = duo.a.post("/api/push/subscribe", json=sub_json())
    assert res.status_code == 200 and res.get_json()["ok"]
    assert PushSubscription.query.one().user_id == duo.alice.id


def test_subscribing_twice_never_duplicates(app, duo, configured):
    duo.a.post("/api/push/subscribe", json=sub_json())
    duo.a.post("/api/push/subscribe", json=sub_json())
    assert PushSubscription.query.count() == 1


def test_subscribe_refuses_when_server_is_not_configured(app, duo):
    res = duo.a.post("/api/push/subscribe", json=sub_json())
    assert res.status_code == 503 and res.get_json()["error"]["code"] == "server_not_configured"
    assert PushSubscription.query.count() == 0


@pytest.mark.parametrize("payload", [{}, {"endpoint": "http://insecure/x", "keys": {"p256dh": "a", "auth": "b"}},
                                     {"endpoint": EP.format(1)}, {"endpoint": EP.format(1), "keys": {"p256dh": "a" * 300, "auth": "b"}}])
def test_bad_subscription_payloads_are_rejected_with_a_readable_message(app, duo, configured, payload):
    res = duo.a.post("/api/push/subscribe", json=payload)
    assert res.status_code == 400 and res.get_json()["error"]["message"]
    assert PushSubscription.query.count() == 0


def test_subscribe_without_json_content_type_is_a_clean_400(app, duo, configured):
    res = duo.a.post("/api/push/subscribe", data="{}")
    assert res.status_code == 400


def test_same_browser_moves_to_the_new_account(app, duo, configured):
    duo.a.post("/api/push/subscribe", json=sub_json())
    duo.b.post("/api/push/subscribe", json=sub_json())
    assert PushSubscription.query.one().user_id == duo.bob.id


def test_devices_per_person_are_capped(app, duo, configured):
    app.config["PUSH_MAX_DEVICES_PER_USER"] = 2
    for n in range(4):
        duo.a.post("/api/push/subscribe", json=sub_json(n))
    assert PushSubscription.query.filter_by(user_id=duo.alice.id).count() == 2


def test_unsubscribe_removes_only_that_device(app, duo, configured):
    duo.a.post("/api/push/subscribe", json=sub_json(1))
    duo.a.post("/api/push/subscribe", json=sub_json(2))
    duo.a.post("/api/push/unsubscribe", json={"endpoint": EP.format(1)})
    assert [s.endpoint for s in PushSubscription.query.all()] == [EP.format(2)]


def test_logout_removes_this_browsers_subscription(app, duo, configured):
    duo.a.post("/api/push/subscribe", json=sub_json())
    duo.a.post("/logout")
    assert PushSubscription.query.count() == 0


# ================================================================ delivery
def subscribe(duo, who="b", n=1):
    getattr(duo, who).post("/api/push/subscribe", json=sub_json(n))


def test_message_pushes_to_the_recipient_only(app, duo, configured, sent):
    subscribe(duo, "a", 1)          # alice (the sender) also has a device
    subscribe(duo, "b", 2)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    assert [c["user"] for c in sent.calls] == [duo.bob.id]


def test_second_message_in_the_same_chat_pushes_again_with_the_new_count(app, duo, configured, sent):
    """Regression: the old code skipped every later message because the first delivery was already 'accepted'."""
    subscribe(duo)
    say(duo.a, duo, "one", "uuid-0001-aaaa")
    say(duo.a, duo, "two", "uuid-0002-aaaa")
    assert len(sent.calls) == 2
    assert sent.calls[0]["payload"]["tag"] == sent.calls[1]["payload"]["tag"]      # grouped per chat
    assert PushDelivery.query.count() == 1                                          # one row per device, updated


def test_push_text_never_contains_the_private_message(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "my secret text", "uuid-0001-aaaa")
    assert "secret" not in str(sent.calls[0]["payload"])


def test_disabled_message_notifications_send_nothing(app, duo, configured, sent):
    subscribe(duo)
    duo.bob.notify_messages = False
    db.session.commit()
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    assert sent.calls == [] and Notification.query.count() == 0


def test_no_push_without_vapid_config_but_the_notice_and_message_still_exist(app, duo, sent):
    res = say(duo.a, duo, "hi", "uuid-0001-aaaa")
    assert res.status_code == 201 and sent.calls == []
    assert Notification.query.filter_by(user_id=duo.bob.id).count() == 1


def test_a_failing_push_never_breaks_sending(app, duo, configured, monkeypatch):
    subscribe(duo)
    monkeypatch.setattr(push_service, "_send_one", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    res = say(duo.a, duo, "hi", "uuid-0001-aaaa")
    assert res.status_code == 201


def test_gone_endpoint_is_marked_expired_and_pruned_on_next_subscribe(app, duo, configured, sent):
    subscribe(duo, "b", 1)
    sent.result = ("expired", "410")
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    assert PushSubscription.query.one().status == "expired"
    subscribe(duo, "b", 2)
    assert [s.endpoint for s in PushSubscription.query.all()] == [EP.format(2)]


def test_failed_delivery_is_retried_a_bounded_number_of_times(app, duo, configured, sent):
    subscribe(duo)
    sent.result = ("failed", "503")
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    before = len(sent.calls)
    for _ in range(10):
        duo.b.post("/api/push/sync")
    assert len(sent.calls) - before <= push_service.MAX_ATTEMPTS - 1


def test_sync_recovers_a_failed_delivery(app, duo, configured, sent):
    subscribe(duo)
    sent.result = ("failed", "503")
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    sent.result = ("accepted", None)
    assert duo.b.post("/api/push/sync").get_json()["accepted"] == 1
    assert PushDelivery.query.one().status == "accepted"


def test_sync_never_pushes_notices_that_were_not_attempted_before(app, duo, configured, sent):
    say(duo.a, duo, "hi", "uuid-0001-aaaa")          # bob has no device yet
    subscribe(duo)
    duo.b.post("/api/push/sync")
    assert sent.calls == []


def test_announcement_respects_the_preference(app, duo, configured, sent):
    subscribe(duo)
    duo.bob.notify_announcements = False
    n = Notification(user_id=duo.bob.id, type="announcement", payload={"announcement_id": 1}, link="/x", dedupe_key="a1")
    db.session.add(n)
    db.session.commit()
    assert push_service.enqueue_and_send(n) == 0 and sent.calls == []


# ================================================================ offline recovery & counters
def test_messages_sent_while_offline_are_kept_grouped_and_visible_on_return(app, duo, configured, sent):
    subscribe(duo)
    for i in range(3):
        say(duo.a, duo, f"m{i}", f"uuid-000{i}-aaaa")
    notes = Notification.query.filter_by(user_id=duo.bob.id).all()
    assert len(notes) == 1 and notes[0].payload["count"] == 3          # one grouped notice, not three
    history = duo.b.get(f"{duo.url}/messages").get_json()["messages"]
    assert [m["body"] for m in history] == ["m0", "m1", "m2"]            # nothing lost
    assert duo.b.get("/api/summary").get_json()["notices"] == 1


def test_opening_the_chat_clears_that_chats_notice_only(app, duo, configured):
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    other = Notification(user_id=duo.bob.id, type="welcome", payload={}, dedupe_key="welcome")
    db.session.add(other)
    db.session.commit()
    duo.b.post(f"{duo.url}/seen", json={})
    assert duo.b.get("/api/summary").get_json()["notices"] == 1        # welcome stays unread


def test_notice_box_marks_only_the_shown_ids_as_read(app, duo):
    ns = []
    for k in ("x", "y"):
        n = Notification(user_id=duo.bob.id, type="welcome", payload={}, dedupe_key=k)
        db.session.add(n); ns.append(n)
    db.session.commit()
    res = duo.b.post("/api/notices/mark-displayed", json={"ids": [ns[0].id]})
    assert res.get_json()["notices"] == 1


# ================================================================ navigation
PAGES = ["/messages", "/people", "/profile", "/settings", "/notices"]


@pytest.mark.parametrize("path", PAGES)
def test_bottom_nav_has_no_notice_box_item(app, duo, path):
    html = duo.a.get(path).get_data(as_text=True)
    nav = re.search(r'<nav class="bottom-nav".*?</nav>', html, re.S).group(0)
    assert "/notices" not in nav and 'data-badge="notices"' not in nav
    assert len(re.findall(r"<a ", nav)) == 4


@pytest.mark.parametrize("path", PAGES)
def test_exactly_one_notice_entry_the_top_right_bell(app, duo, path):
    html = duo.a.get(path).get_data(as_text=True)
    assert html.count('href="/notices"') == 1
    assert html.count('data-badge="notices"') == 1
    assert 'class="icon-btn topbar__notice"' in html


def test_bell_is_on_the_settings_subpages_too(app, duo):
    for path in ("/settings/profile", "/settings/password"):
        assert 'topbar__notice' in duo.a.get(path).get_data(as_text=True)


def test_notice_box_page_and_notices_still_work(app, duo):
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    html = duo.b.get("/notices").get_data(as_text=True)
    assert "notice-list" in html


def test_settings_push_card_has_no_hardcoded_error_codes(app, duo):
    html = duo.a.get("/settings").get_data(as_text=True)
    for code in ("not_configured", "no_sw", "save_failed"):
        assert code not in html
    assert "data-push-enable" in html and "data-push-status" in html
