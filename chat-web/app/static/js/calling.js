/**
 * Goppo voice & video calling client.
 *
 * Flow (caller):  microphone/camera permission -> wake & wait for the Render server (/ready, bounded, Retry/Cancel)
 *                 -> POST /api/calling/initiate (rings the callee) -> /ice -> WebSocket -> offer ...
 * Flow (callee):  incoming screen (poll / push / notification click) -> Accept -> permission -> wake ->
 *                 POST /api/calling/accept -> /ice -> WebSocket -> answer
 *
 * Signaling is reliable: every message carries an id, is acknowledged ("queued") and later reported
 * "delivered" by the server only after the other browser acknowledged it. Unacknowledged messages are re-sent
 * after a reconnect; received messages are de-duplicated by their server sequence number.
 * Media is peer-to-peer WebRTC (DTLS-SRTP); nothing here weakens it.
 */
(function () {
  "use strict";

  const csrf = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const $ = (id) => { try { return document.getElementById(id); } catch (e) { return null; } };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const uid = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID() : "m" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10));
  const pad = (n) => String(n).padStart(2, "0");
  const fmt = (sec) => pad(Math.floor(sec / 60)) + ":" + pad(sec % 60);

  const S = {
    cfg: null,            // /api/calling/config
    call: null,           // the one live call (null = idle)
    wake: null,           // shared wake-up promise (single flight)
    shownIncoming: new Set(),
    pollTimer: null,
    ringTimer: null,
    audioCtx: null,
  };

  // ------------------------------------------------------------------ small helpers
  async function api(path, opts = {}) {
    const init = { credentials: "same-origin", headers: { Accept: "application/json" }, method: opts.method || "GET" };
    if (opts.body !== undefined) {
      init.method = "POST";
      init.headers["Content-Type"] = "application/json";
      init.headers["X-CSRFToken"] = csrf();
      init.body = JSON.stringify(opts.body);
    }
    try {
      const res = await fetch("/api/calling" + path, init);
      const data = await res.json().catch(() => ({}));
      return { ok: res.ok && data.ok !== false, status: res.status, data };
    } catch (e) {
      return { ok: false, status: 0, data: { error: "network" } };
    }
  }

  async function loadConfig() {
    if (S.cfg) return S.cfg;
    const r = await api("/config");
    S.cfg = r.ok ? r.data : { enabled: false };
    return S.cfg;
  }

  function wsUrl(base) {
    return base.replace(/^http/i, "ws").replace(/\/$/, "") + "/ws";
  }

  // ------------------------------------------------------------------ UI
  function ensureUI() {
    if (typeof document === "undefined" || !document || !document.body) return false;   // page is going away
    if ($("goppo-call-overlay")) return true;
    const o = document.createElement("div");
    o.id = "goppo-call-overlay";
    o.className = "goppo-call-overlay hidden";
    o.setAttribute("role", "dialog");
    o.setAttribute("aria-modal", "true");
    o.innerHTML = `
      <div class="goppo-call-panel" data-screen="">
        <div class="goppo-call-videos" id="goppo-call-videos">
          <video id="goppo-remote-video" autoplay playsinline></video>
          <video id="goppo-local-video" autoplay playsinline muted></video>
        </div>
        <div class="goppo-call-top">
          <img id="goppo-call-avatar" class="goppo-call-avatar" alt="" hidden>
          <div class="goppo-call-name" id="goppo-call-name"></div>
          <div class="goppo-call-status" id="goppo-call-status" aria-live="polite"></div>
          <div class="goppo-call-timer" id="goppo-call-timer" hidden>00:00</div>
          <div class="goppo-call-quality" id="goppo-call-quality" hidden title="Network quality">
            <i></i><i></i><i></i><i></i><span id="goppo-call-quality-text"></span>
          </div>
          <div class="goppo-call-hint" id="goppo-call-hint" hidden></div>
        </div>
        <div class="goppo-call-spinner" id="goppo-call-spinner" hidden></div>
        <div class="goppo-call-actions" id="goppo-call-actions">
          <button type="button" class="goppo-call-btn" id="goppo-btn-mute" aria-label="Mute microphone" aria-pressed="false">🎤</button>
          <button type="button" class="goppo-call-btn" id="goppo-btn-cam" aria-label="Turn camera off" aria-pressed="false">📷</button>
          <button type="button" class="goppo-call-btn" id="goppo-btn-flip" aria-label="Switch camera">🔄</button>
          <button type="button" class="goppo-call-btn" id="goppo-btn-full" aria-label="Full screen">⛶</button>
          <button type="button" class="goppo-call-btn goppo-call-btn--end" id="goppo-btn-end" aria-label="End call">📵</button>
        </div>
        <div class="goppo-call-incoming" id="goppo-call-incoming">
          <button type="button" class="goppo-call-btn goppo-call-btn--end" id="goppo-btn-reject" aria-label="Reject"><span>✖</span><small>Reject</small></button>
          <button type="button" class="goppo-call-btn goppo-call-btn--accept" id="goppo-btn-accept" aria-label="Accept"><span>✔</span><small>Accept</small></button>
        </div>
        <div class="goppo-call-extra" id="goppo-call-extra">
          <button type="button" class="btn" id="goppo-btn-retry" hidden>Retry</button>
          <button type="button" class="btn btn--ghost" id="goppo-btn-cancel">Cancel</button>
          <button type="button" class="btn" id="goppo-btn-close" hidden>Close</button>
        </div>
      </div>`;
    document.body.appendChild(o);
    $("goppo-btn-mute").addEventListener("click", toggleMute);
    $("goppo-btn-cam").addEventListener("click", toggleCamera);
    $("goppo-btn-flip").addEventListener("click", flipCamera);
    $("goppo-btn-full").addEventListener("click", toggleFullscreen);
    $("goppo-btn-end").addEventListener("click", () => hangup("user"));
    $("goppo-btn-accept").addEventListener("click", acceptIncoming);
    $("goppo-btn-reject").addEventListener("click", rejectIncoming);
    $("goppo-btn-cancel").addEventListener("click", () => cancelFlow());
    $("goppo-btn-retry").addEventListener("click", () => retryFlow());
    $("goppo-btn-close").addEventListener("click", closeOverlay);
    return true;
  }

  /** screen: wake | calling | incoming | active | ended */
  function screen(name, opts = {}) {
    if (!ensureUI()) return;
    const o = $("goppo-call-overlay");
    o.classList.remove("hidden");
    o.querySelector(".goppo-call-panel").dataset.screen = name;
    const c = S.call;
    if (c && c.peer) {
      $("goppo-call-name").textContent = c.peer.full_name || c.peer.username || "";
      const av = $("goppo-call-avatar");
      if (c.peer.avatar_url) { av.src = c.peer.avatar_url; av.hidden = false; } else { av.hidden = true; }
    }
    $("goppo-call-spinner").hidden = !(name === "wake" || name === "calling" || opts.spinner);
    $("goppo-btn-retry").hidden = !opts.retry;
    $("goppo-btn-cancel").hidden = !(name === "wake" || name === "calling" || name === "incoming-connecting");
    $("goppo-btn-close").hidden = name !== "ended";
    const video = !!(c && c.type === "video" && name === "active");
    o.classList.toggle("has-video", video);
    $("goppo-btn-cam").hidden = !(c && c.type === "video");
    $("goppo-btn-flip").hidden = !(c && c.type === "video" && opts.canFlip !== false);
    if (opts.status !== undefined) status(opts.status);
  }

  function status(text) { const e = $("goppo-call-status"); if (e) e.textContent = text; if (S.call) S.call.statusText = text; }
  function hint(text) { const e = $("goppo-call-hint"); if (!e) return; e.textContent = text || ""; e.hidden = !text; }
  function closeOverlay() {
    const o = $("goppo-call-overlay");
    if (o) o.classList.add("hidden");
    stopRing();
    if (S.call && S.call.ended) S.call = null;
  }

  function toast(msg) {
    if (window.GoppoApp && window.GoppoApp.toast) { window.GoppoApp.toast(msg); return; }
    hint(msg);
  }

  // ------------------------------------------------------------------ ring tone (WebAudio, no files)
  function startRing(kind) {
    stopRing();
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      S.audioCtx = S.audioCtx || new AC();
      const beep = () => {
        const ctx = S.audioCtx, osc = ctx.createOscillator(), g = ctx.createGain();
        osc.frequency.value = kind === "incoming" ? 520 : 425;
        g.gain.value = 0.06;
        osc.connect(g); g.connect(ctx.destination);
        osc.start(); osc.stop(ctx.currentTime + 0.35);
      };
      beep();
      S.ringTimer = setInterval(() => { beep(); if (kind === "incoming" && navigator.vibrate) navigator.vibrate([250, 150, 250]); }, 2000);
    } catch (e) { /* autoplay policy: silent is fine, the screen still shows */ }
  }
  function stopRing() { if (S.ringTimer) { clearInterval(S.ringTimer); S.ringTimer = null; } }

  // ------------------------------------------------------------------ media
  function mediaError(e) {
    if (!window.isSecureContext && location.hostname !== "localhost") return "Calls need a secure (https) connection.";
    switch (e && e.name) {
      case "NotAllowedError": case "SecurityError": return "Permission denied. Allow the microphone" + " (and camera) for this site in your browser settings.";
      case "NotFoundError": case "OverconstrainedError": return "No microphone or camera was found on this device.";
      case "NotReadableError": return "Your microphone or camera is being used by another app.";
      default: return "Could not access the microphone or camera.";
    }
  }

  async function getMedia(type, facing) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw Object.assign(new Error("unsupported"), { name: "SecurityError" });
    const constraints = { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } };
    if (type === "video") constraints.video = { facingMode: facing || "user", width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30, max: 30 } };
    return navigator.mediaDevices.getUserMedia(constraints);
  }

  // ------------------------------------------------------------------ server wake-up (bounded, single flight)
  async function fetchWithTimeout(url, ms, signal, init) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), ms);
    const onAbort = () => ctl.abort();
    if (signal) signal.addEventListener("abort", onAbort);
    try { return await fetch(url, Object.assign({ signal: ctl.signal, cache: "no-store", mode: "cors", credentials: "omit" }, init || {})); }
    finally { clearTimeout(timer); if (signal) signal.removeEventListener("abort", onAbort); }
  }

  /** Resolves when /ready answers 200. Rejects with {code:"timeout"|"cancelled"|"unavailable"}. Never waits forever. */
  function ensureServerReady(onProgress, signal) {
    if (S.wake && S.wake.promise) return S.wake.promise;   // a second click must not start a second wake-up
    const started = Date.now();
    const promise = (async () => {
      const cfg = await loadConfig();
      const base = (cfg.calling_server_url || "").replace(/\/$/, "");
      const limit = cfg.wake_timeout_ms || 90000;
      const every = cfg.health_poll_ms || 2000;
      if (!base) throw { code: "unavailable" };
      let attempt = 0;
      while (true) {
        if (signal && signal.aborted) throw { code: "cancelled" };
        const elapsed = Date.now() - started;
        if (elapsed > limit) throw { code: "timeout" };
        attempt += 1;
        try {
          const res = await fetchWithTimeout(base + "/ready", Math.min(8000, limit - elapsed + 500), signal);
          if (res.ok) { onProgress && onProgress({ state: "ready", elapsed }); return true; }
          onProgress && onProgress({ state: res.status === 503 ? "starting" : "waking", elapsed, attempt });
        } catch (e) {
          if (signal && signal.aborted) throw { code: "cancelled" };
          onProgress && onProgress({ state: "waking", elapsed, attempt });
        }
        await sleep(Math.min(every * Math.min(attempt, 3), 5000));   // gentle back-off: 2s, 4s, 6s, ... capped at 5s
      }
    })();
    S.wake = { promise };
    const clear = () => { S.wake = null; };
    promise.then(clear, clear);
    return promise;
  }

  // ------------------------------------------------------------------ starting a call (caller)
  async function start(peerId, type, peerInfo) {
    if (S.call || S.starting) { toast("You are already in a call."); return null; }   // double taps create ONE call
    S.starting = true;
    let cfg;
    try { cfg = await loadConfig(); } finally { S.starting = false; }
    if (S.call) return null;
    if (!cfg.enabled) { toast("Calling is not available right now."); return null; }
    const call = S.call = {
      role: "caller", type, peer: Object.assign({ id: peerId }, peerInfo || {}), peerId, seq: 0, outbox: new Map(), lastSeq: 0,
      pendingIce: [], reconnects: 0, state: "preparing", abort: new AbortController(), facing: "user", ended: false,
    };
    screen("wake", { status: "Checking permissions…" });
    return runCallerFlow(call);
  }

  async function runCallerFlow(call) {
    try {
      if (!call.stream) {
        screen("wake", { status: "Allow microphone" + (call.type === "video" ? " and camera" : "") + " access…" });
        try { call.stream = await getMedia(call.type, call.facing); }
        catch (e) { return failFlow(call, mediaError(e), false); }
      }
      screen("wake", { status: "Connecting to calling server…" });
      try {
        await ensureServerReady((p) => {
          const s = Math.round(p.elapsed / 1000);
          if (p.state === "ready") status("Server ready");
          else status(p.state === "starting" ? "Server is starting up… " + s + "s" : "Waking up the calling server (can take up to a minute)… " + s + "s");
        }, call.abort.signal);
      } catch (e) {
        if (e.code === "cancelled") return;
        return failFlow(call, "The calling server did not respond in time. Check your internet and try again.", true);
      }
      if (call.ended) return;
      if (!call.callId) {
        status("Calling…");
        const r = await api("/initiate", { body: { peer_id: call.peerId, call_type: call.type } });
        if (!r.ok) {
          const d = r.data || {};
          if (r.status === 409 && d.peer_busy) return endUi(call, "busy", "The other person is busy in another call.");
          if (r.status === 409) return endUi(call, "failed", "You are already in a call.");
          if (r.status === 403) return endUi(call, "failed", "You can only call people you already chat with.");
          return failFlow(call, d.error && d.error !== "network" ? String(d.error) : "Could not start the call. Check your connection.", true);
        }
        Object.assign(call, { callId: r.data.call_id, convId: r.data.conversation_id, token: r.data.token, tokenAt: Date.now(),
          serverUrl: r.data.calling_server_url, ringSeconds: r.data.ring_seconds || 60, peer: Object.assign(call.peer, r.data.peer) });
      }
      screen("calling", { status: "Connecting…" });
      await connectSignaling(call);
    } catch (e) {
      if (!call.ended) failFlow(call, "Could not connect the call.", true);
    }
  }

  function failFlow(call, message, retry) {
    if (call.ended) return;
    call.state = "failed-flow";
    stopRing();
    screen("wake", { status: message, retry, spinner: false });
    $("goppo-call-spinner").hidden = true;
  }

  function retryFlow() {
    const c = S.call;
    if (!c || c.ended) return;
    c.abort = new AbortController();
    $("goppo-btn-retry").hidden = true;
    (c.role === "caller" ? runCallerFlow : runCalleeFlow)(c);
  }

  async function cancelFlow() {
    const c = S.call;
    if (!c) return closeOverlay();
    if (c.abort) c.abort.abort();
    if (c.callId && c.role === "caller") await api("/status", { body: { call_id: c.callId, status: c.state === "connected" ? "completed" : "cancelled" } });
    if (c.callId && c.role === "callee") await api("/status", { body: { call_id: c.callId, status: "rejected" } });
    teardown(c);
    S.call = null;
    closeOverlay();
  }

  // ------------------------------------------------------------------ signaling
  async function freshToken(call, force) {
    const age = Date.now() - (call.tokenAt || 0);
    if (!force && call.token && age < 60000) return call.token;       // tokens live 120 s: refresh when older than 60 s
    const r = await api("/token", { body: { call_id: call.callId } });
    if (!r.ok) throw Object.assign(new Error("token"), { status: r.status, data: r.data });
    call.token = r.data.token; call.tokenAt = Date.now();
    if (r.data.calling_server_url) call.serverUrl = r.data.calling_server_url;
    return call.token;
  }

  async function loadIce(call) {
    try {
      const res = await fetchWithTimeout((call.serverUrl || S.cfg.calling_server_url).replace(/\/$/, "") + "/ice", 8000, call.abort.signal,
        { headers: { "X-Call-Token": call.token } });
      if (res.ok) { const d = await res.json(); call.iceServers = d.iceServers || []; call.turn = !!d.turn_configured; return; }
    } catch (e) { /* fall through */ }
    call.iceServers = [{ urls: ["stun:stun.l.google.com:19302"] }];   // STUN only: works on easy networks, no relay
    call.turn = false;
  }

  function openSocket(call, token) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const ws = new WebSocket(wsUrl(call.serverUrl || S.cfg.calling_server_url), ["goppo.v1", "auth." + token]);
      call.ws = ws;
      const timer = setTimeout(() => { if (!settled) { settled = true; try { ws.close(); } catch (e) {} reject(Object.assign(new Error("ws-timeout"), { code: "timeout" })); } }, 10000);
      ws.onmessage = (ev) => {
        let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (m.type === "welcome" && !settled) { settled = true; clearTimeout(timer); resolve(m); }
        onSignal(call, ws, m);
      };
      ws.onerror = () => {};
      ws.onclose = (ev) => {
        clearTimeout(timer);
        if (!settled) { settled = true; reject(Object.assign(new Error("ws-closed"), { code: ev.code || 0 })); return; }
        if (call.ws === ws) onSocketClosed(call, ev);
      };
    });
  }

  async function connectSignaling(call) {
    await freshToken(call, false);
    await loadIce(call);
    let welcome;
    try { welcome = await openSocket(call, call.token); }
    catch (e) { return handleConnectFailure(call, e); }
    call.reconnects = 0;
    call.peerOnline = !!(welcome.peer && welcome.peer.online);
    if (!call.pc) await createPeerConnection(call);
    if (call.early && call.early.length) { const e = call.early.splice(0); for (const m of e) await handleRemote(call, m); }
    for (const [, m] of call.outbox) if (!m.queued) wsSend(call, m.msg);       // re-send anything the server never confirmed
    if (call.role === "caller" && !call.offerSent) await makeOffer(call, false);
    else if (call.role === "callee") sendSignal(call, "ready", null, true);
    if (call.role === "caller") { screen("calling", { status: call.peerOnline ? "Ringing…" : "Calling…" }); startRing("outgoing"); }
    else screen("calling", { status: "Connecting…" });
  }

  function handleConnectFailure(call, e) {
    if (call.ended) return;
    return (async () => {
      let code = 0;
      try { const t = await api("/state/" + call.callId); code = t.ok ? 0 : t.status; if (t.ok && ["completed", "missed", "rejected", "busy", "failed", "cancelled"].includes(t.data.call.status)) return endUi(call, t.data.call.status); } catch (x) {}
      call.reconnects += 1;
      if (call.reconnects <= 3) { status("Reconnecting… (" + call.reconnects + ")"); await sleep(1000 * call.reconnects); try { await freshToken(call, true); return connectSignaling(call); } catch (x) { return handleConnectFailure(call, x); } }
      failFlow(call, "Could not reach the calling server.", true);
    })();
  }

  function onSocketClosed(call, ev) {
    if (call.ended || call.closing) return;
    if (ev && ev.code === 4001) { endUi(call, "failed", "This call was opened on another device or tab."); return; }
    status("Connection lost. Reconnecting…");
    reconnect(call);
  }

  async function reconnect(call) {
    if (call.reconnecting) return;
    call.reconnecting = true;
    const until = Date.now() + 25000;               // the server keeps the call for a 20 s grace period
    let delay = 500;
    while (!call.ended && Date.now() < until) {
      await sleep(delay); delay = Math.min(delay * 2, 4000);
      try {
        await freshToken(call, true);              // a token opens only one socket: always a new one
        const welcome = await openSocket(call, call.token);
        call.reconnecting = false; call.peerOnline = !!(welcome.peer && welcome.peer.online);
        for (const [, m] of call.outbox) if (!m.queued) wsSend(call, m.msg);
        if (call.state === "connected") status("Connected");
        return;
      } catch (e) {
        if (e.status === 409 || e.status === 404) break;        // the call has ended on the server
        if (e.code === 410) break;
      }
    }
    call.reconnecting = false;
    if (!call.ended) endUi(call, "failed", "Connection lost.");
  }

  function wsSend(call, msg) { if (call.ws && call.ws.readyState === 1) { call.ws.send(JSON.stringify(msg)); return true; } return false; }

  /** Reliable send: kept in the outbox until the server confirms it queued it ("queued"). */
  function sendSignal(call, type, payload, direct) {
    const msg = { type };
    if (direct) { wsSend(call, msg); return; }
    msg.id = uid(); msg.payload = payload;
    call.outbox.set(msg.id, { msg, queued: false, delivered: false, kind: type });
    wsSend(call, msg);
    return msg.id;
  }

  function onSignal(call, ws, m) {
    if (call.ended) return;
    switch (m.type) {
      case "queued": { const o = call.outbox.get(m.id); if (o) o.queued = true; break; }
      case "delivered": { const o = call.outbox.get(m.id); if (o) { o.delivered = true; if (o.kind === "offer") call.offerDelivered = true; } call.outbox.delete(m.id); break; }
      case "expired": { if (m.kind === "offer") endUi(call, "missed", "No answer."); break; }
      case "error": { if (m.id) call.outbox.delete(m.id); break; }
      case "peer_online": call.peerOnline = true; if (call.state !== "connected") status(call.role === "caller" ? "Ringing…" : "Connecting…"); break;
      case "peer_offline": call.peerOnline = false; if (call.state === "connected") status("Other person lost connection. Waiting…"); break;
      case "peer_left": break;
      case "session_end": if (m.reason !== "replaced") endUi(call, reasonToStatus(m.reason), reasonText(m.reason)); break;
      case "quality_report": onQuality(call, m); break;
      case "offer": case "answer": case "ice-candidate": case "hangup": case "reject": case "busy":
        if (typeof m.seq === "number") {
          ws.send(JSON.stringify({ type: "ack", seq: m.seq }));              // acknowledge FIRST: that is the delivery proof
          if (m.seq <= call.lastSeq) return;                                  // already processed (server re-sent)
          call.lastSeq = m.seq;
        }
        handleRemote(call, m);
        break;
      default: break;
    }
  }

  function reasonToStatus(r) { return r === "timeout" ? "missed" : r === "busy" ? "busy" : "failed"; }
  function reasonText(r) { return ({ timeout: "No answer.", peer_dropped: "The other person lost connection.", max_duration: "Call time limit reached.", server_shutdown: "Server restarting. Please call again.", busy: "The other person is busy." })[r] || "Call ended."; }

  // ------------------------------------------------------------------ WebRTC
  async function createPeerConnection(call) {
    const pc = new RTCPeerConnection({ iceServers: call.iceServers || [], bundlePolicy: "max-bundle" });
    call.pc = pc;
    call.remoteStream = new MediaStream();
    const rv = $("goppo-remote-video"); if (rv) rv.srcObject = call.remoteStream;
    const lv = $("goppo-local-video"); if (lv && call.type === "video") lv.srcObject = call.stream;
    call.stream.getTracks().forEach((t) => pc.addTrack(t, call.stream));
    pc.ontrack = (ev) => { call.remoteStream.addTrack(ev.track); const v = $("goppo-remote-video"); if (v) { v.srcObject = call.remoteStream; const p = v.play && v.play(); if (p && p.catch) p.catch(() => {}); } };
    pc.onicecandidate = (ev) => {
      if (!ev.candidate) { sendSignal(call, "ice-candidate", { candidate: "" }); return; }
      const c = ev.candidate;
      sendSignal(call, "ice-candidate", { candidate: c.candidate, sdpMid: c.sdpMid, sdpMLineIndex: c.sdpMLineIndex });
    };
    pc.onconnectionstatechange = () => onPcState(call);
    pc.oniceconnectionstatechange = () => onIceState(call);
  }

  async function makeOffer(call, iceRestart) {
    const offer = await call.pc.createOffer(iceRestart ? { iceRestart: true } : {});
    await call.pc.setLocalDescription(offer);
    call.offerSent = true;
    sendSignal(call, "offer", { sdp: call.pc.localDescription.sdp });
  }

  async function handleRemote(call, m) {
    const pc = call.pc;
    try {
      if (m.type === "offer") {
        if (!pc) { call.early = (call.early || []).concat([m]); return; }   // already acknowledged: keep it until the peer connection exists
        await pc.setRemoteDescription({ type: "offer", sdp: m.payload.sdp });
        await flushIce(call);
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        sendSignal(call, "answer", { sdp: pc.localDescription.sdp });
      } else if (m.type === "answer") {
        await pc.setRemoteDescription({ type: "answer", sdp: m.payload.sdp });
        await flushIce(call);
        stopRing();
        status("Connecting…");
      } else if (m.type === "ice-candidate") {
        const p = m.payload || {};
        if (!p.candidate) return;                                  // end-of-candidates marker
        if (!pc || !pc.remoteDescription) { call.pendingIce.push(p); return; }   // buffer until the remote description exists
        await pc.addIceCandidate({ candidate: p.candidate, sdpMid: p.sdpMid, sdpMLineIndex: p.sdpMLineIndex });
      } else if (m.type === "hangup") endUi(call, call.state === "connected" ? "completed" : "cancelled", call.state === "connected" ? "Call ended." : "The caller cancelled.", true);
      else if (m.type === "reject") endUi(call, "rejected", "Call declined.", true);
      else if (m.type === "busy") endUi(call, "busy", "The other person is busy.", true);
    } catch (e) { /* a bad candidate or a race must never kill the call */ }
  }

  async function flushIce(call) {
    const list = call.pendingIce.splice(0);
    for (const p of list) { try { await call.pc.addIceCandidate({ candidate: p.candidate, sdpMid: p.sdpMid, sdpMLineIndex: p.sdpMLineIndex }); } catch (e) {} }
  }

  function onPcState(call) {
    const st = call.pc && call.pc.connectionState;
    if (st === "connected") {
      stopRing();
      clearTimeout(call.iceTimer);
      if (call.state !== "connected") {
        call.state = "connected"; call.connectedAt = Date.now();
        screen("active", { status: "Connected" });
        startTimer(call); startStats(call);
        api("/status", { body: { call_id: call.callId, status: "connected" } });
      } else status("Connected");
    } else if (st === "failed") {
      if (call.role === "caller" && !call.restarting) { call.restarting = true; status("Reconnecting…"); makeOffer(call, true).finally(() => setTimeout(() => { call.restarting = false; }, 5000)); }
      else if (call.state !== "connected") endUi(call, "failed", "Could not connect. A relay (TURN) server may be needed for your network.");
    }
  }

  function onIceState(call) {
    const st = call.pc && call.pc.iceConnectionState;
    if (st === "disconnected" && call.state === "connected") {
      status("Network problem. Reconnecting…");
      clearTimeout(call.iceTimer);
      call.iceTimer = setTimeout(() => { if (call.role === "caller" && call.pc && call.pc.iceConnectionState !== "connected" && !call.ended) makeOffer(call, true); }, 4000);
    }
    if (st === "connected" || st === "completed") { if (call.state === "connected") status("Connected"); }
  }

  // ------------------------------------------------------------------ in-call controls
  function startTimer(call) {
    const el = $("goppo-call-timer"); if (!el) return;
    el.hidden = false; clearInterval(call.timer);
    call.timer = setInterval(() => { el.textContent = fmt(Math.floor((Date.now() - call.connectedAt) / 1000)); }, 1000);
    el.textContent = "00:00";
  }

  function toggleMute() {
    const c = S.call; if (!c || !c.stream) return;
    const t = c.stream.getAudioTracks()[0]; if (!t) return;
    t.enabled = !t.enabled;
    $("goppo-btn-mute").setAttribute("aria-pressed", String(!t.enabled));
    $("goppo-btn-mute").textContent = t.enabled ? "🎤" : "🔇";
    $("goppo-btn-mute").setAttribute("aria-label", t.enabled ? "Mute microphone" : "Unmute microphone");
  }

  function toggleCamera() {
    const c = S.call; if (!c || !c.stream) return;
    const t = c.stream.getVideoTracks()[0]; if (!t) return;
    t.enabled = !t.enabled;
    $("goppo-btn-cam").setAttribute("aria-pressed", String(!t.enabled));
    $("goppo-btn-cam").setAttribute("aria-label", t.enabled ? "Turn camera off" : "Turn camera on");
    $("goppo-call-overlay").classList.toggle("cam-off", !t.enabled);
  }

  async function flipCamera() {
    const c = S.call; if (!c || c.type !== "video" || !c.pc) return;
    const next = c.facing === "user" ? "environment" : "user";
    try {
      const ns = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { exact: next } }, audio: false });
      const track = ns.getVideoTracks()[0];
      const sender = c.pc.getSenders().find((s) => s.track && s.track.kind === "video");
      if (sender) await sender.replaceTrack(track);
      const old = c.stream.getVideoTracks()[0]; if (old) { c.stream.removeTrack(old); old.stop(); }
      c.stream.addTrack(track);
      c.facing = next;
      const lv = $("goppo-local-video"); if (lv) { lv.srcObject = c.stream; lv.classList.toggle("mirror", next === "user"); }
    } catch (e) { toast("This device has no second camera."); }
  }

  function toggleFullscreen() {
    const el = $("goppo-call-overlay");
    if (document.fullscreenElement) document.exitFullscreen(); else if (el.requestFullscreen) el.requestFullscreen().catch(() => {});
  }

  // ------------------------------------------------------------------ network quality (browser stats -> C++/Rust on the server)
  function startStats(call) {
    clearInterval(call.statsTimer);
    let prev = null;
    call.statsTimer = setInterval(async () => {
      if (!call.pc || call.ended) return;
      try {
        const rep = await call.pc.getStats();
        const cur = { rtt: 0, jitter: 0, lost: 0, recv: 0, bytes: 0, at: Date.now(), aPlay: 0, vPlay: 0, jbDelay: 0, jbCount: 0, peak: -1 };
        rep.forEach((s) => {
          if (s.type === "candidate-pair" && (s.nominated || s.state === "succeeded") && typeof s.currentRoundTripTime === "number") cur.rtt = s.currentRoundTripTime * 1000;
          if (s.type === "inbound-rtp") {
            cur.jitter = Math.max(cur.jitter, (s.jitter || 0) * 1000); cur.lost += s.packetsLost || 0; cur.recv += s.packetsReceived || 0; cur.bytes += s.bytesReceived || 0;
            if (s.kind === "audio") cur.aPlay = s.estimatedPlayoutTimestamp || 0; else if (s.kind === "video") cur.vPlay = s.estimatedPlayoutTimestamp || 0;
            cur.jbDelay += s.jitterBufferDelay || 0; cur.jbCount += s.jitterBufferEmittedCount || 0;
          }
          if (s.type === "media-source" && s.kind === "audio" && typeof s.audioLevel === "number") cur.peak = Math.min(1, s.audioLevel);
        });
        if (prev) {
          const dRecv = cur.recv - prev.recv, dLost = Math.max(0, cur.lost - prev.lost);
          const loss = dRecv + dLost > 0 ? (dLost / (dRecv + dLost)) * 1000 : 0;
          const kbps = ((cur.bytes - prev.bytes) * 8) / Math.max(1, cur.at - prev.at);
          const jb = cur.jbCount > prev.jbCount ? ((cur.jbDelay - prev.jbDelay) / (cur.jbCount - prev.jbCount)) * 1000 : 0;
          wsSend(call, { type: "quality", payload: { rtt_ms: cur.rtt, jitter_ms: cur.jitter, loss_permille: Math.min(1000, loss), bitrate_kbps: Math.max(0, kbps),
            audio_playout_ms: cur.aPlay, video_playout_ms: cur.vPlay, jb_delay_ms: jb, audio_peak: cur.peak } });
        }
        prev = cur;
      } catch (e) { /* stats are best effort */ }
    }, 2000);
  }

  function onQuality(call, m) {
    const r = m.report || {}, el = $("goppo-call-quality"); if (!el) return;
    el.hidden = false;
    const level = r.level || 0, bars = 4 - level;
    el.dataset.level = String(level);
    el.querySelectorAll("i").forEach((b, i) => b.classList.toggle("on", i < bars));
    $("goppo-call-quality-text").textContent = ["Good", "Fair", "Poor", "Bad"][level] || "";
    call.lastQuality = m;
    if (r.advise_audio_only && call.type === "video" && !call.videoSuspended) {
      call.videoSuspended = true;
      const t = call.stream.getVideoTracks()[0]; if (t) t.enabled = false;
      hint("Weak connection: video paused to keep the call going.");
    } else if (!r.advise_reduce_video && call.videoSuspended && level <= 1) {
      call.videoSuspended = false;
      const t = call.stream.getVideoTracks()[0]; if (t) t.enabled = !$("goppo-call-overlay").classList.contains("cam-off");
      hint("");
    } else if (r.silent_mic) hint("Your microphone seems silent. Check that it is not muted or blocked.");
    else if (!call.videoSuspended) hint("");
  }

  // ------------------------------------------------------------------ ending
  async function hangup(reason) {
    const c = S.call; if (!c || c.ended) return closeOverlay();
    const wasConnected = c.state === "connected";
    if (c.ws && c.ws.readyState === 1) sendSignal(c, "hangup", {});
    await sleep(120);
    const status_ = wasConnected ? "completed" : c.role === "caller" ? "cancelled" : "rejected";
    if (c.callId) await api("/status", { body: { call_id: c.callId, status: status_ } });
    endUi(c, status_, wasConnected ? "Call ended." : "Call cancelled.", true);
  }

  async function rejectIncoming() {
    const c = S.call; if (!c || c.role !== "callee") return;
    await api("/status", { body: { call_id: c.callId, status: "rejected" } });
    endUi(c, "rejected", "Call declined.", true, true);
  }

  function teardown(call) {
    call.ended = true; call.closing = true;
    clearInterval(call.timer); clearInterval(call.statsTimer); clearTimeout(call.iceTimer);
    try { call.ws && call.ws.close(1000, "bye"); } catch (e) {}
    try { call.pc && call.pc.close(); } catch (e) {}
    try { call.stream && call.stream.getTracks().forEach((t) => t.stop()); } catch (e) {}
    stopRing();
  }

  /** Final screen. `silent` skips the "ended" screen (e.g. a declined incoming call). */
  function endUi(call, finalStatus, text, localDone, silent) {
    if (call.ended && call.endShown) return;
    const duration = call.connectedAt ? Math.floor((Date.now() - call.connectedAt) / 1000) : 0;
    teardown(call);
    call.endShown = true;
    if (silent) { S.call = null; closeOverlay(); return; }
    screen("ended", { status: text || "Call ended." });
    const t = $("goppo-call-timer"); if (t) { t.hidden = !duration; t.textContent = fmt(duration); }
    const q = $("goppo-call-quality"); if (q) q.hidden = true;
    hint(finalStatus === "missed" ? "Missed call" : "");
    $("goppo-call-spinner").hidden = true;
    const ov = $("goppo-call-overlay"); ov.classList.remove("has-video");
    call.finalStatus = finalStatus;
  }

  // ------------------------------------------------------------------ incoming calls
  function showIncoming(info) {
    if (!info || !info.call_id) return;
    if (S.shownIncoming.has(info.call_id)) return;                 // duplicate events (poll + push + click) show one screen
    if (S.call) {                                                    // already busy: tell the caller, do not interrupt
      S.shownIncoming.add(info.call_id);
      api("/status", { body: { call_id: info.call_id, status: "busy" } });
      return;
    }
    S.shownIncoming.add(info.call_id);
    S.call = { role: "callee", type: info.call_type, callId: info.call_id, convId: info.conversation_id, peer: info.caller || {}, peerId: (info.caller || {}).id,
      lastSeq: 0, outbox: new Map(), pendingIce: [], reconnects: 0, state: "incoming", abort: new AbortController(), facing: "user", ended: false };
    screen("incoming", { status: (info.call_type === "video" ? "Incoming video call" : "Incoming voice call"), spinner: false });
    startRing("incoming");
    S.call.expireTimer = setTimeout(() => {
      const c = S.call;
      if (c && c.callId === info.call_id && c.state === "incoming") { stopRing(); toast("Missed call from " + (c.peer.full_name || "")); S.call = null; closeOverlay(); }
    }, Math.max(3, info.seconds_left || 60) * 1000);
  }

  async function acceptIncoming() {
    const c = S.call; if (!c || c.role !== "callee") return;
    clearTimeout(c.expireTimer); stopRing();
    c.state = "accepting";
    return runCalleeFlow(c);
  }

  async function runCalleeFlow(call) {
    try {
      screen("incoming-connecting", { status: "Allow microphone" + (call.type === "video" ? " and camera" : "") + " access…", spinner: true });
      if (!call.stream) { try { call.stream = await getMedia(call.type, call.facing); } catch (e) { await api("/status", { body: { call_id: call.callId, status: "failed" } }); return endUi(call, "failed", mediaError(e)); } }
      screen("wake", { status: "Connecting to calling server…" });
      try {
        await ensureServerReady((p) => status(p.state === "ready" ? "Server ready" : "Waking up the calling server… " + Math.round(p.elapsed / 1000) + "s"), call.abort.signal);
      } catch (e) { if (e.code === "cancelled") return; return failFlow(call, "The calling server did not respond in time.", true); }
      if (call.ended) return;
      const r = await api("/accept", { body: { call_id: call.callId } });
      if (!r.ok) {
        if (r.status === 409 || r.status === 404) return endUi(call, "missed", "The caller already hung up.");
        return failFlow(call, "Could not accept the call.", true);
      }
      call.token = r.data.token; call.tokenAt = Date.now(); call.serverUrl = r.data.calling_server_url;
      screen("calling", { status: "Connecting…" });
      await connectSignaling(call);
    } catch (e) { if (!call.ended) failFlow(call, "Could not connect the call.", true); }
  }

  async function pollIncoming() {
    if (S.call || !S.cfg || !S.cfg.enabled) return;
    const r = await api("/incoming");
    if (r.ok && r.data.call) showIncoming(r.data.call);
  }

  function schedulePolling() {
    clearTimeout(S.pollTimer);
    const next = () => { S.pollTimer = setTimeout(async () => { try { await pollIncoming(); } finally { next(); } }, document.hidden ? 10000 : 3000); };
    next();
  }

  // ------------------------------------------------------------------ chat header buttons
  function injectCallButtons() {
    const chat = $("chat"); if (!chat) return;
    const header = chat.querySelector(".topbar"); if (!header || header.querySelector(".goppo-call-btns")) return;
    const peerId = parseInt(chat.getAttribute("data-peer"), 10); if (!peerId) return;
    const wrap = document.createElement("div"); wrap.className = "goppo-call-btns";
    wrap.innerHTML = `
      <button type="button" class="icon-btn" id="goppo-voice-btn" title="Voice call" aria-label="Voice call">
        <svg class="icon" viewBox="0 0 24 24" width="22" height="22"><path fill="currentColor" d="M6.6 10.8c1.4 2.8 3.8 5.1 6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.4 2.3.6 3.6.6.6 0 1 .4 1 1V20c0 .6-.4 1-1 1C10.6 21 3 13.4 3 4c0-.6.4-1 1-1h3.5c.6 0 1 .4 1 1 0 1.2.2 2.4.6 3.6.1.4 0 .7-.3 1l-2.2 2.2z"/></svg>
      </button>
      <button type="button" class="icon-btn" id="goppo-video-btn" title="Video call" aria-label="Video call">
        <svg class="icon" viewBox="0 0 24 24" width="22" height="22"><path fill="currentColor" d="M17 10.5V7c0-.6-.4-1-1-1H4c-.6 0-1 .4-1 1v10c0 .6.4 1 1 1h12c.6 0 1-.4 1-1v-3.5l4 4v-11l-4 4z"/></svg>
      </button>`;
    const head = header.querySelector(".chat-head");
    if (head && head.nextSibling) header.insertBefore(wrap, head.nextSibling); else header.appendChild(wrap);
    const info = { id: peerId, full_name: (header.querySelector(".chat-head strong") || {}).textContent || "", avatar_url: ((header.querySelector(".avatar img") || {}).src) || "" };
    $("goppo-voice-btn").addEventListener("click", () => start(peerId, "voice", info));
    $("goppo-video-btn").addEventListener("click", () => start(peerId, "video", info));
  }

  // ------------------------------------------------------------------ wiring
  window.addEventListener("goppo:incoming-call", (ev) => { if (ev.detail) showIncoming(ev.detail); });

  if ("serviceWorker" in navigator && navigator.serviceWorker.addEventListener) {
    navigator.serviceWorker.addEventListener("message", (ev) => {
      const d = ev.data || {};
      if (d.type === "incoming-call") { pollIncoming(); window.dispatchEvent(new CustomEvent("goppo:incoming-call-hint", { detail: d })); }
    });
  }

  async function init() {
    if (!document.querySelector('meta[name="csrf-token"]')) return;     // not a logged-in page
    const cfg = await loadConfig();
    if (!cfg.enabled) return;
    injectCallButtons();
    const incoming = new URLSearchParams(location.search).get("incoming");   // opened from a push notification
    pollIncoming();
    if (incoming) history.replaceState(null, "", location.pathname);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) pollIncoming(); });
    schedulePolling();
  }

  window.GoppoCalling = {
    startVoice: (id, info) => start(id, "voice", info),
    startVideo: (id, info) => start(id, "video", info),
    showIncoming, init, ensureServerReady,
    _state: S,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
