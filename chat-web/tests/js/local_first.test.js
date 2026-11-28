// Task 2: js/local-first.js (the layer that makes the website's own scripts use the phone's local database inside the APK),
// run for real in jsdom against a fake GoppoLocal plugin. This proves the page-side logic only; the Kotlin side (SQLite, WorkManager,
// downloads) is covered by its own tests, and nothing here replaces testing on a phone.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const SRC = fs.readFileSync(path.join(__dirname, "../../app/static/js/local-first.js"), "utf8");
const tick = (ms = 5) => new Promise((r) => setTimeout(r, ms));

async function env({ url = "https://localhost/chat.html?c=5", html = "<body><div class='app'><div id='chat' data-conversation='0'><div class='chat-head'><strong>x</strong><span class='avatar'></span></div></div></div></body>", plugin = {}, noPlugin = false } = {}) {
  const dom = new JSDOM(html, { runScripts: "outside-only", url, pretendToBeVisual: true });
  const w = dom.window;
  if (w.document.readyState === "loading") await new Promise((r) => w.document.addEventListener("DOMContentLoaded", r));
  const log = { calls: [], toasts: [], original: [], events: [] };
  const base = {
    http: async (a) => ({ code: 200, body: "{}" }),
    chatList: async () => ({ chats: [{ id: 5, peer_id: 9, unread: 2, user: { name: "Bob", username: "bob", avatar: null } }, { id: 6, unread: 1, user: { name: "C" } }] }),
    messages: async () => ({ messages: [], more: false, rev: 7 }),
    changes: async () => ({ messages: [], rev: 7 }),
    sendText: async (a) => ({ message: { uuid: a.uuid, type: "text", body: a.body, mine: true, status: "waiting", id: null, at: "2026-11-28T00:00:00Z" } }),
    uploadChunk: async () => ({}), sendMedia: async (a) => ({ message: { uuid: a.uuid, status: "waiting", type: a.type } }),
    markSeen: async () => ({}), deleteEveryone: async () => ({ ok: true }), hideLocal: async () => ({}),
    sessionInfo: async () => ({ userId: 1, phase: "live" }), sessionBegin: async () => ({}), sessionEnd: async () => ({ ended: true, pending: 0 }),
    getMediaSettings: async () => ({ autoDownload: true, photos: true, videos: false, usageBytes: 3 * 1048576 }),
    setMediaSettings: async (a) => ({ autoDownload: a.autoDownload, photos: a.photos, videos: a.videos, usageBytes: 0 }),
    clearMedia: async () => ({ freedBytes: 5 }), syncNow: async () => ({}), downloadMedia: async () => ({ state: "done" }),
    addListener: () => {},
  };
  const p = {};
  for (const k of Object.keys(base)) p[k] = async (a) => { log.calls.push([k, a]); return (plugin[k] || base[k])(a); };
  p.addListener = (n, f) => { log.events.push(n); };
  w.GOPPO_BUNDLE = true; w.GOPPO_LANG = "en";
  if (!noPlugin) w.Capacitor = { Plugins: { GoppoLocal: p } };
  w.app = { api: async (...a) => { log.original.push(a); return { ok: true, status: 200, data: { original: true } }; }, t: (k) => k, toast: (m, k) => log.toasts.push([m, k]),
    confirmDialog: async () => env.confirm !== false };
  w.crypto = w.crypto || require("node:crypto").webcrypto;
  w.eval(SRC);
  return { w, log, close: () => w.close(), calls: (n) => log.calls.filter((c) => c[0] === n).map((c) => JSON.parse(JSON.stringify(c[1] === undefined ? null : c[1]))) };
}

test("sending text saves on the phone first: status 'waiting', no network call, never 'sent' before the server accepts", async (t) => {
  const e = await env(); t.after(e.close);
  const r = await e.w.app.api("/api/conversations/5/messages", { method: "POST", json: { uuid: "uuid-aaaa-0001", body: "hello" } });
  assert.equal(r.ok, true); assert.equal(r.data.message.status, "waiting");
  assert.deepEqual(e.calls("sendText"), [{ conv: 5, uuid: "uuid-aaaa-0001", body: "hello" }]);
  assert.equal(e.calls("http").length, 0); assert.equal(e.log.original.length, 0);
});

test("history comes from the local database; 'older' pages by the first server id; has_more is passed on", async (t) => {
  const e = await env({ plugin: { messages: async (a) => ({ messages: [{ uuid: "u1", id: 11, rev: 3, status: "sent" }], more: !a.before, rev: 9 }) } }); t.after(e.close);
  const first = await e.w.app.api("/api/conversations/5/messages");
  assert.equal(first.data.has_more, true); assert.equal(first.data.server_time, "9"); assert.equal(first.data.messages.length, 1);
  await e.w.app.api("/api/conversations/5/messages?before=11");
  assert.deepEqual(e.calls("messages").at(-1), { conv: 5, before: 11, limit: 30 });
  assert.equal(e.calls("http").length, 0);
});

