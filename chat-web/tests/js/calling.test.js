const test = require("node:test");
const assert = require("node:assert/strict");
const { makeEnv, until, sleep } = require("./harness");

const peer = { full_name: "Bob" };
const lastWs = (e) => e.sockets[e.sockets.length - 1];

test("call buttons are injected into the chat header", async (t) => {
  const e = makeEnv(); t.after(e.close);
  await until(() => e.$("goppo-voice-btn") && e.$("goppo-video-btn"), 1000, "buttons");
});

test("wake-up: polls /ready with back-off until the server answers, shows progress, then continues automatically", async (t) => {
  let n = 0;
  const e = makeEnv({ ready: () => (++n < 3 ? (n === 1 ? new Error("network") : { status: 503, body: {} }) : { status: 200, body: { ready: true } }) });
  t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  e.G.startVoice(7, peer);
  await until(() => lastWs(e), 3000, "websocket after wake-up");
  assert.equal(n, 3, "two failed polls then success");
  const urls = e.urls();
  assert.ok(urls.indexOf("/api/calling/initiate") > urls.lastIndexOf("https://calls.test/ready"), "the callee is only rung AFTER the server is ready");
  assert.deepEqual(Array.from(lastWs(e).protocols), ["goppo.v1", "auth.tok-A"], "token travels as sub-protocol, never in the URL");
  assert.ok(!lastWs(e).url.includes("tok-A") && lastWs(e).url === "wss://calls.test/ws");
});

test("wake-up: single flight — a second click does not start a second wake-up or a second call", async (t) => {
  let polls = 0;
  const e = makeEnv({ ready: () => { polls++; return { status: polls < 4 ? 503 : 200, body: {} }; } });
  t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  const p1 = e.G.ensureServerReady(); const p2 = e.G.ensureServerReady();
  assert.equal(p1, p2, "same shared promise");
  e.G.startVoice(7, peer); e.G.startVoice(7, peer); e.G.startVideo(7, peer);
  await until(() => lastWs(e), 3000);
  assert.equal(e.calls.filter((c) => c.url === "/api/calling/initiate").length, 1, "exactly one call is created");
});

test("wake-up is bounded: times out, shows Retry and Cancel, never loads forever", async (t) => {
  const e = makeEnv({ ready: () => ({ status: 503, body: {} }), wakeTimeout: 150 });
  t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  e.G.startVoice(7, peer);
  await until(() => !e.$("goppo-btn-retry").hidden, 2000, "retry button");
  assert.match(e.$("goppo-call-status").textContent, /did not respond in time/);
  assert.equal(e.$("goppo-call-spinner").hidden, true, "spinner stops");
  assert.equal(e.$("goppo-btn-cancel").hidden, false);
  assert.ok(!e.calls.some((c) => c.url === "/api/calling/initiate"), "nobody was rung while the server was down");
  // Retry works once the server is up
  e.ready = () => ({ status: 200, body: {} });
  e.$("goppo-btn-retry").click();
  await until(() => lastWs(e), 2000, "ws after retry");
});

test("cancel during wake-up aborts everything and leaves no call behind", async (t) => {
  const e = makeEnv({ ready: () => ({ status: 503, body: {} }), wakeTimeout: 5000 });
  t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  e.G.startVoice(7, peer);
  await until(() => e.screen() === "wake" && e.calls.some((c) => c.url.endsWith("/ready")), 1000);
  e.$("goppo-btn-cancel").click();
  await until(() => e.screen() === null, 1000, "overlay closed");
  assert.equal(e.G._state.call, null);
  assert.ok(!e.calls.some((c) => c.url === "/api/calling/initiate"));
  e.G.startVoice(7, peer);                                           // a fresh call is possible afterwards
  await until(() => e.screen() === "wake", 500);
});

test("microphone permission denied: clear message, nobody is rung, no server contact", async (t) => {
  const e = makeEnv({ mediaDenied: "NotAllowedError" }); t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  e.G.startVoice(7, peer);
  await until(() => /Permission denied/.test(e.$("goppo-call-status").textContent), 1000, "message");
  assert.ok(!e.calls.some((c) => c.url === "/api/calling/initiate" || c.url.endsWith("/ready")));
});

