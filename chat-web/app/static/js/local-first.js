/* Android app only (bundled UI). Makes the website's own scripts (chat.js, chatlist.js, device.js ...) work from the phone's
 * local database instead of the network:
 *   - window.app.api() keeps its signature; chat/message calls are answered by the GoppoLocal plugin (SQLite on the phone),
 *     everything else goes to the website through the plugin's HTTPS bridge (session cookie + CSRF handled natively);
 *   - sending saves locally first (status "waiting" until the SERVER accepts it); background sync does the rest;
 *   - uploads are copied to the phone's outbox, then sent; delete-for-everyone is queued until the server confirms.
 * Nothing here pretends a server operation succeeded: failures stay visible (offline banner, Waiting / Failed ticks).
 */
(function () {
  "use strict";
  if (!window.GOPPO_BUNDLE) return;

  const plugin = () => (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.GoppoLocal) || null;
  const T = (k, fb, v) => (window.app && window.app.t ? window.app.t(k, v) : fb) || fb;
  const toast = (k, fb, kind) => window.app && window.app.toast && window.app.toast(T(k, fb), kind);
  const NET_ERR = () => ({ message: T("toast.network_error", "Network error") });

  /* ------------------------------------------------------------------ the bridge */
  async function call(method, args) {
    const p = plugin();
    if (!p) throw new Error("no_bridge");
    return p[method](args || {});
  }

  /** The website's JSON API through the native HTTPS client. Same result shape as app.js api(). */
  async function http(method, path, json) {
    try {
      const r = await call("http", { method, path, json });
      let data = {};
      try { data = JSON.parse(r.body || "{}"); } catch (e) { /* not json */ }
      if (r.code >= 200 && r.code < 300) return { ok: true, status: r.code, data };
      if (r.code === 401) window.dispatchEvent(new Event("goppo:signed-out"));
      return { ok: false, status: r.code, error: data.error || { message: r.code === 0 ? NET_ERR().message : T("toast.something_wrong", "Something went wrong") } };
    } catch (e) {
      return { ok: false, status: 0, error: NET_ERR() };
    }
  }

  const ok = (data, status) => ({ ok: true, status: status || 200, data });
  const local = (e) => ({ ok: false, status: 500, error: { message: (e && e.message) || T("toast.something_wrong", "Something went wrong") } });

  /* ------------------------------------------------------------------ chat calls answered locally */
  const seenSig = new Map();            // conversation -> Map(uuid -> signature): only changed messages are handed to chat.js again
  const sigOf = (m) => [m.rev, m.status, m.deleted, m.delete_pending, m.id, m.att && m.att.state, m.att && m.att.downloaded].join("|");
  const idToUuid = new Map();
  const remember = (list) => list.forEach((m) => { if (m.id) idToUuid.set(m.id, m.uuid); });
  const PEER0 = { online: false, last_seen: null, typing: false };

  async function localApi(method, url, opts) {
    let m;
    if (method === "GET" && /^\/api\/chats(\?|$)/.test(url)) {
      const r = await call("chatList");
      return ok({ chats: r.chats, total_unread: r.chats.reduce((n, c) => n + (c.unread || 0), 0) });
    }
    if ((m = /^\/api\/conversations\/(\d+)\/messages(?:\?before=(\d+))?$/.exec(url))) {
      const conv = Number(m[1]);
      if (method === "GET") {
        const r = await call("messages", { conv, before: m[2] ? Number(m[2]) : undefined, limit: 30 });
        remember(r.messages);
        if (!m[2]) { const memo = new Map(); r.messages.forEach((x) => memo.set(x.uuid, sigOf(x))); seenSig.set(conv, memo); }
        return ok({ messages: r.messages, has_more: r.more, server_time: String(r.rev), peer: PEER0 });
      }
      if (method === "POST") {                                   // text: saved on the phone at once; the server accepts it later
        const body = opts.json || {};
        const r = await call("sendText", { conv, uuid: body.uuid, body: body.body });
        return ok({ ok: true, created: true, message: r.message }, 201);
      }
    }
    if ((m = /^\/api\/conversations\/(\d+)\/poll\?after=(\d+)&since=(\d*)/.exec(url))) {
      const conv = Number(m[1]);
      const r = await call("changes", { conv, rev: Number(m[3] || 0) });
      const memo = seenSig.get(conv) || new Map(); seenSig.set(conv, memo);
      const fresh = r.messages.filter((x) => { const s = sigOf(x); if (memo.get(x.uuid) === s) return false; memo.set(x.uuid, s); return true; });
      remember(fresh);
      presence(conv);                                            // best effort: online / last seen (needs internet)
      return ok({ messages: fresh, updates: [], more: false, server_time: String(r.rev), peer: lastPeer[conv] || PEER0 });
    }
    if ((m = /^\/api\/conversations\/(\d+)\/seen$/.exec(url)) && method === "POST") {
      await call("markSeen", { conv: Number(m[1]), upto: (opts.json || {}).upto || 0 });
      return ok({ ok: true });
    }
    if ((m = /^\/api\/messages\/(\d+)\/delete$/.exec(url)) && method === "POST") {
      const id = Number(m[1]), scope = (opts.json || {}).scope;
      if (scope === "everyone") { const r = await call("deleteEveryone", { serverId: id }); return r.ok ? ok({ ok: true, queued: true }) : { ok: false, status: 400, error: { message: T("toast.something_wrong", "Something went wrong") } }; }
      const res = await http("POST", url, opts.json);              // "delete for me": the server must know, so a second phone follows
      if (res.ok) await call("hideLocal", { serverId: id });
      else if (res.status === 0) return { ok: false, status: 0, error: { message: T("local.need_online", "This needs an internet connection.") } };
      return res;
    }
    return null;                                                  // not a local call
  }

  const lastPeer = {};
  const presenceBusy = {};
  async function presence(conv) {
    if (!navigator.onLine || presenceBusy[conv]) return;
    presenceBusy[conv] = true;
    try {
      const r = await http("GET", "/api/conversations/" + conv + "/poll?after=2147483647&since=");
      if (r.ok && r.data.peer) lastPeer[conv] = r.data.peer;
    } finally { presenceBusy[conv] = false; }
  }

  /* ------------------------------------------------------------------ window.app.api / upload / media */
  function installApi() {
    if (!window.app || window.app.__localFirst) return;
    const original = window.app.api;
    window.app.api = async function (url, options) {
      const opts = options || {};
      const method = (opts.method || "GET").toUpperCase();
      try {
        const r = await localApi(method, url, opts);
        if (r) return r;
      } catch (e) {
        if (!e || e.message !== "no_bridge") return local(e);
      }
      if (!plugin()) return original(url, options);
      return http(method, url, opts.json);
    };
    window.app.__localFirst = true;
  }

  const CHUNK = 3 * 128 * 1024;                                  // multiple of 3 bytes: every chunk is clean base64
  function b64(blob) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result).split(",")[1] || "");
      fr.onerror = () => reject(fr.error);
      fr.readAsDataURL(blob);
    });
  }
  function hex32() { const a = new Uint8Array(16); crypto.getRandomValues(a); return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join(""); }

  /** chat.js sendFile(): copy the file into the phone's outbox, then queue it. Returns the same shape as the website upload. */
  async function upload(url, form, onProgress) {
    const m = /^\/api\/conversations\/(\d+)\/messages$/.exec(url);
    const file = form.get("file");
    if (!m || !file) return { ok: false, status: 400, error: { message: T("toast.something_wrong", "Something went wrong") } };
    try {
      const handle = hex32();
      for (let off = 0; off < file.size; off += CHUNK) {
        await call("uploadChunk", { handle, data: await b64(file.slice(off, off + CHUNK)), first: off === 0 });
        onProgress(Math.min(99, Math.round(((off + CHUNK) / file.size) * 100)));
      }
      const voice = form.get("voice") === "1";
      const type = voice ? "voice" : file.type.startsWith("image/") ? "image" : file.type.startsWith("video/") ? "video" : "document";
      const r = await call("sendMedia", { conv: Number(m[1]), uuid: String(form.get("uuid")), handle, type, caption: String(form.get("caption") || ""),
        name: file.name || "file", mime: file.type || "application/octet-stream", duration: Number(form.get("duration") || 0) });
      onProgress(100);
      return { ok: true, status: 201, data: { ok: true, created: true, message: r.message } };
    } catch (e) {
      const code = e && e.message;
      return { ok: false, status: code === "too_large" ? 413 : 500, error: { message: T(code === "too_large" ? "chat.too_large" : "toast.something_wrong", "Could not save the file", { size: 25 }) } };
    }
  }

  window.GoppoLocal = {
    get active() { return !!plugin(); },
    call, http, upload,
    message: async (uuid) => { try { const r = await call("messages", { conv: 0, uuid }); return r.messages && r.messages[0]; } catch (e) { return null; } },   // plugin returns one message when uuid is given
    downloadMedia: (mediaId) => call("downloadMedia", { mediaId }),
  };

  /* ------------------------------------------------------------------ native events */
  function listen() {
    const p = plugin();
    if (!p || listen.done) return;
    listen.done = true;
    p.addListener("localChanged", (e) => window.dispatchEvent(new CustomEvent("goppo:local-changed", { detail: e })));
    p.addListener("mediaChanged", (e) => { window.dispatchEvent(new CustomEvent("goppo:media", { detail: e })); });
    p.addListener("signedOut", () => window.dispatchEvent(new Event("goppo:signed-out")));
  }

  window.addEventListener("goppo:signed-out", () => {
    if (location.pathname.indexOf("login") !== -1) return;
    toast("local.login_signed_out", "Your session has ended. Please log in again.", "error");
    setTimeout(() => location.replace("/login" + (window.GOPPO_LANG === "bn" ? ".bn" : "") + ".html"), 1200);
  }, { once: true });

  /* ------------------------------------------------------------------ offline banner (the real network state, not a guess) */
  function netBanner() {
    const app = document.querySelector(".app");
    if (!app) return;
    let b = document.getElementById("goppo-net");
    if (!b) { b = document.createElement("div"); b.id = "goppo-net"; b.className = "site-banner hidden"; b.setAttribute("role", "status"); app.prepend(b); }
    const upd = () => {
      b.textContent = T("local.offline", "You are offline. New messages will be sent automatically when you are back online.");
      b.classList.toggle("hidden", navigator.onLine);
      document.querySelectorAll("[data-needs-online]").forEach((x) => x.classList.toggle("is-offline", !navigator.onLine));
    };
    window.addEventListener("online", () => { upd(); call("syncNow").catch(() => {}); });
    window.addEventListener("offline", upd);
    upd();
  }

  /* ------------------------------------------------------------------ per-page setup */
  const page = (/^\/(login|messages|chat|settings)/.exec(location.pathname) || [])[1];

  function chatPage() {
    const root = document.getElementById("chat");
    const c = new URLSearchParams(location.search).get("c");
    if (!root || !/^\d{1,18}$/.test(c || "")) { location.replace("/messages" + (window.GOPPO_LANG === "bn" ? ".bn" : "") + ".html"); return; }
    root.dataset.conversation = c;                               // chat.js reads this before it starts
    call("chatList").then((r) => {                               // header from the local copy of the conversation
      const chat = (r.chats || []).find((x) => String(x.id) === c);
      if (!chat) return;
      root.dataset.peer = chat.peer_id;
      const head = root.querySelector(".chat-head strong"); if (head) head.textContent = chat.user.name;
      const a = root.querySelector(".chat-head"); if (a && chat.user.username) a.href = a.href.replace(/\/user(\/?|\?.*)?$/, "/" + encodeURIComponent(chat.user.username) + "$1");
      const av = root.querySelector(".chat-head .avatar");
      if (av) { av.textContent = (chat.user.name || "?").slice(0, 1).toUpperCase(); if (chat.user.avatar) { const img = document.createElement("img"); img.src = chat.user.avatar; img.alt = ""; av.textContent = ""; av.appendChild(img); } }
    }).catch(() => {});
    call("sessionInfo").then((i) => {                            // first run on this phone: say that history is being restored
      if (i && i.phase === "restoring") { const b = document.getElementById("chat-offline"); if (b) { b.textContent = T("local.restoring", "Restoring your message history..."); b.classList.remove("hidden"); } }
    }).catch(() => {});
  }

  function loginPage() {
    const form = document.querySelector("form.stack");
    if (!form) return;
    const banner = form.parentElement.querySelector(".banner--error") || (() => { const d = document.createElement("div"); d.className = "banner banner--error hidden"; d.setAttribute("role", "alert"); form.before(d); return d; })();
    form.addEventListener("submit", async (e) => {
      e.preventDefault(); e.stopImmediatePropagation();
      const btn = form.querySelector("[type=submit]"); btn.disabled = true;
      const res = await http("POST", "/api/auth/login", { identifier: form.elements.identifier.value.trim(), password: form.elements.password.value });
      if (res.ok) {
        try { await call("sessionBegin", { userId: res.data.user.id }); } catch (x) { /* shown below */ }
        return location.replace("/messages" + (window.GOPPO_LANG === "bn" ? ".bn" : "") + ".html");
      }
      btn.disabled = false;
      if (res.status === 403 && res.data === undefined && res.error && res.error.needs_verification) { /* handled below */ }
      banner.textContent = res.status === 0 ? T("toast.network_error", "Network error") : (res.error && res.error.message) || T("toast.something_wrong", "Something went wrong");
      banner.classList.remove("hidden");
    }, true);
  }

  async function fmtBytes(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(0, Math.round(n / 1024)) + " KB"; }

  function settingsPage() {
    const sec = document.getElementById("media-settings");
    if (sec && plugin()) {
      sec.hidden = false;
      const auto = document.getElementById("media-auto"), ph = document.getElementById("media-photos"), vi = document.getElementById("media-videos");
      const usage = document.getElementById("media-usage"), clear = document.getElementById("media-clear");
      const paint = async (s) => {
        auto.checked = !!s.autoDownload; ph.checked = !!s.photos; vi.checked = !!s.videos;
        ph.disabled = vi.disabled = !s.autoDownload;             // the master switch OFF means nothing is downloaded automatically
        usage.textContent = await fmtBytes(s.usageBytes || 0);
      };
      call("getMediaSettings").then(paint).catch(() => {});
      const save = () => call("setMediaSettings", { autoDownload: auto.checked, photos: ph.checked, videos: vi.checked }).then(paint);
      [auto, ph, vi].forEach((x) => x.addEventListener("change", save));
      clear.addEventListener("click", async () => {
        if (!(await window.app.confirmDialog({ title: T("media.clear", "Clear downloaded media"), text: T("media.clear_confirm", ""), confirmLabel: T("media.clear", "Clear"), danger: true }))) return;
        clear.disabled = true;
        try { await call("clearMedia"); toast("media.cleared", "Downloaded media cleared."); paint(await call("getMediaSettings")); } finally { clear.disabled = false; }
      });
    }
    const out = document.getElementById("bundle-logout");
    if (out) out.addEventListener("click", async () => {
      out.disabled = true;
      try {
        let r = await call("sessionEnd", { force: false });
        if (!r.ended) {                                          // unsent messages would be lost: the person must agree
          if (!(await window.app.confirmDialog({ title: T("nav.logout", "Log out"), text: T("local.logout_pending", "You have unsent messages. Log out anyway?"), confirmLabel: T("nav.logout", "Log out"), danger: true }))) return;
          r = await call("sessionEnd", { force: true });
        }
        await http("POST", "/logout");                           // ends the server session; server-side chat history is untouched
        location.replace("/login" + (window.GOPPO_LANG === "bn" ? ".bn" : "") + ".html");
      } finally { out.disabled = false; }
    });
    document.addEventListener("click", (e) => {
      const a = e.target.closest && e.target.closest("[data-needs-online]");
      if (a && !navigator.onLine) { e.preventDefault(); toast("local.need_online", "This needs an internet connection.", "error"); }
    }, true);
  }

  function start() {
    installApi(); listen(); netBanner();
    if (page === "chat") chatPage();
    if (page === "login") loginPage();
    if (page === "settings") settingsPage();
  }
  start();
  if (!plugin()) {                                               // the bridge can arrive a moment later: set up once it does
    let n = 0; const t = setInterval(() => { if (plugin() || ++n > 60) { clearInterval(t); if (plugin()) { listen(); } } }, 100);
  }
})();
