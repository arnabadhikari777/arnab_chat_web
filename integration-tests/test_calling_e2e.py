"""End-to-end: the REAL Go calling server binary (with C, C++, Rust linked) + the REAL Flask app + real WebSockets.

Run:  ./scripts/build_all.sh   then   cd integration-tests && python3 -m pytest -q
Flask mints the tokens (calling_service), Go verifies them: this proves both sides agree on the token format.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
import websockets

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "calling-server" / "bin" / "goppo-calling"
sys.path.insert(0, str(ROOT / "chat-web"))

SECRET = "e2e-shared-secret-e2e-shared-secret-123456"
ORIGIN = "https://goppo-e2e.example"
SDP = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=fingerprint:sha-256 AA:BB:CC\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=rtpmap:111 opus/48000/2\r\n"
CAND = "candidate:1 1 udp 2113937151 203.0.113.7 50000 typ host"


_NEXT = [0]


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture(scope="module")
def server():
    if not BINARY.exists():
        pytest.fail(f"{BINARY} missing: run scripts/build_all.sh first")
    port = free_port()
    env = dict(os.environ, PORT=str(port), CALLING_HMAC_SECRET=SECRET, ALLOWED_ORIGINS=ORIGIN + ",http://localhost:5000",
               TURN_URLS="turn:turn.example:3478?transport=udp,turns:turn.example:5349?transport=tcp", TURN_SHARED_SECRET="turn-secret",
               RING_TIMEOUT_SECONDS="30", RECONNECT_GRACE_SECONDS="5")
    proc = subprocess.Popen([str(BINARY)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/health", timeout=1); break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill(); pytest.fail("server did not start: " + proc.stdout.read())
    yield {"base": base, "ws": f"ws://127.0.0.1:{port}/ws", "proc": proc}
    proc.terminate(); proc.wait(timeout=10)


@pytest.fixture()
def flask(monkeypatch):
    from app import create_app
    from app.extensions import db
    app = create_app("testing")
    app.config.update(CALLING_HMAC_SECRET=SECRET, CALLING_SERVER_URL=ORIGIN, CALLING_TOKEN_TTL_SECONDS=120)
    def _fresh_request_state():          # same isolation the unit tests use: one login per browser, not per context
        from flask import g
        g.pop("_login_user", None)
        db.session.expire_all()
    app.before_request_funcs.setdefault(None, []).insert(0, _fresh_request_state)
    with app.app_context():
        db.create_all()
        from tests.conftest import verified, login_as
        from app.services import conversation_service
        for i in range(_NEXT[0]):          # distinct user ids per test: the shared Go server keeps users "busy" until a call ends
            verified(f"pad{i}")
        _NEXT[0] += 2
        a, b = verified("alice"), verified("bob")
        conversation_service.get_or_create(a.id, b.id)
        db.session.commit()
        yield app, login_as(app, a), login_as(app, b), a, b
        db.session.remove(); db.drop_all()


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


async def connect(srv, token, origin=ORIGIN):
    return await websockets.connect(srv["ws"], subprotocols=["goppo.v1", "auth." + token], origin=origin, open_timeout=5)


async def recv(ws, want, timeout=4):
    end = time.time() + timeout
    while time.time() < end:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=end - time.time()))
        if m["type"] == want:
            return m
    raise AssertionError(f"no {want}")


def test_http_surface(server):
    assert get_json(server["base"] + "/health")[1]["status"] == "ok"
    code, ready = get_json(server["base"] + "/ready")
    assert code == 200 and ready["native_verified"] is True
    code, comp = get_json(server["base"] + "/components")
    assert comp["all_verified"] and {c["language"] for c in comp["components"]} == {"c", "c++", "rust"}
    assert get_json(server["base"] + "/ice")[0] == 401
    req = urllib.request.Request(server["base"] + "/health", headers={"Origin": "https://evil.example"})
    with urllib.request.urlopen(req) as r:
        assert r.headers.get("Access-Control-Allow-Origin") is None
    req = urllib.request.Request(server["base"] + "/health", headers={"Origin": ORIGIN})
    with urllib.request.urlopen(req) as r:
        assert r.headers.get("Access-Control-Allow-Origin") == ORIGIN


def test_full_call_flask_tokens_reliable_signaling_and_native_counters(server, flask):
    app, ba, bb, a, b = flask
    before = get_json(server["base"] + "/components")[1]["components"]
    r = ba.post("/api/calling/initiate", json={"peer_id": b.id, "call_type": "voice"}).get_json()
    assert r["ok"], r
    call_id, caller_token = r["call_id"], r["token"]

    code, ice = get_json(server["base"] + "/ice", {"X-Call-Token": caller_token})
    assert code == 200 and ice["turn_configured"] and any("credential" in s for s in ice["iceServers"])
    assert "turn-secret" not in json.dumps(ice)

    inc = bb.get("/api/calling/incoming").get_json()["call"]
    assert inc["call_id"] == call_id                                           # incoming call delivery (Flask side)
    accept = bb.post("/api/calling/accept", json={"call_id": call_id}).get_json()
    assert accept["role"] == "callee"

    async def scenario():
        caller = await connect(server, caller_token)
        w = json.loads(await caller.recv()); assert w["type"] == "welcome" and w["role"] == "caller" and w["user_id"] == a.id
        await caller.send(json.dumps({"type": "offer", "id": "o1", "payload": {"sdp": SDP}}))       # callee NOT connected yet
        q = await recv(caller, "queued"); assert q["peer_online"] is False

        callee = await connect(server, accept["token"])
        w2 = json.loads(await callee.recv()); assert w2["type"] == "welcome" and w2["role"] == "callee"
        off = await recv(callee, "offer")
        assert off["payload"]["sdp"] == SDP and off["from"] == a.id
        await callee.send(json.dumps({"type": "ack", "seq": off["seq"]}))
        assert (await recv(caller, "delivered"))["id"] == "o1"                                       # real delivery proof

        await callee.send(json.dumps({"type": "answer", "id": "a1", "payload": {"sdp": SDP}}))
        assert (await recv(caller, "answer"))["from"] == b.id
        await caller.send(json.dumps({"type": "ice-candidate", "id": "i1", "payload": {"candidate": CAND}}))
        await recv(callee, "ice-candidate")
        await caller.send(json.dumps({"type": "ice-candidate", "id": "i2", "payload": {"candidate": "javascript:alert(1)"}}))
        assert (await recv(caller, "error"))["code"] == "invalid_candidate"
        for _ in range(5):
            await caller.send(json.dumps({"type": "quality", "payload": {"rtt_ms": 40, "jitter_ms": 4, "loss_permille": 1, "bitrate_kbps": 900, "audio_peak": 0.3}}))
        rep = None
        for _ in range(5):
            rep = await recv(caller, "quality_report")
        assert rep["score"] > 80 and rep["report"]["level"] == 0
        await caller.send(json.dumps({"type": "hangup", "id": "h1", "payload": {}}))
        await recv(callee, "hangup")
        await caller.close(); await callee.close()

        with pytest.raises(Exception):                                         # the same token must not open a second socket
            await connect(server, caller_token)
    asyncio.run(scenario())

    assert ba.post("/api/calling/status", json={"call_id": call_id, "status": "completed"}).status_code == 409   # never connected
    assert ba.post("/api/calling/status", json={"call_id": call_id, "status": "cancelled"}).status_code == 200
    after = get_json(server["base"] + "/components")[1]["components"]
    rust_b, rust_a = before[0]["detail"], after[0]["detail"]
    cpp_b, cpp_a = before[2]["detail"], after[2]["detail"]
    assert rust_a["signals_checked"] > rust_b["signals_checked"] and rust_a["signals_rejected"] > rust_b["signals_rejected"]
    assert rust_a["quality_updates"] >= rust_b["quality_updates"] + 5
    assert cpp_a["telemetry_samples_processed"] >= cpp_b["telemetry_samples_processed"] + 5


def test_token_cannot_be_used_from_wrong_origin_or_after_forging(server, flask):
    app, ba, bb, a, b = flask
    token = ba.post("/api/calling/initiate", json={"peer_id": b.id, "call_type": "video"}).get_json()["token"]
    async def go():
        with pytest.raises(Exception):
            await connect(server, token, origin="https://evil.example")
        payload, sig = token.split(".")
        with pytest.raises(Exception):
            await connect(server, payload + "." + sig[::-1])
        ws = await connect(server, token)                                      # the genuine token still works
        assert json.loads(await ws.recv())["call_type"] == "video"
        await ws.close()
    asyncio.run(go())


def test_busy_enforced_by_server_across_calls(server, flask):
    app, ba, bb, a, b = flask
    from app.services import calling_service
    d1 = ba.post("/api/calling/initiate", json={"peer_id": b.id}).get_json()
    async def go():
        ws = await connect(server, d1["token"]); await ws.recv()
        # a second token for the same user but another call id must be refused with 409
        with app.test_request_context():
            t2 = calling_service.make_call_token(user_id=a.id, peer_id=b.id, conversation_id=1, call_type="voice", call_id="other-call", role="caller")
        with pytest.raises(Exception) as e:
            await connect(server, t2)
        assert "409" in str(e.value)
        await ws.close()
    asyncio.run(go())