test("caller: offer is sent with an id, stays in the outbox until the server confirms 'queued', re-sent after reconnect", async (t) => {
  const e = makeEnv(); t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  e.G.startVideo(7, peer);
  await until(() => lastWs(e) && lastWs(e).types().includes("offer"), 3000, "offer");
  const ws1 = lastWs(e);
  const offer = ws1.sent.find((m) => m.type === "offer");
  assert.ok(offer.id && offer.payload.sdp.startsWith("v=0"));
  assert.equal(e.G._state.call.outbox.get(offer.id).queued, false, "sending over a socket is not delivery");
  assert.equal(e.lastConstraints.video.facingMode, "user");

  ws1._close(1006);                                                  // network drop before the server confirmed anything
  await until(() => e.sockets.length === 2 && e.sockets[1].types().includes("offer"), 4000, "re-sent after reconnect");
  assert.ok(e.tokens >= 1, "a fresh token is fetched: a token opens only one socket");
  assert.equal(e.sockets[1].protocols[1], "auth.tok-R1");
  assert.equal(e.sockets[1].sent.find((m) => m.type === "offer").id, offer.id, "same id so the server de-duplicates");

  e.sockets[1].emit({ type: "queued", id: offer.id });
  e.sockets[1].emit({ type: "delivered", id: offer.id });
  assert.equal(e.G._state.call.outbox.size, 0);
  assert.equal(e.G._state.call.offerDelivered, true);
});

test("remote messages: acknowledged first, duplicates (same seq) processed once, ICE buffered until the answer arrives", async (t) => {
  const e = makeEnv({ peerOnline: true }); t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  e.G.startVoice(7, peer);
  await until(() => lastWs(e) && lastWs(e).types().includes("offer"), 3000);
  const ws = lastWs(e), pc = e.pcs[0];
  const cand = { candidate: "candidate:1 1 udp 1 1.2.3.4 9 typ host", sdpMid: "0", sdpMLineIndex: 0 };
  ws.emit({ type: "ice-candidate", seq: 1, id: "c1", payload: cand });
  ws.emit({ type: "ice-candidate", seq: 1, id: "c1", payload: cand });   // server re-send
  await sleep(20);
  assert.equal(pc.added.length, 0, "no remote description yet: candidate must be buffered, not dropped or applied");
  ws.emit({ type: "answer", seq: 2, id: "a1", payload: { sdp: "v=0 answer" } });
  await until(() => pc.added.length === 1, 1000, "buffered candidate applied");
  assert.equal(ws.sent.filter((m) => m.type === "ack").length, 3, "every delivery is acknowledged, duplicates too");
  assert.deepEqual(ws.sent.filter((m) => m.type === "ack").map((m) => m.seq), [1, 1, 2]);
  assert.equal(pc.remoteDescription.type, "answer");
});

test("connected state: timer, status report to Flask, quality indicator and automatic audio-only advice", async (t) => {
  const e = makeEnv({ peerOnline: true }); t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  e.G.startVideo(7, peer);
  await until(() => e.pcs[0], 3000);
  e.pcs[0].setState("connected");
  await until(() => e.screen() === "active", 500);
  assert.ok(e.statuses.some((s) => s.status === "connected" && s.call_id === "call-1"));
  assert.equal(e.$("goppo-call-timer").hidden, false);
  assert.ok(e.$("goppo-call-overlay").classList.contains("has-video"));
  const ws = lastWs(e);
  ws.emit({ type: "quality_report", score: 85, report: { level: 0, advise_reduce_video: false, advise_audio_only: false } });
  assert.equal(e.$("goppo-call-quality").dataset.level, "0");
  assert.equal(e.$("goppo-call-quality").querySelectorAll("i.on").length, 4);
  ws.emit({ type: "quality_report", score: 20, report: { level: 3, advise_reduce_video: true, advise_audio_only: true } });
  assert.equal(e.$("goppo-call-quality").querySelectorAll("i.on").length, 1);
  assert.equal(e.G._state.call.stream.getVideoTracks()[0].enabled, false, "video paused on a bad network");
  assert.match(e.$("goppo-call-hint").textContent, /video paused/);
  ws.emit({ type: "quality_report", score: 90, report: { level: 0 } });
  assert.equal(e.G._state.call.stream.getVideoTracks()[0].enabled, true, "video resumes when the network recovers");
});

