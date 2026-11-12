"""Verified delivery: a message becomes Delivered only after the recipient's service worker acknowledges the push.

Real push services are never contacted (push_service._send_one is replaced by a recorder, as in test_push.py).
"""
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import Message, Notification, PushDelivery, PushSubscription, User
from app.services import push_service
from app.utils.time import utcnow
from tests.test_push import configured, duo, say, sent, sub_json  # noqa: F401  (fixtures + helpers)


def subscribe(duo, who="b", n=1):
    """Register device number n for alice (who='a') or bob (who='b', the recipient)."""
    res = getattr(duo, who).post("/api/push/subscribe", json=sub_json(n))
    assert res.status_code == 200, res.get_data(as_text=True)


def ack(app, body):
    """The service worker has no session and no CSRF token: a bare client."""
    return app.test_client().post("/api/push/ack", json=body)


def endpoint_of(sub_id):
    return db.session.get(PushSubscription, sub_id).endpoint


def good(sent, n=-1):
    c = sent.calls[n]
    return {"id": c["payload"]["ack"]["id"], "token": c["payload"]["ack"]["token"], "endpoint": endpoint_of(c["sub"])}


def status():
    db.session.expire_all()
    return Message.query.order_by(Message.id).first().status


def make_online(user, online=True):
    user.last_seen_at = utcnow() if online else utcnow() - timedelta(hours=2)
    db.session.commit()


# ------------------------------------------------------------------ 1/2: sent, even when the push service accepted it
def test_offline_recipient_stays_single_tick(app, duo, configured, sent):
    subscribe(duo)
    make_online(duo.bob, False)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    assert status() == "sent"


