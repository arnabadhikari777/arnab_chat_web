// Android-readiness layer: platform detection, browser fallback, native event/command contract, calling hooks, device registration.
const test = require("node:test");
const assert = require("node:assert/strict");
const { makeEnv, until, sleep, DEVICE_SRC } = require("./harness");

const plain = (x) => JSON.parse(JSON.stringify(x));   // jsdom objects live in another realm
const peer = { full_name: "Bob" };
const ANDROID_UA = "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 Chrome/126 Mobile Safari/537.36 GoppoAndroid/1.0.0";

test("detection: desktop browser, mobile browser, Capacitor Android", async (t) => {
  const d = makeEnv({ platform: true }); t.after(d.close);
  assert.equal(d.P.info.kind, "browser"); assert.equal(d.P.isNative(), false);
  const m = makeEnv({ platform: true, userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148" }); t.after(m.close);
  assert.equal(m.P.info.kind, "mobile-browser"); assert.equal(m.P.isNative(), false);
  const a = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA }); t.after(a.close);
  assert.equal(a.P.info.kind, "capacitor-android"); assert.equal(a.P.isNative(), true);
});

test("browser fallback: no native layer -> nothing handled, no capabilities, events stay on the web side", async (t) => {
  const e = makeEnv({ platform: true }); t.after(e.close);
  assert.deepEqual(plain(await e.P.native.call("anything")), { handled: false });
  assert.equal(e.P.has("nativeIncomingCall"), false);
  assert.equal(e.P.webPushAllowed(), true);
  let got = null; e.P.on("call:ended", (x) => { got = x; });
  e.P.emit("call:ended", { call_id: "z" });
  assert.equal(got.call_id, "z");
  assert.deepEqual(plain(await e.P.receive("unknown:command", {})), { handled: false });
});

test("native layer: capabilities are read, app:ready is sent, web push is switched off only with nativePush", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA, nativeCaps: ["nativePush", "nativeIncomingCall"] }); t.after(e.close);
  await until(() => e.P.has("nativePush"), 1000, "capabilities");
  assert.equal(e.P.webPushAllowed(), false);
  assert.ok(e.native.events.some((m) => m.event === "app:ready"));
  const e2 = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA, nativeCaps: [] }); t.after(e2.close);
  await sleep(30);
  assert.equal(e2.P.webPushAllowed(), true, "an app without native push keeps whatever the browser offers");
});

test("a failing or slow native call never throws into the website", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA }); t.after(e.close);
  e.w.Capacitor.Plugins.GoppoNative.explode = async () => { throw new Error("boom"); };
  const r = await e.P.native.call("explode");
  assert.equal(r.handled, false);
  assert.deepEqual(plain(await e.P.native.call("doesNotExist")), { handled: false });
});

test("calling events reach the native layer: incoming, connected, muted, ended", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA, nativeCaps: [] }); t.after(e.close);
  await until(() => e.G._state.cfg, 800);
  e.G.showIncoming({ call_id: "c9", call_type: "voice", conversation_id: 1, caller: { id: 7, full_name: "Bob" }, seconds_left: 30 });
  await until(() => e.native.events.some((m) => m.event === "call:incoming"), 500);
  const inc = e.native.events.find((m) => m.event === "call:incoming").detail;
  assert.deepEqual([inc.call_id, inc.call_type, inc.caller.full_name], ["c9", "voice", "Bob"]);
  assert.ok(!JSON.stringify(inc).includes("tok"), "no token ever goes to the native layer");
  const r = await e.P.receive("call:reject", { call_id: "c9" });
  assert.equal(r.result.ok, true);
  await until(() => e.native.events.some((m) => m.event === "call:ended"), 500);
  assert.equal(e.native.events.find((m) => m.event === "call:ended").detail.status, "rejected");
  assert.deepEqual(plain(e.statuses.map((s) => s.status)), ["rejected"]);
});

test("native-owned ringing: with nativeIncomingCall the web page does not ring twice", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA, nativeCaps: ["nativeIncomingCall"] }); t.after(e.close);
  await until(() => e.P.has("nativeIncomingCall"), 800);
  await until(() => e.G._state.cfg, 800);
  e.G.showIncoming({ call_id: "c1", call_type: "voice", caller: peer, seconds_left: 30 });
  assert.equal(e.G._state.ringTimer, null);
});

