"""Calling API: authorization, role derivation, state machine, busy handling, tokens, incoming delivery."""
import base64
import hashlib
import hmac
import json
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import CallHistory, Notification
from app.services import conversation_service
from app.utils.time import utcnow

from conftest import login_as, verified

SECRET = "s" * 40


@pytest.fixture(autouse=True)
def calling_config(app):
    app.config.update(CALLING_HMAC_SECRET=SECRET, CALLING_SERVER_URL="https://calls.example.onrender.com",
                      CALLING_RING_SECONDS=60)


def _pair(app):
    a, b, c = verified("alice"), verified("bob"), verified("carol")
    conversation_service.get_or_create(a.id, b.id)
    conversation_service.get_or_create(a.id, c.id)
    conversation_service.get_or_create(b.id, c.id)
    db.session.commit()
    return a, b, c, login_as(app, a), login_as(app, b), login_as(app, c)


def post(browser, path, **body):
    return browser.post(f"/api/calling{path}", json=body)


def decode(token):
    payload, sig = token.split(".")
    expect = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    assert hmac.compare_digest(sig, expect), "token signature must verify with the shared secret"
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def start(browser, peer, kind="voice"):
    res = post(browser, "/initiate", peer_id=peer.id, call_type=kind)
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def test_requires_login(app, client):
    for method, path in (("post", "/initiate"), ("post", "/token"), ("post", "/status"), ("post", "/accept"),
                         ("get", "/incoming"), ("get", "/history")):
        res = getattr(client, method)(f"/api/calling{path}")
        assert res.status_code in (302, 401), path


def test_initiate_needs_existing_conversation_and_valid_peer(app):
    a, b = verified("alice"), verified("bob")
    browser = login_as(app, a)
    assert post(browser, "/initiate", peer_id=b.id, call_type="voice").status_code == 403   # never chatted
    conversation_service.get_or_create(a.id, b.id)
    db.session.commit()
    assert post(browser, "/initiate", peer_id=a.id).status_code == 400                      # yourself
    assert post(browser, "/initiate", peer_id=99999).status_code == 404
    assert post(browser, "/initiate", peer_id="x").status_code == 400
    assert post(browser, "/initiate", peer_id=b.id, call_type="screen").status_code == 400


def test_caller_token_carries_server_decided_role_and_is_unique(app):
    a, b, c, ba, bb, bc = _pair(app)
    data = start(ba, b, "video")
    claims = decode(data["token"])
    assert (claims["role"], claims["uid"], claims["pid"], claims["type"], claims["call_id"]) == ("caller", a.id, b.id, "video", data["call_id"])
    assert claims["exp"] - claims["iat"] == 120 and len(claims["jti"]) >= 16 and claims["v"] == 1
    t2 = post(ba, "/token", call_id=data["call_id"]).get_json()["token"]
    assert decode(t2)["jti"] != claims["jti"] and decode(t2)["role"] == "caller"


def test_incoming_call_is_delivered_marks_ringing_and_creates_notice(app):
    a, b, c, ba, bb, bc = _pair(app)
    assert bb.get("/api/calling/incoming").get_json()["call"] is None
    data = start(ba, b)
    inc = bb.get("/api/calling/incoming").get_json()["call"]
    assert inc["call_id"] == data["call_id"] and inc["caller"]["id"] == a.id and inc["call_type"] == "voice"
    assert 0 < inc["seconds_left"] <= 60
    assert CallHistory.query.filter_by(call_id=data["call_id"]).one().status == "ringing"
    assert bb.get("/api/calling/incoming").get_json()["call"]["call_id"] == data["call_id"]   # polling again: same call, no duplicate
    assert ba.get("/api/calling/incoming").get_json()["call"] is None                          # the caller never "receives" it
    notes = Notification.query.filter_by(user_id=b.id, type="incoming_call").all()
    assert len(notes) == 1 and notes[0].dedupe_key == f"call:{data['call_id']}"


def test_busy_rules(app):
    a, b, c, ba, bb, bc = _pair(app)
    start(ba, b)
    res = post(ba, "/initiate", peer_id=c.id)                      # alice is already calling
    assert res.status_code == 409 and res.get_json()["busy"] is True
    res = post(bc, "/initiate", peer_id=b.id)                      # bob is busy (being called)
    assert res.status_code == 409 and res.get_json()["peer_busy"] is True
    assert CallHistory.query.filter_by(caller_id=c.id, status="busy").count() == 1     # shows up in carol's history
    assert Notification.query.filter_by(user_id=b.id, type="incoming_call").count() == 1   # bob was not disturbed again