test("controls: mute, camera off, hang up reports completed and closes everything", async (t) => {
  const e = makeEnv({ peerOnline: true }); t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  e.G.startVideo(7, peer);
  await until(() => e.pcs[0], 3000);
  e.pcs[0].setState("connected");
  await until(() => e.screen() === "active", 500);
  const c = e.G._state.call;
  e.$("goppo-btn-mute").click();
  assert.equal(c.stream.getAudioTracks()[0].enabled, false);
  e.$("goppo-btn-mute").click();
  assert.equal(c.stream.getAudioTracks()[0].enabled, true);
  e.$("goppo-btn-cam").click();
  assert.equal(c.stream.getVideoTracks()[0].enabled, false);
  const ws = lastWs(e), tracks = c.stream.getTracks();
  e.$("goppo-btn-end").click();
  await until(() => e.screen() === "ended", 1000, "ended screen");
  assert.ok(ws.types().includes("hangup"));
  assert.ok(e.statuses.some((s) => s.status === "completed"));
  assert.ok(tracks.every((x) => x.stopped), "camera and microphone are released");
  assert.equal(e.pcs[0].closed, true);
  assert.equal(e.$("goppo-btn-close").hidden, false);
  e.$("goppo-btn-close").click();
  assert.equal(e.G._state.call, null);
});

test("busy peer: 409 peer_busy shows a busy end screen", async (t) => {
  const e = makeEnv({ initiate: (reply) => reply(409, { ok: false, error: "busy", busy: true, peer_busy: true }) }); t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  e.G.startVoice(7, peer);
  await until(() => e.screen() === "ended", 2000, "ended");
  assert.match(e.$("goppo-call-status").textContent, /busy/i);
  assert.equal(e.sockets.length, 0);
});

test("remote reject / busy / hangup end the call with the right message", async (t) => {
  for (const [type, re] of [["reject", /declined/], ["busy", /busy/], ["hangup", /cancelled/]]) {
    const e = makeEnv({ peerOnline: true });
    await until(() => e.G && e.G._state.cfg, 500);
    e.G.startVoice(7, peer);
    await until(() => lastWs(e), 3000);
    lastWs(e).emit({ type, seq: 1, id: "x" });
    await until(() => e.screen() === "ended", 1000, type);
    assert.match(e.$("goppo-call-status").textContent, re);
    e.close();
  }
});

test("server session_end (timeout) shows missed; 'replaced' is ignored", async (t) => {
  const e = makeEnv(); t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  e.G.startVoice(7, peer);
  await until(() => lastWs(e), 3000);
  lastWs(e).emit({ type: "session_end", reason: "replaced" });
  assert.notEqual(e.screen(), "ended");
  lastWs(e).emit({ type: "session_end", reason: "timeout" });
  await until(() => e.screen() === "ended", 500);
  assert.match(e.$("goppo-call-status").textContent, /No answer/);
});

test("incoming call event: one screen with caller and type; duplicates ignored; Reject reports rejected", async (t) => {
  const e = makeEnv(); t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  const info = { call_id: "in-1", call_type: "video", conversation_id: 1, caller: { id: 7, full_name: "Bob" }, seconds_left: 60 };
  e.w.dispatchEvent(new e.w.CustomEvent("goppo:incoming-call", { detail: info }));
  e.w.dispatchEvent(new e.w.CustomEvent("goppo:incoming-call", { detail: info }));
  e.G.showIncoming(info);
  assert.equal(e.screen(), "incoming");
  assert.equal(e.$("goppo-call-name").textContent, "Bob");
  assert.match(e.$("goppo-call-status").textContent, /Incoming video call/);
  assert.equal(e.$("goppo-call-incoming").children.length, 2, "Accept and Reject");
  e.$("goppo-btn-reject").click();
  await until(() => e.statuses.some((s) => s.status === "rejected" && s.call_id === "in-1"), 1000);
  await until(() => e.screen() === null, 1000, "overlay closed after reject");
  e.G.showIncoming(info);                                              // already handled: never shown again
  assert.equal(e.screen(), null);
});