test("poll hands chat.js only what changed (no flicker, no repeated scroll-to-bottom)", async (t) => {
  const row = { uuid: "u1", id: 11, rev: 3, status: "sent", deleted: false };
  let cur = row;
  const e = await env({ plugin: { changes: async () => ({ messages: [cur], rev: 3 }), http: async () => ({ code: 0, body: "" }) } }); t.after(e.close);
  const url = "/api/conversations/5/poll?after=11&since=3";
  assert.equal((await e.w.app.api(url)).data.messages.length, 1);
  assert.equal((await e.w.app.api(url)).data.messages.length, 0);               // unchanged: nothing
  cur = { ...row, rev: 4, status: "seen" };
  assert.equal((await e.w.app.api(url)).data.messages.length, 1);               // blue tick arrives
  cur = { ...row, rev: 5, deleted: true };
  const del = await e.w.app.api(url);
  assert.equal(del.data.messages[0].deleted, true);                              // deletion event arrives
});

test("delete for everyone is queued until the SERVER confirms: the page is told 'queued', not 'deleted'", async (t) => {
  const e = await env(); t.after(e.close);
  const r = await e.w.app.api("/api/messages/11/delete", { method: "POST", json: { scope: "everyone" } });
  assert.equal(r.ok, true); assert.equal(r.data.queued, true);
  assert.deepEqual(e.calls("deleteEveryone"), [{ serverId: 11 }]);
  assert.equal(e.calls("http").length, 0);
});

test("delete for me needs the server: offline it fails clearly and the local row is NOT hidden", async (t) => {
  const e = await env({ plugin: { http: async () => ({ code: 0, body: "" }) } }); t.after(e.close);
  const r = await e.w.app.api("/api/messages/11/delete", { method: "POST", json: { scope: "me" } });
  assert.equal(r.ok, false); assert.equal(r.status, 0); assert.equal(e.calls("hideLocal").length, 0);
  const ok = await env({ plugin: { http: async () => ({ code: 200, body: "{}" }) } }); t.after(ok.close);
  assert.equal((await ok.w.app.api("/api/messages/11/delete", { method: "POST", json: { scope: "me" } })).ok, true);
  assert.deepEqual(ok.calls("hideLocal"), [{ serverId: 11 }]);
});

test("other API calls go through the native HTTPS bridge; offline is reported honestly (never a fake success)", async (t) => {
  const e = await env({ plugin: { http: async (a) => (a.path === "/api/summary" ? { code: 0, body: "" } : { code: 200, body: '{"enabled":true}' }) } }); t.after(e.close);
  const bad = await e.w.app.api("/api/summary");
  assert.equal(bad.ok, false); assert.equal(bad.status, 0);
  const good = await e.w.app.api("/api/calling/config");
  assert.equal(good.ok, true); assert.equal(good.data.enabled, true);
  assert.deepEqual(e.calls("http").at(-1), { method: "GET", path: "/api/calling/config" });
});

test("a 401 from the server signs the page out (event), it is not silently ignored", async (t) => {
  const e = await env({ plugin: { http: async () => ({ code: 401, body: "{}" }) } }); t.after(e.close);
  let fired = false; e.w.addEventListener("goppo:signed-out", () => { fired = true; });
  await e.w.app.api("/api/summary");
  assert.equal(fired, true);
});

test("chat list is answered from the phone and works offline", async (t) => {
  const e = await env(); t.after(e.close);
  const r = await e.w.app.api("/api/chats");
  assert.equal(r.data.chats.length, 2); assert.equal(r.data.total_unread, 3); assert.equal(e.calls("http").length, 0);
});

test("upload: the file is copied to the phone in clean base64 chunks (multiple of 3 bytes), then queued; progress reaches 100", async (t) => {
  const e = await env(); t.after(e.close);
  const size = 3 * 128 * 1024 * 2 + 1000;                                          // 2 full chunks + a short one
  const bytes = new Uint8Array(size).map((_, i) => i % 251);
  const file = new e.w.File([bytes], "pic.jpg", { type: "image/jpeg" });
  const form = new e.w.FormData(); form.append("file", file); form.append("uuid", "uuid-bbbb-0002");
  const prog = [];
  const res = await e.w.GoppoLocal.upload("/api/conversations/5/messages", form, (p) => prog.push(p));
  assert.equal(res.ok, true); assert.equal(res.data.message.status, "waiting");
  const chunks = e.calls("uploadChunk");
  assert.equal(chunks.length, 3); assert.equal(chunks[0].first, true); assert.equal(chunks[1].first, false);
  const decoded = chunks.map((c) => Buffer.from(c.data, "base64"));
  assert.deepEqual(decoded.slice(0, 2).map((d) => d.length), [393216, 393216]);
  assert.equal(Buffer.concat(decoded).length, size);
  assert.deepEqual([...Buffer.concat(decoded).subarray(0, 8)], [...bytes.subarray(0, 8)]);
  assert.equal(new Set(chunks.map((c) => c.handle)).size, 1); assert.match(chunks[0].handle, /^[a-f0-9]{32}$/);
  assert.equal(prog.at(-1), 100);
  const sent = e.calls("sendMedia")[0];
  assert.equal(sent.type, "image"); assert.equal(sent.conv, 5); assert.equal(sent.uuid, "uuid-bbbb-0002"); assert.equal(sent.mime, "image/jpeg");
});

