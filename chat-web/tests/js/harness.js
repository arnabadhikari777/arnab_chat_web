// Loads app/static/js/calling.js into a jsdom window with a scripted fake network, fake WebRTC and fake WebSocket.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const SRC = fs.readFileSync(path.join(__dirname, "../../app/static/js/calling.js"), "utf8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class FakeTrack { constructor(kind) { this.kind = kind; this.enabled = true; this.stopped = false; } stop() { this.stopped = true; } }
class FakeStream {
  constructor(tracks) { this.t = tracks || []; }
  getTracks() { return this.t; }
  getAudioTracks() { return this.t.filter((x) => x.kind === "audio"); }
  getVideoTracks() { return this.t.filter((x) => x.kind === "video"); }
  addTrack(x) { this.t.push(x); }
  removeTrack(x) { this.t = this.t.filter((y) => y !== x); }
}

function makeEnv(opts = {}) {
  const dom = new JSDOM(`<!doctype html><html><head><meta name="csrf-token" content="tok123"></head><body>
    <div id="chat" data-peer="7"><header class="topbar"><div class="chat-head"><strong>Bob</strong></div></header></div></body></html>`,
    { url: "https://chat.example/messages/1", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  const env = { w, calls: [], sockets: [], pcs: [], statuses: [], ready: opts.ready || (() => ({ status: 200, body: { ready: true } })), mediaDenied: opts.mediaDenied || null };
  env.log = (m) => env.calls.push(m);

  // ---- fake fetch (Flask API + Render server) -----------------------------------------------------------------
  w.fetch = async (url, init = {}) => {
    const u = String(url);
    const method = (init.method || "GET").toUpperCase();
    const body = init.body ? JSON.parse(init.body) : null;
    env.log({ url: u, method, body, headers: init.headers || {} });
    if (init.signal && init.signal.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
    const reply = (status, data) => ({ ok: status >= 200 && status < 300, status, json: async () => data });
    if (u === "/api/calling/config") return reply(200, { ok: true, enabled: true, calling_server_url: "https://calls.test", wake_timeout_ms: opts.wakeTimeout || 400, health_poll_ms: 5, ring_seconds: 60 });
    if (u.startsWith("https://calls.test/ready")) { const r = env.ready(); if (r instanceof Error) throw r; return reply(r.status, r.body); }
    if (u.startsWith("https://calls.test/ice")) return reply(200, { iceServers: [{ urls: ["stun:s"] }, { urls: ["turn:t"], username: "u", credential: "c" }], turn_configured: true });
    if (u === "/api/calling/incoming") return reply(200, { ok: true, call: env.incoming || null });
    if (u === "/api/calling/initiate") return (opts.initiate || (() => reply(200, { ok: true, call_id: "call-1", conversation_id: 1, token: "tok-A", calling_server_url: "https://calls.test", ring_seconds: 60, peer: { id: 7, full_name: "Bob" } })))(reply, body);
    if (u === "/api/calling/token") { env.tokens = (env.tokens || 0) + 1; return reply(200, { ok: true, token: "tok-R" + env.tokens, calling_server_url: "https://calls.test" }); }
    if (u === "/api/calling/accept") return reply(200, { ok: true, token: "tok-C", calling_server_url: "https://calls.test", call_type: "voice" });
    if (u === "/api/calling/status") { env.statuses.push(body); return reply(200, { ok: true }); }
    if (u.startsWith("/api/calling/state/")) return reply(200, { ok: true, call: { status: env.serverStatus || "ringing" } });
    return reply(404, {});
  };

  // ---- fake WebSocket ---------------------------------------------------------------------------------------------
  w.WebSocket = class {
    constructor(url, protocols) {
      this.url = url; this.protocols = protocols; this.readyState = 0; this.sent = [];
      env.sockets.push(this);
      setTimeout(() => { if (this.refuse) return this._close(this.refuse); this.readyState = 1; if (opts.autoWelcome !== false) this.emit({ type: "welcome", resumed: false, peer: { online: !!opts.peerOnline, ready: false } }); }, 1);
    }
    send(s) { this.sent.push(JSON.parse(s)); }
    emit(m) { this.onmessage && this.onmessage({ data: JSON.stringify(m) }); }
    close() { this._close(1000); }
    _close(code) { if (this.readyState === 3) return; this.readyState = 3; this.onclose && this.onclose({ code }); }
    types() { return this.sent.map((m) => m.type); }
  };

  // ---- fake WebRTC ----------------------------------------------------------------------------------------------------
  w.MediaStream = FakeStream;
  w.RTCPeerConnection = class {
    constructor(cfg) { this.cfg = cfg; this.connectionState = "new"; this.iceConnectionState = "new"; this.remoteDescription = null; this.localDescription = null; this.added = []; this.tracks = []; this.senders = []; env.pcs.push(this); }
    addTrack(t) { this.tracks.push(t); const s = { track: t, replaceTrack: async (n) => { s.track = n; } }; this.senders.push(s); return s; }
    getSenders() { return this.senders; }
    async createOffer(o) { return { type: "offer", sdp: "v=0 offer" + (o && o.iceRestart ? " restart" : "") }; }
    async createAnswer() { return { type: "answer", sdp: "v=0 answer" }; }
    async setLocalDescription(d) { this.localDescription = d; }
    async setRemoteDescription(d) { this.remoteDescription = d; }
    async addIceCandidate(c) { if (!this.remoteDescription) throw new Error("no remote description"); this.added.push(c); }
    async getStats() { return new Map(); }
    close() { this.closed = true; }
    setState(s) { this.connectionState = s; this.onconnectionstatechange && this.onconnectionstatechange(); }
  };
  Object.defineProperty(w.navigator, "mediaDevices", { configurable: true, value: {
    getUserMedia: async (c) => { if (env.mediaDenied) throw Object.assign(new Error("x"), { name: env.mediaDenied }); env.lastConstraints = c; return new FakeStream([new FakeTrack("audio")].concat(c.video ? [new FakeTrack("video")] : [])); } } });
  Object.defineProperty(w, "isSecureContext", { value: true });
  w.AudioContext = undefined;

  w.eval(SRC);
  env.G = w.GoppoCalling;
  env.$ = (id) => w.document.getElementById(id);
  env.screen = () => (env.$("goppo-call-overlay") && !env.$("goppo-call-overlay").classList.contains("hidden")) ? env.$("goppo-call-overlay").querySelector(".goppo-call-panel").dataset.screen : null;
  env.urls = () => env.calls.map((c) => c.url);
  env.close = () => w.close();
  return env;
}

async function until(fn, ms = 2000, msg = "condition") {
  const end = Date.now() + ms;
  while (Date.now() < end) { try { const v = await fn(); if (v) return v; } catch (e) { /* retry */ } await sleep(5); }
  throw new Error("timeout waiting for " + msg);
}

module.exports = { makeEnv, until, sleep, FakeStream, FakeTrack };