test("incoming call found by polling (e.g. after opening a push notification)", async (t) => {
  const e = makeEnv(); t.after(e.close);
  e.incoming = { call_id: "poll-1", call_type: "voice", conversation_id: 1, caller: { id: 7, full_name: "Bob" }, seconds_left: 30 };
  await until(() => e.screen() === "incoming", 1500, "incoming screen from the poll");
});

test("accept: permission -> server ready -> /accept -> callee token -> answer; offer that arrives early is not lost", async (t) => {
  const e = makeEnv({ peerOnline: true }); t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  e.G.showIncoming({ call_id: "in-2", call_type: "voice", conversation_id: 1, caller: { id: 7, full_name: "Bob" }, seconds_left: 60 });
  e.$("goppo-btn-accept").click();
  await until(() => lastWs(e), 3000, "callee websocket");
  assert.deepEqual(Array.from(lastWs(e).protocols), ["goppo.v1", "auth.tok-C"]);
  const urls = e.urls();
  assert.ok(urls.indexOf("/api/calling/accept") > urls.lastIndexOf("https://calls.test/ready"));
  await until(() => lastWs(e).types().includes("ready"), 1000, "ready sent");
  lastWs(e).emit({ type: "offer", seq: 1, id: "o1", payload: { sdp: "v=0 offer" } });
  await until(() => lastWs(e).types().includes("answer"), 1000, "answer");
  assert.equal(e.pcs[0].remoteDescription.type, "offer");
  assert.deepEqual(Array.from(e.pcs[0].cfg.iceServers[1].urls), ["turn:t"], "TURN servers from /ice are used");
});

test("a second incoming call while in a call is answered 'busy' without interrupting", async (t) => {
  const e = makeEnv(); t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  e.G.startVoice(7, peer);
  await until(() => lastWs(e), 3000);
  e.G.showIncoming({ call_id: "in-3", call_type: "voice", caller: { id: 9, full_name: "Carol" }, seconds_left: 60 });
  await until(() => e.statuses.some((s) => s.call_id === "in-3" && s.status === "busy"), 1000);
  assert.notEqual(e.screen(), "incoming");
});

test("every state-changing request carries the CSRF token", async (t) => {
  const e = makeEnv(); t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  e.G.startVoice(7, peer);
  await until(() => lastWs(e), 3000);
  const posts = e.calls.filter((c) => c.method === "POST");
  assert.ok(posts.length >= 1 && posts.every((c) => c.headers["X-CSRFToken"] === "tok123"));
});

test("reconnection after a drop in a connected call keeps the call and reports status", async (t) => {
  const e = makeEnv({ peerOnline: true }); t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  e.G.startVoice(7, peer);
  await until(() => e.pcs[0], 3000);
  e.pcs[0].setState("connected");
  await until(() => e.screen() === "active", 500);
  lastWs(e)._close(1006);
  await until(() => /Reconnecting/.test(e.$("goppo-call-status").textContent), 1000);
  await until(() => e.sockets.length === 2, 3000, "second socket");
  await until(() => e.$("goppo-call-status").textContent === "Connected", 1500);
  assert.equal(e.screen(), "active");
  e.sockets[1]._close(4001);                                           // opened elsewhere
  await until(() => e.screen() === "ended", 1000);
  assert.match(e.$("goppo-call-status").textContent, /another device/);
});

test("ICE failure on the caller side triggers an ICE-restart offer", async (t) => {
  const e = makeEnv({ peerOnline: true }); t.after(e.close);
  await until(() => e.G && e.G._state.cfg, 500);
  e.G.startVoice(7, peer);
  await until(() => e.pcs[0], 3000);
  e.pcs[0].setState("connected");
  await until(() => e.screen() === "active", 500);
  e.pcs[0].setState("failed");
  await until(() => lastWs(e).sent.filter((m) => m.type === "offer").length === 2, 1000, "restart offer");
  assert.match(lastWs(e).sent.filter((m) => m.type === "offer")[1].payload.sdp, /restart/);
});
