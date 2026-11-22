"""A saved message stays saved, whatever happens to its push; and the ticks only move on real proof.

Covers the repair of: _after_send() calling a push function that did not exist (500 + a red "failed" bubble for a
message that WAS saved), send_media() deleting the Cloudinary file of a committed message, and the missing
push_service functions (notify_after_commit / enqueue_and_send / sync_pending_for_user).
Real push services are never contacted (push_service._send_one is replaced by a recorder, see test_push.py).
"""
import pytest

from app.extensions import db
from app.models import Message, MessageAttachment, Notification, PushDelivery, PushSubscription
from app.realtime import get_backend
from app.services import push_service
from app.utils.time import utcnow
from datetime import timedelta
from tests.conftest import verified  # noqa: F401
from tests.test_messaging import PNG, file_post
from tests.test_push import configured, duo, say, sent, sub_json  # noqa: F401  (fixtures + helpers)


def subscribe(duo, n=1, who="b"):
    res = getattr(duo, who).post("/api/push/subscribe", json=sub_json(n))
    assert res.status_code == 200, res.get_data(as_text=True)


def token(sent, n=-1):
    return sent.calls[n]["payload"]["ack"]


def ack(app, tok):
    return app.test_client().post("/api/push/ack", json={"token": tok})


def statuses():
    db.session.expire_all()
    return [m.status for m in Message.query.order_by(Message.id)]


def boom(*a, **k):
    raise RuntimeError("push exploded")


# ================================================================ 1. a saved message is never reported as failed
def test_push_function_crashing_still_returns_success_and_keeps_the_message(app, duo, configured, monkeypatch):
    subscribe(duo)
    monkeypatch.setattr(push_service, "notify_after_commit", boom)
    res = say(duo.a, duo, "hello", "uuid-0001-aaaa")
    assert res.status_code == 201 and res.get_json()["ok"] is True
    assert res.get_json()["message"]["status"] == "sent"
    assert Message.query.count() == 1


def test_notice_creation_crashing_still_keeps_the_message(app, duo, monkeypatch):
    from app.services import notification_service
    monkeypatch.setattr(notification_service, "upsert_message_notice", boom)
    res = say(duo.a, duo, "hello", "uuid-0001-aaaa")
    assert res.status_code == 201 and Message.query.count() == 1


def test_realtime_publish_crashing_still_keeps_the_message(app, duo, monkeypatch):
    monkeypatch.setattr(get_backend(), "publish", boom)
    res = say(duo.a, duo, "hello", "uuid-0001-aaaa")
    assert res.status_code == 201 and Message.query.count() == 1


def test_push_service_down_message_sending_works_and_stays_single_tick(app, duo, configured, sent):
    subscribe(duo)
    sent.result = ("failed", "503")
    res = say(duo.a, duo, "hello", "uuid-0001-aaaa")
    assert res.status_code == 201 and statuses() == ["sent"]
    assert PushDelivery.query.one().status == "failed"


def test_notify_after_commit_exists_and_never_raises(app, duo, configured, monkeypatch):
    note = Notification(user_id=duo.bob.id, type="announcement", payload={"announcement_id": 1}, link="/x", dedupe_key="a9")
    db.session.add(note)
    db.session.commit()
    monkeypatch.setattr(push_service, "enqueue_and_send", boom)
    assert push_service.notify_after_commit(note) == 0
    assert push_service.notify_after_commit(None) == 0


# ================================================================ 2. images survive a push failure
def test_image_stays_in_the_cloud_and_in_the_chat_when_the_push_crashes(app, duo, configured, fake_services, monkeypatch):
    subscribe(duo)
    monkeypatch.setattr(push_service, "notify_after_commit", boom)
    res = file_post(duo.a, duo, PNG, "photo.png")
    assert res.status_code == 201, res.get_json()
    assert fake_services.deleted == []                         # the committed message keeps its Cloudinary file
    att = MessageAttachment.query.one()
    assert att.public_id == "pid1" and att.resource_type == "image" and att.mime_type.startswith("image/")
    assert att.url.startswith("https://res.cloudinary.com/")


def test_sender_and_receiver_both_get_the_image_after_a_refresh(app, duo, configured, sent):
    subscribe(duo)
    file_post(duo.a, duo, PNG, "photo.png")
    for browser in (duo.a, duo.b):
        m = browser.get(f"{duo.url}/messages").get_json()["messages"][0]
        assert m["type"] == "image" and m["att"]["url"].startswith("https://res.cloudinary.com/")
        assert m["att"]["mime"].startswith("image/")
    polled = duo.b.get(f"{duo.url}/poll?after=0").get_json()["messages"][0]
    assert polled["att"]["url"].startswith("https://res.cloudinary.com/")