test("native command call:accept on a ringing call runs the normal accept flow; repeats and wrong ids are refused", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA }); t.after(e.close);
  await until(() => e.G._state.cfg, 800);
  e.incoming = { call_id: "c5", call_type: "voice", conversation_id: 1, caller: { id: 7, full_name: "Bob" }, seconds_left: 40 };
  const wrong = await e.P.receive("call:accept", { call_id: "other" });
  assert.equal(wrong.result.ok, false);
  const ok = await e.P.receive("call:accept", { call_id: "c5" });
  assert.equal(ok.result.ok, true);
  await until(() => e.sockets.length === 1, 3000, "websocket");
  assert.equal(e.calls.filter((c) => c.url === "/api/calling/accept").length, 1);
  const again = await e.P.receive("call:accept", { call_id: "c5" });
  assert.equal(again.result.ok, false, "a second accept of the same call does nothing");
  assert.equal(e.calls.filter((c) => c.url === "/api/calling/accept").length, 1);
});

test("native hangup and mute commands act on the live call only", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA }); t.after(e.close);
  await until(() => e.G._state.cfg, 800);
  assert.equal((await e.P.receive("call:hangup", { call_id: "nope" })).result.ok, false, "no call -> refused");
  e.G.startVoice(7, peer);
  await until(() => e.sockets.length === 1, 3000);
  const c = e.G._state.call;
  assert.equal((await e.P.receive("call:set-muted", { muted: true })).result.muted, true);
  assert.equal(c.stream.getAudioTracks()[0].enabled, false);
  assert.equal((await e.P.receive("call:set-muted", { muted: true })).result.muted, true, "same state twice stays muted");
  assert.equal((await e.P.receive("call:set-muted", { muted: false })).result.muted, false);
  assert.equal((await e.P.receive("call:hangup", { call_id: "wrong" })).result.ok, false);
  assert.equal((await e.P.receive("call:hangup", { call_id: c.callId })).result.ok, true);
  assert.ok(e.native.events.some((m) => m.event === "call:muted-changed"));
  assert.ok(c.ended);
});

test("microphone/camera: the native layer may prepare OS permission first; browsers go straight to getUserMedia", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA, nativeCaps: ["nativePermissions"] }); t.after(e.close);
  await until(() => e.P.has("nativePermissions"), 800);
  await until(() => e.G._state.cfg, 800);
  e.G.startVideo(7, peer);
  await until(() => e.sockets.length === 1, 3000);
  assert.deepEqual(plain(e.native.calls.find((c) => c[0] === "requestMediaPermissions")[1]), { audio: true, video: true });
  const b = makeEnv({ platform: true }); t.after(b.close);
  await until(() => b.G._state.cfg, 800);
  b.G.startVoice(7, peer);
  await until(() => b.sockets.length === 1, 3000);
});

test("calling works with no platform layer at all (browser tests, old cached pages)", async (t) => {
  const e = makeEnv(); t.after(e.close);
  await until(() => e.G._state.cfg, 800);
  e.G.startVoice(7, peer);
  await until(() => e.sockets.length === 1, 3000);
  assert.equal(e.P, undefined);
});

test("app:back closes a dialog, never leaves a live call, otherwise goes back; external links go to the system browser", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA, nativeCaps: ["openExternal"] }); t.after(e.close);
  await until(() => e.P.has("openExternal"), 800);
  const d = e.w.document;
  d.body.insertAdjacentHTML("beforeend", '<div id="goppo-call-overlay"></div>');
  assert.equal((await e.P.receive("app:back")).result.consumed, true, "call overlay visible");
  d.getElementById("goppo-call-overlay").classList.add("hidden");
  assert.equal((await e.P.receive("app:back")).result.consumed, false, "nothing to go back to");
  d.body.insertAdjacentHTML("beforeend", '<a id="x" href="https://example.org/page" target="_blank">x</a><a id="y" href="/inside">y</a>');
  d.getElementById("x").click(); await sleep(20);
  assert.deepEqual(plain(e.native.calls.find((c) => c[0] === "openExternal")[1]), { url: "https://example.org/page" });
  const before = e.native.calls.length;
  d.getElementById("y").addEventListener("click", (ev) => ev.preventDefault()); d.getElementById("y").click();
  assert.equal(e.native.calls.length, before, "same-site links are untouched");
});