def test_push_accepted_but_never_acknowledged_stays_sent(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    row = PushDelivery.query.one()
    assert row.status == "accepted" and row.acked_at is None
    assert status() == "sent"


def test_push_payload_has_a_token_but_the_database_only_has_its_hash(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    block = sent.calls[0]["payload"]["ack"]
    row = PushDelivery.query.one()
    assert block["id"] == row.id and block["token"] not in (row.ack_token_hash or "")
    assert len(row.ack_token_hash) == 64 and row.ack_expires_at > utcnow()


# ------------------------------------------------------------------ 3: valid ack while online
def test_valid_ack_while_online_makes_it_delivered(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    make_online(duo.bob)
    res = ack(app, good(sent))
    assert res.status_code == 200 and res.get_json() == {"ok": True}
    assert status() == "delivered"
    assert Message.query.one().delivered_at is not None


def test_ack_covers_only_messages_that_existed_when_the_push_was_sent(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "one", "uuid-0001-aaaa")
    first = good(sent)
    # a second message: the same notice is re-sent with a NEW token, so the old token is dead
    say(duo.a, duo, "two", "uuid-0002-aaaa")
    make_online(duo.bob)
    assert ack(app, first).status_code == 400
    assert [m.status for m in Message.query.order_by(Message.id)] == ["sent", "sent"]
    assert ack(app, good(sent)).status_code == 200
    assert [m.status for m in Message.query.order_by(Message.id)] == ["delivered", "delivered"]


def test_a_newer_send_replaces_the_token_even_if_that_send_failed(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "one", "uuid-0001-aaaa")
    old = good(sent)
    sent.result = ("failed", "boom")                  # the next push never leaves the server
    say(duo.a, duo, "two", "uuid-0002-aaaa")
    make_online(duo.bob)
    assert PushDelivery.query.one().message_upto_id == 2
    assert ack(app, old).status_code == 400           # an old token can never be replayed
    assert status() == "sent"


# ------------------------------------------------------------------ 4/5: invalid and duplicate
def test_invalid_acknowledgements_change_nothing(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    make_online(duo.bob)
    g = good(sent)
    bad = [
        {}, {"id": g["id"]}, {"id": "1", "token": g["token"], "endpoint": g["endpoint"]},
        {"id": True, "token": g["token"], "endpoint": g["endpoint"]},
        {**g, "token": "x" * 43}, {**g, "token": "short"}, {**g, "id": g["id"] + 99},
        {**g, "endpoint": "https://attacker.example/x"}, {**g, "endpoint": ""},
    ]
    for body in bad:
        res = ack(app, body)
        assert res.status_code == 400 and res.get_json() == {"ok": False}      # always the same answer
    assert status() == "sent"
    assert PushDelivery.query.one().acked_at is None


def test_non_json_body_is_refused(app, duo):
    res = app.test_client().post("/api/push/ack", data="nope", content_type="text/plain")
    assert res.status_code == 400


def test_duplicate_and_replayed_acks_do_not_corrupt_anything(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    make_online(duo.bob)
    g = good(sent)
    assert ack(app, g).status_code == 200
    first_time = Message.query.one().delivered_at
    assert ack(app, g).status_code == 400            # token is single-use
    assert ack(app, g).status_code == 400
    assert status() == "delivered" and Message.query.one().delivered_at == first_time


def test_ack_never_moves_a_seen_message_backwards(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    duo.b.post(f"{duo.url}/seen", json={})
    make_online(duo.bob)
    ack(app, good(sent))
    assert status() == "seen"


def test_ack_works_without_cookies_and_needs_no_csrf(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    app.config["WTF_CSRF_ENABLED"] = True              # from here on a normal POST would need a CSRF token
    assert app.test_client().post("/api/push/ack", json=good(sent)).status_code == 200


def test_another_persons_device_cannot_use_the_token(app, duo, configured, sent):
    subscribe(duo, "b", 1)
    subscribe(duo, "a", 2)                            # alice's own device
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    g = good(sent)
    other = db.session.query(PushSubscription).filter_by(user_id=duo.alice.id).one().endpoint
    make_online(duo.bob)
    assert ack(app, {**g, "endpoint": other}).status_code == 400
    assert status() == "sent"


# ------------------------------------------------------------------ 6: expired / inactive
def test_expired_token_is_refused(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    make_online(duo.bob)
    row = PushDelivery.query.one()
    row.ack_expires_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    assert ack(app, good(sent)).status_code == 400 and status() == "sent"


def test_ack_for_an_expired_subscription_is_refused(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    make_online(duo.bob)
    g = good(sent)
    db.session.get(PushSubscription, sent.calls[0]["sub"]).status = "expired"
    db.session.commit()
    assert ack(app, g).status_code == 400 and status() == "sent"


def test_push_service_410_marks_subscription_expired_and_message_stays_sent(app, duo, configured, sent):
    subscribe(duo)
    sent.result = ("expired", "410 Gone")
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    assert PushSubscription.query.one().status == "expired"
    assert PushDelivery.query.one().status == "expired" and status() == "sent"


def test_suspended_recipient_cannot_acknowledge(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    g = good(sent)
    duo.bob.status = "suspended"
    db.session.commit()
    assert ack(app, g).status_code == 400


# ------------------------------------------------------------------ 7: multiple devices
def test_one_acknowledging_device_is_enough_and_the_other_changes_nothing(app, duo, configured, sent):
    subscribe(duo, "b", 1)
    subscribe(duo, "b", 2)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    assert PushDelivery.query.count() == 2            # one record per device, no duplicates
    make_online(duo.bob)
    assert ack(app, good(sent, 0)).status_code == 200
    assert status() == "delivered"
    t = Message.query.one().delivered_at
    assert ack(app, good(sent, 1)).status_code == 200  # second device also valid: harmless
    assert status() == "delivered" and Message.query.one().delivered_at == t


# ------------------------------------------------------------------ 8: acknowledged while offline, applied when back online
def test_ack_while_offline_is_recorded_but_applied_only_when_online_again(app, duo, configured, sent):
    subscribe(duo)
    make_online(duo.bob, False)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    assert ack(app, good(sent)).status_code == 200    # the phone woke up and the worker acknowledged
    assert PushDelivery.query.one().acked_at is not None
    assert status() == "sent"                         # but the person is not online yet
    duo.b.get("/api/summary")                         # the app opens: they are online now
    assert status() == "delivered"


def test_pending_ack_is_applied_once(app, duo, configured, sent):
    subscribe(duo)
    make_online(duo.bob, False)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    ack(app, good(sent))
    duo.b.get("/api/summary")
    row = PushDelivery.query.one()
    assert row.applied_upto_id == row.acked_upto_id
    assert push_service.apply_pending_acks(duo.bob.id) == 0


# ------------------------------------------------------------------ 9: grouping and retries
def test_offline_messages_stay_one_grouped_notice_with_one_topic(app, duo, configured, sent):
    subscribe(duo)
    for i in range(3):
        say(duo.a, duo, f"m{i}", f"uuid-000{i}-aaaa")
    assert Notification.query.filter_by(user_id=duo.bob.id).count() == 1
    assert PushDelivery.query.count() == 1
    assert len({c["topic"] for c in sent.calls}) == 1


def test_retry_of_a_failed_push_gets_a_fresh_token(app, duo, configured, sent):
    subscribe(duo)
    sent.result = ("failed", "network")
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    old = sent.calls[0]["payload"]["ack"]["token"]
    sent.result = ("accepted", None)
    duo.b.post("/api/push/sync")
    new = sent.calls[-1]["payload"]["ack"]["token"]
    assert old != new and PushDelivery.query.count() == 1


# ------------------------------------------------------------------ 10: nothing else regressed
def test_seen_still_works(app, duo, configured, sent):
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    duo.b.post(f"{duo.url}/seen", json={})
    assert status() == "seen"


def test_open_chat_reporting_displayed_messages_still_marks_delivered(app, duo):
    mid = say(duo.a, duo, "hi", "uuid-0001-aaaa").get_json()["message"]["id"]
    duo.b.post(f"{duo.url}/delivered", json={"ids": [mid]})
    assert status() == "delivered"
