/* The chat screen: show messages, send text / files / voice, poll for news, delete. */
(function () {
  "use strict";
  const root = document.getElementById("chat");
  if (!root) return;

  const $ = (id) => document.getElementById(id);
  const convId = root.dataset.conversation;
  const API = "/api/conversations/" + convId;
  const POLL_ACTIVE = Number(document.body.dataset.pollActive) || 2500;
  const POLL_IDLE = Number(document.body.dataset.pollIdle) || 12000;
  const LIMITS = {
    image: +root.dataset.maxImage, video: +root.dataset.maxVideo,
    document: Math.max(+root.dataset.maxDocument, +root.dataset.maxFile), voice: +root.dataset.maxVoiceBytes,
  };
  const MAX_VOICE = +root.dataset.maxVoice || 300;
  const t = (...a) => window.app.t(...a);
  const api = (...a) => window.app.api(...a);

  const scroller = $("chat-scroll"), list = $("chat-messages"), input = $("msg-input");
  const state = { msgs: new Map(), els: new Map(), lastId: 0, firstId: 0, serverTime: "", hasMore: false,
                  peer: null, stopped: false, typingAt: 0, typingTimer: null };

  /* ---------------------------------------------------------------- tiny DOM helpers */
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;     // textContent only: no HTML from users
    return n;
  };
  const icon = (name, cls) => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon icon--sm " + (cls || ""));
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#" + name);
    svg.appendChild(use);
    return svg;
  };
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : "u" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10));
  const mmss = (s) => { s = Math.max(0, Math.floor(s || 0)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };
  const sizeText = (b) => (b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB");
  const nearBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 140;
  const toBottom = () => { scroller.scrollTop = scroller.scrollHeight; };
  const visibleNow = () => !document.hidden && document.hasFocus();

  let emojiOnly = () => false;
  try {
    const re = /^(?:\p{Extended_Pictographic}|\p{Emoji_Component}|\u200d|\ufe0f|\s){1,12}$/u;
    emojiOnly = (s) => re.test(s) && /\p{Extended_Pictographic}/u.test(s);
  } catch (e) { /* very old browser: emoji just stay normal size */ }

  /* ---------------------------------------------------------------- one message bubble */
  let playing = null;                                  // only one voice message plays at a time
  function voicePlayer(att) {
    const wrap = el("div", "voice");
    const audio = el("audio"); audio.src = att.url; audio.preload = "metadata";
    const btn = el("button", "icon-btn icon-btn--primary"); btn.type = "button";
    btn.setAttribute("aria-label", t("chat.voice_play"));
    const setIcon = (name) => btn.replaceChildren(icon(name));
    setIcon("i-play");
    const seek = el("input", "vr-seek"); seek.type = "range"; seek.min = 0; seek.max = 100; seek.value = 0;
    seek.setAttribute("aria-label", t("chat.voice_seek"));
    const time = el("span", "vr-time", mmss(att.dur));
    const total = () => (isFinite(audio.duration) && audio.duration > 0 ? audio.duration : att.dur || 0);
    btn.addEventListener("click", () => {
      if (audio.paused) {
        if (playing && playing !== audio) playing.pause();
        playing = audio; audio.play().catch(() => window.app.toast(t("toast.something_wrong"), "error"));
      } else audio.pause();
    });
    audio.addEventListener("play", () => setIcon("i-pause"));
    audio.addEventListener("pause", () => setIcon("i-play"));
    audio.addEventListener("ended", () => { seek.value = 0; time.textContent = mmss(total()); });
    audio.addEventListener("timeupdate", () => {
      if (total()) seek.value = (audio.currentTime / total()) * 100;
      time.textContent = mmss(audio.paused && audio.currentTime === 0 ? total() : audio.currentTime);
    });
    seek.addEventListener("input", () => { if (total()) audio.currentTime = (seek.value / 100) * total(); });
    wrap.append(btn, seek, time, audio);
    return wrap;
  }

  function content(m) {
    const box = el("div", "bubble__content");
    if (m.deleted) { box.appendChild(el("p", "bubble__text bubble--deleted", t("chat.deleted"))); return box; }
    const a = m.att;
    if (m.type === "image" && a) {
      const open = el("button", "media"); open.type = "button";
      const img = el("img"); img.src = a.url; img.alt = a.name || ""; img.loading = "lazy";
      open.appendChild(img);
      open.addEventListener("click", () => lightbox(a.url));
      box.appendChild(open);
    } else if (m.type === "video" && a) {
      const v = el("video", "media"); v.src = a.url; v.controls = true; v.preload = "metadata"; v.playsInline = true;
      box.appendChild(v);
    } else if (m.type === "voice" && a) {
      box.appendChild(voicePlayer(a));
    } else if ((m.type === "document" || m.type === "file") && a) {
      const f = el("a", "filecard"); f.href = a.url; f.target = "_blank"; f.rel = "noopener noreferrer"; f.download = a.name || "";
      const info = el("span", "filecard__info");
      info.append(el("strong", "", a.name || t("chat.file")), el("span", "muted", a.size ? sizeText(a.size) : t("chat.download")));
      f.append(icon("i-file"), info, icon("i-download"));
      box.appendChild(f);
    }
    if (m.body) box.appendChild(el("p", "bubble__text" + (m.type === "text" && emojiOnly(m.body) ? " bubble__text--emoji" : ""), m.body));
    return box;
  }

  function tick(m) {
    if (m.status === "sending") return icon("i-clock");
    if (m.status === "failed") return icon("i-alert", "tick--failed");
    if (m.status === "sent") return icon("i-check");
    return icon("i-check2", m.status === "seen" ? "tick--seen" : "");
  }

  function build(m) {
    const row = el("div", "msg " + (m.mine ? "msg--out" : "msg--in"));
    row.dataset.uuid = m.uuid;
    const bubble = el("div", "bubble" + (m.mine ? " bubble--out" : ""));
    row.appendChild(bubble);
    fill(row, m);
    return row;
  }

  /* (Re)draw the inside of a bubble. Voice/video/images are only rebuilt when content changes. */
  function fill(row, m) {
    row.dataset.id = m.id || "";
    row.dataset.day = new Date(m.at).toDateString();
    const bubble = row.firstChild;
    const sig = [m.deleted, m.type, m.body, m.att && m.att.url, m.progress !== undefined].join("|");
    if (row._sig !== sig) {
      row._sig = sig;
      bubble.replaceChildren(content(m));
      if (m.progress !== undefined) {
        const p = el("progress", "upload"); p.max = 100; p.value = m.progress; bubble.appendChild(p);
      }
      const meta = el("div", "bubble__meta"); meta.append(el("time", "", window.app.fmtTime(m.at)));
      row._meta = meta;
      bubble.appendChild(meta);
      if (m.id && !m.pending && !m.deleted) {
        const more = el("button", "bubble__more"); more.type = "button";
        more.setAttribute("aria-label", t("chat.options")); more.appendChild(icon("i-more"));
        more.addEventListener("click", () => options(row.dataset.uuid));
        bubble.appendChild(more);
      }
    }
    const p = bubble.querySelector("progress");
    if (p && m.progress !== undefined) p.value = m.progress;
    bubble.classList.toggle("bubble--failed", m.status === "failed");
    if (row._meta) {
      row._meta.querySelectorAll("svg").forEach((s) => s.remove());
      if (m.mine && !m.deleted) row._meta.appendChild(tick(m));
    }
    row.onclick = m.status === "failed" ? () => retry(m.uuid) : null;
  }

  /* ---------------------------------------------------------------- the message list */
  function separators() {
    list.querySelectorAll(".day-sep").forEach((n) => n.remove());
    let prev = "";
    [...list.querySelectorAll(".msg")].forEach((row) => {
      if (row.dataset.day !== prev) {
        prev = row.dataset.day;
        const iso = state.msgs.get(row.dataset.uuid).at;
        list.insertBefore(el("div", "day-sep", window.app.fmtDay(iso)), row);
      }
    });
    $("chat-empty").classList.toggle("hidden", state.msgs.size > 0);
  }

  function put(m, { prepend = false } = {}) {
    const old = state.msgs.get(m.uuid);
    const merged = { ...old, ...m, pending: false, progress: undefined };
    state.msgs.set(m.uuid, merged);
    let row = state.els.get(m.uuid);
    if (row) { fill(row, merged); }
    else {
      row = build(merged);
      state.els.set(m.uuid, row);
      if (prepend) list.insertBefore(row, list.querySelector(".msg"));
      else list.appendChild(row);
    }
    if (m.id) {
      state.lastId = Math.max(state.lastId, m.id);
      state.firstId = state.firstId ? Math.min(state.firstId, m.id) : m.id;
    }
    return row;
  }

  function byId(id) { return [...state.msgs.values()].find((m) => m.id === id); }

  function applyUpdates(updates) {
    updates.forEach((u) => {
      const m = byId(u.id);
      if (!m) return;
      if (u.deleted) m.deleted = true; else if (m.mine && u.status) m.status = u.status;
      fill(state.els.get(m.uuid), m);
    });
  }

  /* ---------------------------------------------------------------- header status */
  function showPeer(peer) {
    state.peer = peer;
    const box = $("peer-status");
    box.textContent = peer.typing ? t("status.typing") : peer.online ? t("status.online") : window.app.lastSeen(peer.last_seen) || "\u00a0";
    box.classList.toggle("peer--online", !!(peer.online || peer.typing));
  }

  /* ---------------------------------------------------------------- delivered / seen */
  async function acknowledge(incoming) {
    const unsent = incoming.filter((m) => !m.mine && !m.deleted && m.status === "sent").map((m) => m.id);
    if (unsent.length) await api(API + "/delivered", { method: "POST", json: { ids: unsent } });
    markSeen();
  }
  let seenBusy = false;
  async function markSeen() {
    if (!visibleNow() || seenBusy) return;
    const waiting = [...state.msgs.values()].filter((m) => !m.mine && !m.deleted && m.status !== "seen" && m.id);
    if (!waiting.length) return;
    seenBusy = true;
    const upto = Math.max(...waiting.map((m) => m.id));
    const res = await api(API + "/seen", { method: "POST", json: { upto } });
    seenBusy = false;
    if (res.ok) waiting.forEach((m) => { m.status = "seen"; });
  }

  /* ---------------------------------------------------------------- loading & polling */
  async function loadFirst() {
    await window.app.ready;
    const res = await api(API + "/messages");
    if (!res.ok) { $("chat-offline").classList.remove("hidden"); return setTimeout(loadFirst, POLL_IDLE); }
    state.serverTime = res.data.server_time; state.hasMore = res.data.has_more;
    res.data.messages.forEach((m) => put(m));
    separators(); toBottom(); showPeer(res.data.peer);
    $("load-older").classList.toggle("hidden", !state.hasMore);
    acknowledge(res.data.messages);
    schedule();
  }

  async function loadOlder() {
    const before = scroller.scrollHeight;
    const res = await api(API + "/messages?before=" + state.firstId);
    if (!res.ok) return window.app.toast(res.error.message, "error");
    [...res.data.messages].reverse().forEach((m) => put(m, { prepend: true }));
    state.hasMore = res.data.has_more;
    $("load-older").classList.toggle("hidden", !state.hasMore);
    separators();
    scroller.scrollTop += scroller.scrollHeight - before;   // keep the view where it was
  }

  async function pollOnce() {
    const url = API + "/poll?after=" + state.lastId + "&since=" + encodeURIComponent(state.serverTime);
    const res = await api(url);
    if (res.status === 404 || res.status === 401) { state.stopped = true; return; }
    $("chat-offline").classList.toggle("hidden", res.ok);
    if (!res.ok) return;
    const stick = nearBottom();
    state.serverTime = res.data.server_time;
    res.data.messages.forEach((m) => put(m));
    applyUpdates(res.data.updates);
    separators(); showPeer(res.data.peer);
    if (res.data.messages.length) {
      if (stick) toBottom();
      acknowledge(res.data.messages);
    } else markSeen();
  }

  let timer = null;
  function schedule() {
    clearTimeout(timer);
    if (state.stopped) return;
    timer = setTimeout(async () => { await pollOnce(); schedule(); }, document.hidden ? POLL_IDLE : POLL_ACTIVE);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { pollOnce(); schedule(); } });
  window.addEventListener("focus", markSeen);

  /* ---------------------------------------------------------------- sending */
  function addPending(m) {
    const stick = true;
    state.msgs.set(m.uuid, m);
    const row = build(m);
    state.els.set(m.uuid, row);
    list.appendChild(row);
    separators();
    if (stick) toBottom();
  }
  function setLocal(uuid, patch) {
    const m = state.msgs.get(uuid);
    Object.assign(m, patch);
    fill(state.els.get(uuid), m);
  }
  function dropLocal(uuid) {
    state.els.get(uuid)?.remove(); state.els.delete(uuid); state.msgs.delete(uuid); separators();
  }
  function finish(uuid, res, restoreText) {
    if (res.ok) { put(res.data.message); separators(); return; }
    if (res.status === 0 || res.status >= 500) { setLocal(uuid, { status: "failed" }); return; }   // can retry
    window.app.toast(res.error.message, "error");              // the server said no: retrying is pointless
    dropLocal(uuid);
    if (restoreText) { input.value = restoreText; sync(); }
  }

  const sending = new Map();     // uuid -> function that sends it again
  function sendText(text) {
    const m = { uuid: uuid(), type: "text", body: text, mine: true, status: "sending", pending: true, at: new Date().toISOString() };
    addPending(m);
    const go = async () => {
      setLocal(m.uuid, { status: "sending" });
      finish(m.uuid, await api(API + "/messages", { method: "POST", json: { uuid: m.uuid, body: text } }), text);
    };
    sending.set(m.uuid, go); go();
  }

  function xhr(url, form, onProgress) {
    return new Promise((resolve) => {
      const req = new XMLHttpRequest();
      req.open("POST", url);
      req.setRequestHeader("X-CSRFToken", document.querySelector('meta[name="csrf-token"]').content);
      req.setRequestHeader("Accept", "application/json");
      req.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
      req.onload = () => {
        let data = {};
        try { data = JSON.parse(req.responseText); } catch (e) { /* not json */ }
        resolve(req.status < 400 ? { ok: true, status: req.status, data } : { ok: false, status: req.status, error: data.error || { message: t("toast.something_wrong") } });
      };
      req.onerror = () => resolve({ ok: false, status: 0, error: { message: t("toast.network_error") } });
      req.send(form);
    });
  }

  function guessType(file, voice) {
    if (voice) return "voice";
    if (file.type.startsWith("image/")) return "image";
    if (file.type.startsWith("video/")) return "video";
    return "document";
  }

  function sendFile(file, { voice = false, duration = null, name = null } = {}) {
    const kind = guessType(file, voice);
    if (file.size > LIMITS[kind]) {
      window.app.toast(t("chat.too_large", { size: Math.max(1, Math.floor(LIMITS[kind] / 1048576)) }), "error");
      return;
    }
    const m = { uuid: uuid(), type: kind, body: "", mine: true, status: "sending", pending: true, progress: 0,
                at: new Date().toISOString(), att: { url: kind === "document" ? "" : URL.createObjectURL(file), name: name || file.name, size: file.size, dur: duration } };
    addPending(m);
    const go = async () => {
      setLocal(m.uuid, { status: "sending", progress: 0 });
      const form = new FormData();
      form.append("file", file, name || file.name); form.append("uuid", m.uuid);
      if (voice) { form.append("voice", "1"); form.append("duration", String(duration || 0)); }
      const res = await xhr(API + "/messages", form, (p) => setLocal(m.uuid, { progress: p }));
      if (res.ok) { const row = state.els.get(m.uuid); put({ ...res.data.message }); row && separators(); }
      else finish(m.uuid, res);
    };
    sending.set(m.uuid, go); go();
  }
  function retry(u) { const go = sending.get(u); if (go) go(); }

  /* ---------------------------------------------------------------- composer */
  function sync() {
    const has = input.value.trim().length > 0;
    $("btn-send").classList.toggle("hidden", !has);
    $("btn-mic").classList.toggle("hidden", has);
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 130) + "px";
  }
  function typing(on) {
    api(API + "/typing", { method: "POST", json: { typing: on } });
  }
  input.addEventListener("input", () => {
    sync();
    const now = Date.now();
    if (input.value.trim() && now - state.typingAt > 3000) { state.typingAt = now; typing(true); }
    clearTimeout(state.typingTimer);
    state.typingTimer = setTimeout(() => { state.typingAt = 0; typing(false); }, 4000);
  });
  /* Computer: Enter sends, Shift+Enter makes a new line. Phone/tablet (touch): Enter stays a new line,
     the send button is used. Ctrl/Cmd+Enter always sends. */
  const isTouch = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing || e.keyCode === 229) return;
    if (e.ctrlKey || e.metaKey || (!e.shiftKey && !isTouch)) { e.preventDefault(); $("composer").requestSubmit(); }
  });

  /* Keep the whole chat (and the typing box) inside the visible screen when the keyboard opens. */
  const vv = window.visualViewport;
  function fitScreen() {
    if (!vv) return;
    root.style.setProperty("--vvh", Math.round(vv.height) + "px");
    window.scrollTo(0, 0);
    scroller.scrollTop = scroller.scrollHeight;
  }
  if (vv) { vv.addEventListener("resize", fitScreen); vv.addEventListener("scroll", () => window.scrollTo(0, 0)); }
  $("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = ""; sync();
    clearTimeout(state.typingTimer); state.typingAt = 0; typing(false);
    sendText(text);
    input.focus();
  });

  /* emoji */
  const EMOJI = "😀 😁 😂 🤣 😊 😍 😘 😎 🤗 🤔 😅 😉 🙂 😇 🥰 😋 😜 🤩 😢 😭 😡 😱 😴 🤝 👍 👎 👏 🙏 💪 👋 🙌 ✌️ ❤️ 💔 🔥 ✨ 🎉 🎁 🌹 ☕ 🍕 🎂 ⚽ 🚀 🌙 ☀️ ⭐ 💯 ✅ ❌ 😐 🥳 😬 🤭 🙄 😏 🥺 😤 🤒 🤯 😌".split(" ");
  const panel = $("emoji-panel");
  EMOJI.forEach((e) => {
    const b = el("button", "emoji", e); b.type = "button";
    b.addEventListener("click", () => {
      const s = input.selectionStart ?? input.value.length, f = input.selectionEnd ?? s;
      input.value = input.value.slice(0, s) + e + input.value.slice(f);
      input.focus(); input.setSelectionRange(s + e.length, s + e.length); sync();
    });
    panel.appendChild(b);
  });
  $("btn-emoji").addEventListener("click", () => { $("attach-menu").classList.add("hidden"); panel.classList.toggle("hidden"); });

  /* attach */
  $("btn-attach").addEventListener("click", () => { panel.classList.add("hidden"); $("attach-menu").classList.toggle("hidden"); });
  root.querySelectorAll("[data-attach]").forEach((b) => b.addEventListener("click", () => {
    $("attach-menu").classList.add("hidden");
    (b.dataset.attach === "media" ? $("file-media") : $("file-doc")).click();
  }));
  [$("file-media"), $("file-doc")].forEach((inp) => inp.addEventListener("change", () => {
    [...inp.files].forEach((f) => sendFile(f));
    inp.value = "";
  }));
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#attach-menu, #btn-attach")) $("attach-menu").classList.add("hidden");
    if (!e.target.closest("#emoji-panel, #btn-emoji")) panel.classList.add("hidden");
  });

  /* ---------------------------------------------------------------- voice messages */
  const vr = { rec: null, blob: null, audio: null, seconds: 0, ext: "webm", url: "" };
  const show = (id, on) => $(id).classList.toggle("hidden", !on);
  function voiceUI(mode) {          // mode: off | recording | preview
    show("composer-default", mode === "off"); show("composer-voice", mode !== "off");
    show("vr-recording", mode === "recording"); show("vr-preview", mode === "preview");
    show("vr-pause", mode === "recording"); show("vr-stop", mode === "recording"); show("vr-send", mode === "preview");
    $("vr-dot").classList.toggle("vr-dot--live", mode === "recording");
  }
  function voiceReset() {
    if (vr.rec) { vr.rec.cancel(); vr.rec = null; }
    if (vr.audio) { vr.audio.pause(); vr.audio = null; }
    if (vr.url) URL.revokeObjectURL(vr.url);
    vr.blob = null; vr.url = ""; $("vr-time").textContent = "0:00";
    voiceUI("off");
  }
  const setIcon = (btn, name) => { btn.replaceChildren(icon(name)); };

  $("btn-mic").addEventListener("click", async () => {
    await window.app.ready;
    vr.rec = new VoiceRecorder({
      maxSeconds: MAX_VOICE,
      onTick: (s) => { $("vr-time").textContent = mmss(s); },
      onLimit: () => { window.app.toast(t("chat.voice_limit")); finishRecording(); },
    });
    try { await vr.rec.start(); }
    catch (e) {
      vr.rec = null;
      window.app.toast(t({ denied: "chat.mic_denied", missing: "chat.mic_missing", unsupported: "chat.mic_unsupported" }[e.code] || "chat.mic_failed"), "error");
      return;
    }
    setIcon($("vr-pause"), "i-pause"); $("vr-state").textContent = t("chat.recording"); voiceUI("recording");
  });
  $("vr-pause").addEventListener("click", () => {
    if (!vr.rec) return;
    if (vr.rec.state === "recording") { vr.rec.pause(); setIcon($("vr-pause"), "i-play"); $("vr-state").textContent = t("chat.paused"); $("vr-dot").classList.remove("vr-dot--live"); $("vr-pause").setAttribute("aria-label", t("chat.voice_resume")); }
    else { vr.rec.resume(); setIcon($("vr-pause"), "i-pause"); $("vr-state").textContent = t("chat.recording"); $("vr-dot").classList.add("vr-dot--live"); $("vr-pause").setAttribute("aria-label", t("chat.voice_pause")); }
  });
  async function finishRecording() {
    if (!vr.rec) return;
    try {
      const out = await vr.rec.stop();
      vr.rec = null;
      if (out.seconds < 1) { voiceReset(); return; }            // an accidental tap
      vr.blob = out.blob; vr.seconds = out.seconds; vr.ext = out.ext;
      vr.url = URL.createObjectURL(out.blob);
      vr.audio = new Audio(vr.url);
      const seek = $("vr-seek"), play = $("vr-play");
      seek.value = 0; $("vr-preview-time").textContent = mmss(vr.seconds); setIcon(play, "i-play");
      vr.audio.ontimeupdate = () => { seek.value = (vr.audio.currentTime / vr.seconds) * 100; $("vr-preview-time").textContent = mmss(vr.audio.currentTime); };
      vr.audio.onended = () => { setIcon(play, "i-play"); seek.value = 0; $("vr-preview-time").textContent = mmss(vr.seconds); };
      voiceUI("preview");
    } catch (e) { voiceReset(); window.app.toast(t("chat.mic_failed"), "error"); }
  }
  $("vr-stop").addEventListener("click", finishRecording);
  $("vr-play").addEventListener("click", () => {
    if (!vr.audio) return;
    if (vr.audio.paused) { vr.audio.play(); setIcon($("vr-play"), "i-pause"); } else { vr.audio.pause(); setIcon($("vr-play"), "i-play"); }
  });
  $("vr-seek").addEventListener("input", () => { if (vr.audio) vr.audio.currentTime = ($("vr-seek").value / 100) * vr.seconds; });
  $("vr-delete").addEventListener("click", voiceReset);
  $("vr-send").addEventListener("click", () => {
    if (!vr.blob) return;
    const file = new File([vr.blob], "voice." + vr.ext, { type: vr.blob.type });
    if (file.size > LIMITS.voice) {
      window.app.toast(t("chat.too_large", { size: Math.max(1, Math.floor(LIMITS.voice / 1048576)) }), "error");
      return;
    }
    const seconds = vr.seconds;
    voiceReset();
    sendFile(file, { voice: true, duration: seconds, name: file.name });
  });
  window.addEventListener("pagehide", () => vr.rec && vr.rec.cancel());   // never leave the mic on

  /* ---------------------------------------------------------------- delete & lightbox */
  function options(u) {
    const m = state.msgs.get(u);
    if (!m || !m.id) return;
    const dlg = el("dialog", "dialog");
    dlg.appendChild(el("h3", "", t("chat.options")));
    const actions = el("div", "dialog__actions");
    const choose = (scope, label) => {
      const b = el("button", "btn " + (scope === "everyone" ? "btn--danger" : "btn--secondary"), label); b.type = "button";
      b.addEventListener("click", async () => {
        dlg.close(); dlg.remove();
        const yes = await window.app.confirmDialog({ title: t("dialog.delete_message.title"), text: label, confirmLabel: t("common.delete"), danger: true });
        if (yes) removeMessage(m, scope);
      });
      actions.appendChild(b);
    };
    choose("me", t("chat.delete_for_me"));
    if (m.mine && !m.deleted) choose("everyone", t("chat.delete_for_everyone"));
    const cancel = el("button", "btn btn--ghost", t("common.cancel")); cancel.type = "button";
    cancel.addEventListener("click", () => { dlg.close(); dlg.remove(); });
    actions.appendChild(cancel);
    dlg.appendChild(actions);
    dlg.addEventListener("cancel", () => dlg.remove());
    document.body.appendChild(dlg); dlg.showModal();
  }
  async function removeMessage(m, scope) {
    const res = await api("/api/messages/" + m.id + "/delete", { method: "POST", json: { scope } });
    if (!res.ok) return window.app.toast(res.error.message, "error");
    if (scope === "me") dropLocal(m.uuid);
    else { m.deleted = true; fill(state.els.get(m.uuid), m); }
  }
  function lightbox(url) {
    const dlg = el("dialog", "lightbox");
    const img = el("img"); img.src = url; img.alt = "";
    dlg.appendChild(img);
    dlg.addEventListener("click", () => { dlg.close(); dlg.remove(); });
    document.body.appendChild(dlg); dlg.showModal();
  }

  $("load-older").addEventListener("click", loadOlder);
  sync();
  loadFirst();
})();