test("app:back closes an open <dialog> even when the page has no cancel handler, and respects one that handles it (Android back button)", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA }); t.after(e.close);
  const d = e.w.document;
  // jsdom has no real <dialog>: emulate the two pieces of the contract the code relies on (open flag + close()).
  const mk = (id) => { const x = d.createElement("dialog"); x.id = id; x.setAttribute("open", ""); Object.defineProperty(x, "open", { get() { return this.hasAttribute("open"); } }); x.close = function () { this.removeAttribute("open"); }; d.body.appendChild(x); return x; };
  const plainDlg = mk("plain");
  assert.equal((await e.P.receive("app:back")).result.consumed, true);
  assert.equal(plainDlg.open, false, "dialog without a cancel handler is closed by the back button");
  const own = mk("own"); let handled = 0;
  own.addEventListener("cancel", (ev) => { ev.preventDefault(); handled++; });
  assert.equal((await e.P.receive("app:back")).result.consumed, true);
  assert.equal(handled, 1, "the dialog's own cancel handler ran once");
  assert.equal(own.open, true, "a dialog that handles cancel itself is not force-closed");
});

test("firstSight: one notification id is shown once, however many channels deliver it", async (t) => {
  const e = makeEnv({ platform: true }); t.after(e.close);
  assert.equal(e.P.firstSight(41), true); assert.equal(e.P.firstSight(41), false); assert.equal(e.P.firstSight("42"), true);
  assert.equal(e.P.firstSight(undefined), true);
});

test("device.js registers the token with session+CSRF API, rotates with PATCH, and is inert in a plain browser", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA, nativeCaps: ["nativePush"], pushToken: "fcm:" + "T".repeat(40) }); t.after(e.close);
  const sent = [];
  e.w.document.body.dataset.auth = "1";
  e.w.app = { api: async (url, o) => { sent.push([url, o.method, o.json]); return { ok: true, status: 200, data: {} }; } };
  await until(() => e.P.has("nativePush"), 800);
  e.w.eval(DEVICE_SRC);
  await until(() => sent.length === 1, 1500, "first registration");
  assert.deepEqual(sent[0].slice(0, 2), ["/api/devices", "POST"]);
  assert.equal(sent[0][2].device_id, "dev-fixed-0001"); assert.equal(sent[0][2].app_version, "1.2.3");
  const r = await e.P.receive("push:token", { token: "fcm:" + "N".repeat(40) });
  assert.equal(r.result.ok, true);
  assert.deepEqual(sent[1].slice(0, 2), ["/api/devices/dev-fixed-0001", "PATCH"]);
  const b = makeEnv({ platform: true }); t.after(b.close);
  b.w.document.body.dataset.auth = "1"; b.w.eval(DEVICE_SRC);
  assert.equal(b.w.GoppoDevices, undefined);
});


// ======================================================================= Phase 3: native events, audio, notifications
const devices = [{ id: "earpiece", type: "earpiece", name: "Phone" }, { id: "speaker", type: "speaker", name: "Speaker" }, { id: "bluetooth:7", type: "bluetooth", name: "Buds" }];

test("native audio events update the page state and are never echoed back to native", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA, nativeCaps: ["audioRouting"] }); t.after(e.close);
  await until(() => e.P.has("audioRouting"), 1000, "capabilities");
  const seen = []; e.w.addEventListener("goppo:audio", (ev) => seen.push(plain(ev.detail)));
  const before = e.native.events.length;
  await e.P.receive("audio:devicesChanged", { devices, selected: "earpiece", muted: false, backend: "telecom" });
  await e.P.receive("audio:routeChanged", { selected: "bluetooth:7" });
  await e.P.receive("audio:muteChanged", { muted: true });
  const st = plain(e.P.audio.state());
  assert.equal(st.devices.length, 3); assert.equal(st.selected, "bluetooth:7"); assert.equal(st.muted, true); assert.equal(st.backend, "telecom");
  assert.equal(seen.length, 3);
  assert.equal(e.native.events.length, before, "events FROM native must not be sent back TO native");
});

test("audio state ignores malformed devices and ids", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA, nativeCaps: ["audioRouting"] }); t.after(e.close);
  await e.P.receive("audio:devicesChanged", { devices: [{ id: "<script>", type: "x" }, { id: "ok-1", type: "speaker" }, null, { id: "x".repeat(80), type: "x" }, { id: "n", type: 5 }], selected: "../../etc" });
  const st = plain(e.P.audio.state());
  assert.deepEqual(st.devices.map((d) => d.id), ["ok-1"]); assert.equal(st.selected, null);
});