test("upload: voice flag and documents are typed correctly; a too-large file is reported, not queued", async (t) => {
  const e = await env({ plugin: { uploadChunk: async () => { throw new Error("too_large"); } } }); t.after(e.close);
  const form = new e.w.FormData(); form.append("file", new e.w.File([new Uint8Array(10)], "a.webm", { type: "audio/webm" })); form.append("uuid", "uuid-cccc-0003");
  const res = await e.w.GoppoLocal.upload("/api/conversations/5/messages", form, () => {});
  assert.equal(res.ok, false); assert.equal(res.status, 413); assert.equal(e.calls("sendMedia").length, 0);
  const ok = await env(); t.after(ok.close);
  const f2 = new ok.w.FormData(); f2.append("file", new ok.w.File([new Uint8Array(10)], "a.webm", { type: "audio/webm" })); f2.append("uuid", "uuid-cccc-0004"); f2.append("voice", "1"); f2.append("duration", "4.5");
  await ok.w.GoppoLocal.upload("/api/conversations/5/messages", f2, () => {});
  assert.deepEqual([ok.calls("sendMedia")[0].type, ok.calls("sendMedia")[0].duration], ["voice", 4.5]);
  const f3 = new ok.w.FormData(); f3.append("file", new ok.w.File([new Uint8Array(10)], "a.pdf", { type: "application/pdf" })); f3.append("uuid", "uuid-cccc-0005");
  await ok.w.GoppoLocal.upload("/api/conversations/5/messages", f3, () => {});
  assert.equal(ok.calls("sendMedia")[1].type, "document");
});

test("chat page: conversation id comes from the URL; header is filled from the local copy; a bad id goes back to the list", async (t) => {
  const e = await env(); t.after(e.close);
  assert.equal(e.w.document.getElementById("chat").dataset.conversation, "5");
  await tick(20);
  assert.equal(e.w.document.querySelector(".chat-head strong").textContent, "Bob");
  const bad = await env({ url: "https://localhost/chat.html?c=abc" }); t.after(bad.close);
  assert.equal(bad.w.document.getElementById("chat").dataset.conversation, "0");
});

test("first run: while history is being restored the chat says so", async (t) => {
  const e = await env({ html: "<body><div class='app'><div id='chat'><div id='chat-offline' class='hidden'></div></div></div></body>", plugin: { sessionInfo: async () => ({ userId: 1, phase: "restoring" }) } }); t.after(e.close);
  await tick(20);
  assert.equal(e.w.document.getElementById("chat-offline").classList.contains("hidden"), false);
});

const SETTINGS = `<body><div class="app"><section id="media-settings" hidden><input type="checkbox" id="media-auto"><input type="checkbox" id="media-photos"><input type="checkbox" id="media-videos">
  <span id="media-usage"></span><button id="media-clear">clear</button></section><button id="bundle-logout">out</button></div></body>`;

test("media settings: shown from the phone's values; master OFF disables photo/video switches; every change is saved natively", async (t) => {
  const e = await env({ url: "https://localhost/settings.html", html: SETTINGS }); t.after(e.close);
  await tick(20);
  const d = e.w.document;
  assert.equal(d.getElementById("media-settings").hidden, false);
  assert.deepEqual([d.getElementById("media-auto").checked, d.getElementById("media-photos").checked, d.getElementById("media-videos").checked], [true, true, false]);
  assert.equal(d.getElementById("media-usage").textContent, "3.0 MB");
  d.getElementById("media-auto").checked = false; d.getElementById("media-auto").dispatchEvent(new e.w.Event("change")); await tick(20);
  assert.deepEqual(e.calls("setMediaSettings").at(-1), { autoDownload: false, photos: true, videos: false });
  assert.equal(d.getElementById("media-photos").disabled, true); assert.equal(d.getElementById("media-videos").disabled, true);
});

