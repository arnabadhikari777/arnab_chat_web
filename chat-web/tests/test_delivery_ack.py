"""Verified delivery: a message becomes Delivered only after the recipient's DEVICE confirms the push.

Design under test (what the application really does): the push payload carries a SIGNED token
(itsdangerous, bound to the recipient, the chat, the newest announced message, the notification and the
device). The service worker posts it to /api/push/ack with no cookies and no CSRF token. Nothing secret
is stored: push_deliveries only gets acked_at. An earlier draft of this file tested a different design
(token hashes stored in extra columns) that was never implemented; see CODE_REPAIR_REPORT.md.

Real push services are never contacted (push_service._send_one is replaced by a recorder).
"""
import pytest

from app.extensions import db
from app.models import Message, PushDelivery
from app.services import push_service
from tests.conftest import verified
from tests.test_push import configured, duo, say, sent, sub_json  # noqa: F401  (fixtures + helpers)


def subscribe(duo, n=1):
    res = duo.b.post("/api/push/subscribe", json=sub_json(n))
    assert res.status_code == 200, res.get_data(as_text=True)


def token(sent, n=-1):
    return sent.calls[n]["payload"]["ack"]


def ack(app, body):
    """The service worker has no session and no CSRF token: a bare client."""
    return app.test_client().post("/api/push/ack", json=body)


def statuses():
    db.session.expire_all()
    return [m.status for m in Message.query.order_by(Message.id)]


def test_accepted_push_that_is_never_acknowledged_stays_sent(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    assert len(sent.calls) == 1
    row = PushDelivery.query.one()
    assert row.status == "accepted" and row.acked_at is None
    assert statuses() == ["sent"]


def test_payload_carries_a_signed_token_and_the_database_stores_none(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    tok = token(sent)
    assert isinstance(tok, str) and len(tok) > 40
    assert not hasattr(PushDelivery, "ack_token_hash")              # stateless: nothing to leak from the database


def test_valid_ack_makes_the_message_delivered_and_records_the_receipt(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    res = ack(app, {"token": token(sent)})
    assert res.status_code == 200 and res.get_json() == {"ok": True, "updated": 1}
    assert statuses() == ["delivered"]
    assert Message.query.one().delivered_at is not None
    assert PushDelivery.query.one().acked_at is not None


def test_replaying_the_same_token_changes_nothing(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    tok = token(sent)
    assert ack(app, {"token": tok}).get_json()["updated"] == 1
    again = ack(app, {"token": tok})
    assert again.status_code == 200 and again.get_json()["updated"] == 0
    assert statuses() == ["delivered"]


@pytest.mark.parametrize("body", [{}, {"token": ""}, {"token": "not-a-token"}, {"token": "x" * 2000}, {"token": 5}, [], "text"])
def test_invalid_acknowledgements_change_nothing(app, duo, configured, sent, body):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    res = ack(app, body)
    assert res.status_code == 400 and res.get_json()["ok"] is False
    assert statuses() == ["sent"]


def test_non_json_body_is_refused(app, duo):
    res = app.test_client().post("/api/push/ack", data="token=abc", content_type="text/plain")
    assert res.status_code == 400


def test_ack_covers_only_messages_that_existed_when_the_push_was_sent(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "one", "uuid-0001-aaaa")
    first = token(sent)
    say(duo.a, duo, "two", "uuid-0002-aaaa")
    assert ack(app, {"token": first}).status_code == 200
    assert statuses() == ["delivered", "sent"]                       # the newer message was not announced by that push
    assert ack(app, {"token": token(sent)}).status_code == 200
    assert statuses() == ["delivered", "delivered"]


def test_a_token_for_someone_outside_the_chat_is_refused(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    carol = verified("carol")
    forged = push_service._ack_serializer().dumps({"u": carol.id, "c": duo.conv, "m": 1, "n": 1, "s": None})
    assert ack(app, {"token": forged}).status_code == 400
    assert statuses() == ["sent"]


def test_a_token_signed_with_another_key_is_refused(app, duo, configured, sent):
    from itsdangerous import URLSafeTimedSerializer
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    forged = URLSafeTimedSerializer("some-other-key", salt=push_service._ACK_SALT).dumps(
        {"u": duo.bob.id, "c": duo.conv, "m": 1, "n": 1, "s": None})
    assert ack(app, {"token": forged}).status_code == 400
    assert statuses() == ["sent"]


def test_expired_token_is_refused(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    tok = token(sent)
    app.config["PUSH_ACK_MAX_AGE_SECONDS"] = -1
    assert ack(app, {"token": tok}).status_code == 400
    assert statuses() == ["sent"]


def test_suspended_recipient_cannot_acknowledge(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    tok = token(sent)
    duo.bob.status = "suspended"
    db.session.commit()
    assert ack(app, {"token": tok}).status_code == 400
    assert statuses() == ["sent"]


def test_ack_never_moves_a_seen_message_backwards(app, duo, configured, sent):
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    tok = token(sent)
    assert duo.b.post(f"{duo.url}/seen", json={}).status_code == 200
    assert statuses() == ["seen"]
    assert ack(app, {"token": tok}).status_code == 200
    assert statuses() == ["seen"]


def test_ack_works_without_cookies_and_needs_no_csrf(app, duo, configured, sent):
    app.config["WTF_CSRF_ENABLED"] = True
    subscribe(duo)
    say(duo.a, duo, "hi", "uuid-0001-aaaa")
    assert ack(app, {"token": token(sent)}).status_code == 200