def test_accept_is_receiver_only_and_gives_callee_token(app):
    a, b, c, ba, bb, bc = _pair(app)
    d = start(ba, b)
    assert post(ba, "/accept", call_id=d["call_id"]).status_code == 404          # the caller cannot "accept" her own call
    assert post(bc, "/accept", call_id=d["call_id"]).status_code == 404          # a stranger cannot either
    assert post(bc, "/token", call_id=d["call_id"]).status_code == 404
    res = post(bb, "/accept", call_id=d["call_id"]).get_json()
    claims = decode(res["token"])
    assert claims["role"] == "callee" and claims["uid"] == b.id and claims["pid"] == a.id
    assert CallHistory.query.filter_by(call_id=d["call_id"]).one().accepted_at is not None


def test_status_transitions_and_roles(app):
    a, b, c, ba, bb, bc = _pair(app)
    d = start(ba, b)
    cid = d["call_id"]
    assert post(bc, "/status", call_id=cid, status="completed").status_code == 404      # not a participant
    assert post(ba, "/status", call_id=cid, status="rejected").status_code == 403       # caller cannot reject
    assert post(bb, "/status", call_id=cid, status="cancelled").status_code == 403      # receiver cannot cancel
    assert post(bb, "/status", call_id=cid, status="completed").status_code == 409      # not connected yet
    assert post(ba, "/status", call_id=cid, status="connected").status_code == 409      # nobody accepted
    assert post(ba, "/status", call_id=cid, status="bogus").status_code == 400
    assert post(ba, "/status", call_id=cid, status="initiated").status_code == 400
    post(bb, "/accept", call_id=cid)
    assert post(ba, "/status", call_id=cid, status="connected").status_code == 200
    rec = CallHistory.query.filter_by(call_id=cid).one()
    rec.answered_at = utcnow() - timedelta(seconds=75)        # pretend the call lasted 75 s
    db.session.commit()
    res = post(bb, "/status", call_id=cid, status="completed", duration_seconds=999999).get_json()
    assert 74 <= res["call"]["duration_seconds"] <= 77, "duration is computed by the server, never taken from the browser"
    assert post(ba, "/status", call_id=cid, status="completed", duration_seconds=1).status_code == 200   # idempotent
    assert CallHistory.query.filter_by(call_id=cid).one().duration_seconds == res["call"]["duration_seconds"]
    assert post(ba, "/status", call_id=cid, status="failed").status_code == 409        # a finished call never changes
    assert post(ba, "/token", call_id=cid).status_code == 409                          # no tokens for ended calls


def test_reject_cancel_and_busy_by_receiver(app):
    a, b, c, ba, bb, bc = _pair(app)
    d = start(ba, b)
    assert post(bb, "/status", call_id=d["call_id"], status="rejected").status_code == 200
    rec = CallHistory.query.filter_by(call_id=d["call_id"]).one()
    assert rec.status == "rejected" and rec.ended_by_id == b.id and rec.duration_seconds == 0
    d2 = start(ba, b)
    assert post(ba, "/status", call_id=d2["call_id"], status="cancelled").status_code == 200
    d3 = start(ba, b)
    assert post(bb, "/status", call_id=d3["call_id"], status="busy").status_code == 200


def test_unanswered_call_becomes_missed_with_notice(app):
    a, b, c, ba, bb, bc = _pair(app)
    d = start(ba, b)
    rec = CallHistory.query.filter_by(call_id=d["call_id"]).one()
    rec.started_at = utcnow() - timedelta(seconds=120)
    db.session.commit()
    assert bb.get("/api/calling/incoming").get_json()["call"] is None       # too late to ring
    db.session.expire_all()
    rec = CallHistory.query.filter_by(call_id=d["call_id"]).one()
    assert rec.status == "missed" and rec.end_reason == "timeout"
    assert Notification.query.filter_by(user_id=b.id, type="missed_call").count() == 1
    assert post(bb, "/accept", call_id=d["call_id"]).status_code == 409
    start(ba, b)                                                             # nobody stays busy forever


def test_history_is_private(app):
    a, b, c, ba, bb, bc = _pair(app)
    start(ba, b)
    assert len(ba.get("/api/calling/history").get_json()["calls"]) == 1
    assert len(bb.get("/api/calling/history").get_json()["calls"]) == 1
    assert bc.get("/api/calling/history").get_json()["calls"] == []
    assert bc.get(f"/api/calling/state/{CallHistory.query.first().call_id}").status_code == 404


def test_not_configured_is_a_clean_503(app):
    a, b = verified("alice"), verified("bob")
    conversation_service.get_or_create(a.id, b.id)
    db.session.commit()
    app.config["CALLING_HMAC_SECRET"] = ""
    assert post(login_as(app, a), "/initiate", peer_id=b.id).status_code == 503
    assert CallHistory.query.count() == 0


def test_browser_may_reach_calling_server_and_use_camera(app):
    a = verified("alice")
    res = login_as(app, a).get("/messages")
    csp = res.headers["Content-Security-Policy"]
    assert "https://calls.example.onrender.com" in csp and "wss://calls.example.onrender.com" in csp
    assert "camera=(self)" in res.headers["Permissions-Policy"] and "microphone=(self)" in res.headers["Permissions-Policy"]