test("clear downloaded media: asks first; only a confirmed click clears (local copies only)", async (t) => {
  const e = await env({ url: "https://localhost/settings.html", html: SETTINGS }); t.after(e.close);
  await tick(20);
  e.w.app.confirmDialog = async () => false;
  e.w.document.getElementById("media-clear").click(); await tick(20);
  assert.equal(e.calls("clearMedia").length, 0);
  e.w.app.confirmDialog = async () => true;
  e.w.document.getElementById("media-clear").click(); await tick(30);
  assert.equal(e.calls("clearMedia").length, 1);
});

test("logout: unsent messages need an explicit OK; the server session is ended; nothing is deleted on the server", async (t) => {
  let n = 0;
  const e = await env({ url: "https://localhost/settings.html", html: SETTINGS, plugin: { sessionEnd: async (a) => (a.force ? { ended: true, pending: 2 } : { ended: false, pending: 2 }) } }); t.after(e.close);
  await tick(20);
  e.w.app.confirmDialog = async () => false;
  e.w.document.getElementById("bundle-logout").click(); await tick(30);
  assert.deepEqual(e.calls("sessionEnd"), [{ force: false }]);
  assert.equal(e.calls("http").filter((h) => h.path === "/logout").length, 0);
  e.w.app.confirmDialog = async () => true;
  e.w.document.getElementById("bundle-logout").click(); await tick(40);
  assert.deepEqual(e.calls("sessionEnd").at(-1), { force: true });
  assert.equal(e.calls("http").filter((h) => h.path === "/logout").length, 1);
});

const LOGIN = `<body><div class="app"><main><form class="stack"><input name="identifier" value="nina"><input name="password" value="pw"><button type="submit">go</button></form></main></div></body>`;

test("login page: posts JSON to /api/auth/login, starts the local session on success, shows the server's message on failure", async (t) => {
  const bad = await env({ url: "https://localhost/login.html", html: LOGIN, plugin: { http: async () => ({ code: 401, body: '{"ok":false,"error":{"message":"Wrong password"}}' }) } }); t.after(bad.close);
  bad.w.document.querySelector("form").dispatchEvent(new bad.w.Event("submit", { cancelable: true })); await tick(30);
  assert.deepEqual(bad.calls("http")[0], { method: "POST", path: "/api/auth/login", json: { identifier: "nina", password: "pw" } });
  assert.equal(bad.calls("sessionBegin").length, 0);
  assert.match(bad.w.document.querySelector(".banner--error").textContent, /Wrong password/);
  const good = await env({ url: "https://localhost/login.html", html: LOGIN, plugin: { http: async () => ({ code: 200, body: '{"ok":true,"user":{"id":42}}' }) } }); t.after(good.close);
  good.w.document.querySelector("form").dispatchEvent(new good.w.Event("submit", { cancelable: true })); await tick(30);
  assert.deepEqual(good.calls("sessionBegin"), [{ userId: 42 }]);
});

test("offline login attempt: honest network error, no session started", async (t) => {
  const e = await env({ url: "https://localhost/login.html", html: LOGIN, plugin: { http: async () => ({ code: 0, body: "" }) } }); t.after(e.close);
  e.w.document.querySelector("form").dispatchEvent(new e.w.Event("submit", { cancelable: true })); await tick(30);
  assert.equal(e.calls("sessionBegin").length, 0);
  assert.match(e.w.document.querySelector(".banner--error").textContent, /toast.network_error|Network/);
});

test("offline banner follows the real connection state, and 'online' triggers a sync", async (t) => {
  const e = await env(); t.after(e.close);
  Object.defineProperty(e.w.navigator, "onLine", { value: false, configurable: true });
  e.w.dispatchEvent(new e.w.Event("offline"));
  assert.equal(e.w.document.getElementById("goppo-net").classList.contains("hidden"), false);
  Object.defineProperty(e.w.navigator, "onLine", { value: true, configurable: true });
  e.w.dispatchEvent(new e.w.Event("online")); await tick(10);
  assert.equal(e.w.document.getElementById("goppo-net").classList.contains("hidden"), true);
  assert.equal(e.calls("syncNow").length >= 1, true);
});

test("outside the app (no native bridge) the website's own api() is used untouched", async (t) => {
  const e = await env({ noPlugin: true }); t.after(e.close);
  const r = await e.w.app.api("/api/chats");
  assert.equal(r.data.original, true); assert.equal(e.log.original.length, 1);
});

test("the website itself is not affected: without GOPPO_BUNDLE the script does nothing", async (t) => {
  const dom = new JSDOM("<body></body>", { runScripts: "outside-only", url: "https://example.org/messages" });
  dom.window.app = { api: () => 1 };
  dom.window.eval(SRC);
  assert.equal(dom.window.GoppoLocal, undefined); assert.equal(dom.window.app.api(), 1);
});