def test_double_tap_on_an_image_makes_one_message_and_one_upload(app, duo, configured, sent, fake_services):
    subscribe(duo)
    file_post(duo.a, duo, PNG, "photo.png")
    again = file_post(duo.a, duo, PNG, "photo.png")
    assert again.get_json()["created"] is False
    assert Message.query.count() == 1 and len(fake_services.uploads) == 1 and len(sent.calls) == 1


# ================================================================ 3. idempotency / no duplicates
def test_same_uuid_twice_is_one_message_one_notice_one_push(app, duo, configured, sent):
    subscribe(duo)
    assert say(duo.a, duo, "hi", "uuid-0001-aaaa").status_code == 201
    again = say(duo.a, duo, "hi", "uuid-0001-aaaa")
    assert again.status_code == 200 and again.get_json()["created"] is False
    assert Message.query.count() == 1 and len(sent.calls) == 1
    assert Notification.query.filter_by(user_id=duo.bob.id).one().payload["count"] == 1


# ================================================================ 4. the ticks
def test_offline_device_stays_single_tick_then_double_after_it_returns_and_confirms(app, duo, configured, sent):
    subscribe(duo)
    sent.result = ("failed", "phone is off")
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    assert statuses() == ["sent"]
    sent.result = ("accepted", None)                            # the phone is back: the retry is accepted ...
    assert duo.b.post("/api/push/sync").get_json()["accepted"] == 1
    assert statuses() == ["sent"]                               # ... but "accepted" is NOT delivered
    assert ack(app, token(sent)).get_json()["updated"] == 1     # only the phone's receipt is
    assert statuses() == ["delivered"]
    assert duo.a.get(f"{duo.url}/messages").get_json()["messages"][0]["status"] == "delivered"


def test_any_one_of_several_devices_confirming_is_enough(app, duo, configured, sent):
    subscribe(duo, 1)
    subscribe(duo, 2)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    assert statuses() == ["sent"]
    assert ack(app, token(sent, 1)).get_json()["updated"] == 1
    assert statuses() == ["delivered"]
    assert ack(app, token(sent, 0)).get_json()["updated"] == 0  # the other device later: nothing changes, no error


def test_a_receipt_never_moves_a_seen_message_back(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    tok = token(sent)
    duo.b.post(f"{duo.url}/seen", json={})
    assert statuses() == ["seen"]
    assert ack(app, tok).get_json() == {"ok": True, "updated": 0}
    assert statuses() == ["seen"]


def test_reading_gives_blue_tick_even_if_the_phone_never_confirmed(app, duo, configured, sent):
    subscribe(duo)
    sent.result = ("failed", "503")
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    duo.b.post(f"{duo.url}/seen", json={})
    assert statuses() == ["seen"]


def test_a_receipt_of_another_person_is_refused(app, duo, configured, sent):
    """The token is bound to Bob. Re-signing it for Alice (the sender) must not touch Bob's messages."""
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    forged = push_service._ack_serializer().dumps({"u": duo.alice.id, "c": duo.conv, "m": 1, "n": 1, "s": None})
    ack(app, forged)
    assert statuses() == ["sent"]


# ================================================================ 5. queue, retry, cleanup
def test_sync_does_not_retry_notices_the_person_already_read(app, duo, configured, sent):
    subscribe(duo)
    sent.result = ("failed", "503")
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    duo.b.post(f"{duo.url}/seen", json={})                       # the chat notice is cleared
    before = len(sent.calls)
    duo.b.post("/api/push/sync")
    assert len(sent.calls) == before


def test_sync_skips_an_expired_device(app, duo, configured, sent):
    subscribe(duo)
    sent.result = ("failed", "503")
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    PushSubscription.query.update({"status": "expired"})
    db.session.commit()
    before = len(sent.calls)
    duo.b.post("/api/push/sync")
    assert len(sent.calls) == before


def test_expired_subscriptions_are_removed_after_a_day(app, duo, configured, sent):
    subscribe(duo, 1)
    PushSubscription.query.update({"status": "expired", "updated_at": utcnow() - timedelta(days=3)})
    db.session.commit()
    duo.b.post("/api/push/sync")
    assert PushSubscription.query.count() == 0


def test_a_new_message_pushes_again_with_a_fresh_receipt_even_after_an_earlier_receipt(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "one", "uuid-0001-aaaa")
    ack(app, token(sent))
    say(duo.a, duo, "two", "uuid-0002-aaaa")
    assert len(sent.calls) == 2 and statuses() == ["delivered", "sent"]
    assert PushDelivery.query.one().acked_at is None             # waiting for the NEW push's receipt
    ack(app, token(sent))
    assert statuses() == ["delivered", "delivered"]


def test_no_vapid_config_means_no_double_tick(app, duo, sent):
    res = say(duo.a, duo, "hi", "uuid-0001-aaaa")
    assert res.status_code == 201 and sent.calls == [] and statuses() == ["sent"]
    assert PushDelivery.query.count() == 0