test("audio API calls the plugin with plain arguments and adopts the answer", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA, nativeCaps: ["audioRouting"] }); t.after(e.close);
  e.native.audio = { devices, selected: "earpiece", muted: false };
  await until(() => e.P.has("audioRouting"), 1000, "capabilities");
  assert.equal(e.P.audio.available(), true);
  assert.equal(plain(await e.P.audio.refresh()).devices.length, 3);
  assert.equal(await e.P.audio.select("speaker"), true);
  assert.deepEqual(plain(e.native.calls.find((c) => c[0] === "selectAudioDevice")[1]), { id: "speaker" });
  assert.equal(e.P.audio.state().selected, "speaker");
  await e.P.audio.setSpeaker(1);
  assert.deepEqual(plain(e.native.calls.find((c) => c[0] === "setSpeakerEnabled")[1]), { enabled: true });
  const b = makeEnv({ platform: true }); t.after(b.close);
  assert.equal(b.P.audio.available(), false); assert.equal(await b.P.audio.select("speaker"), false, "browser: nothing to do, no error");
});

test("notification API: status, one-time permission request, settings screens", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA }); t.after(e.close);
  e.native.notif = { enabled: true, permission: "denied", fullScreenIntent: "denied" };
  assert.equal(e.P.notifications.available(), true);
  assert.equal((await e.P.notifications.status()).permission, "denied");
  assert.equal((await e.P.notifications.request()).permission, "granted");
  await e.P.notifications.openSettings({ channel: "calls" });
  await e.P.notifications.openSettings({ target: "fullScreenIntent" });
  assert.deepEqual(plain(e.native.calls.filter((c) => c[0] === "openNotificationSettings").map((c) => c[1])), [{ channel: "calls" }, { target: "fullScreenIntent" }]);
  const b = makeEnv({ platform: true }); t.after(b.close);
  assert.equal(b.P.notifications.available(), false); assert.equal(await b.P.notifications.status(), null);
});

test("native call events reach the page as window events; ended/rejected clear the audio state", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA, nativeCaps: ["audioRouting", "nativeIncomingCall"] }); t.after(e.close);
  const got = []; ["incoming", "accepted", "rejected", "connected", "ended", "missed"].forEach((n) => e.w.addEventListener("goppo:native-call-" + n, (ev) => got.push([n, plain(ev.detail)])));
  await e.P.receive("audio:devicesChanged", { devices, selected: "speaker" });
  for (const n of ["incoming", "accepted", "connected", "missed"]) await e.P.receive("call:" + n, { call_id: "c1" });
  await e.P.receive("call:ended", { call_id: "c1" });
  assert.deepEqual(got.map((g) => g[0]), ["incoming", "accepted", "connected", "missed", "ended"]);
  assert.equal(e.P.audio.state().devices.length, 0); assert.equal(e.P.audio.state().selected, null);
});

test("a native call:incoming makes the page look for the call at once", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA, nativeCaps: ["nativeIncomingCall"] }); t.after(e.close);
  e.incoming = { call_id: "c9", call_type: "voice", conversation_id: 1, caller: { id: 7, full_name: "Bob" }, seconds_left: 50 };
  e.w.document.body.dataset.auth = "1";
  const before = e.calls.filter((c) => c.url === "/api/calling/incoming").length;
  await e.P.receive("call:incoming", { call_id: "c9" });
  await until(() => e.calls.filter((c) => c.url === "/api/calling/incoming").length > before, 1000, "poll");
});

test("incoming and connected events carry what the Android layer needs (peer, avatar, conversation)", async (t) => {
  const e = makeEnv({ platform: true, capacitor: true, userAgent: ANDROID_UA, nativeCaps: ["nativeIncomingCall"] }); t.after(e.close);
  e.w.dispatchEvent(new e.w.CustomEvent("goppo:incoming-call", { detail: { call_id: "c7", call_type: "video", conversation_id: 4, seconds_left: 40,
    caller: { id: 7, full_name: "Bob", avatar_url: "https://img.example/b.jpg" } } }));
  await until(() => e.native.events.some((m) => m.event === "call:incoming"), 1000, "incoming event");
  const inc = e.native.events.find((m) => m.event === "call:incoming").detail;
  assert.equal(inc.caller.avatar_url, "https://img.example/b.jpg"); assert.equal(inc.conversation_id, 4);
  // native Accept -> the normal accept flow runs once; a second Accept is refused (one WebRTC connection)
  const first = await e.P.receive("call:accept", { call_id: "c7" });
  const second = await e.P.receive("call:accept", { call_id: "c7" });
  assert.equal(first.result.ok, true); assert.equal(second.result.ok, false);
  await sleep(60);
  assert.ok(e.pcs.length <= 1, "never two peer connections for one call");
});
