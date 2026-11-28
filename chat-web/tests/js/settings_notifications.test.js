// Task 2: the Android Settings buttons (Allow notification / Calling sound / Message sound). Real device.js against a fake platform
// layer in jsdom. Proves: the click handler works IMMEDIATELY (no wait for app:ready or the status call), the right channel is opened,
// permission states are real (granted / denied / permanently denied), no duplicate handlers, no false "success".
// It does NOT prove the Android system screens open - that needs a phone.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const SRC = fs.readFileSync(path.join(__dirname, "../../app/static/js/device.js"), "utf8");
const HTML = `<!doctype html><body data-auth="1"><section id="android-settings" hidden>
  <p data-android-status class="hidden"></p>
  <button data-android-open="allow">Allow</button><button data-android-open="calls">Calls</button>
  <button data-android-open="messages">Messages</button><button data-android-open="fullScreenIntent" class="hidden">FS</button></section></body>`;

async function env({ status = { permission: "granted", enabled: true, fullScreenIntent: "granted" }, request = null, openSettings = async () => true, readyLate = false, statusDelayMs = 0 } = {}) {
  const dom = new JSDOM(HTML, { runScripts: "outside-only", url: "https://localhost/settings.html" });
  const w = dom.window;
  if (w.document.readyState === "loading") await new Promise((r) => w.document.addEventListener("DOMContentLoaded", r));   // like a deferred script: runs after parsing
  const calls = { open: [], request: 0, status: 0, toasts: [] };
  const handlers = {};
  let st = { ...status };
  w.GoppoPlatform = {
    isNative: () => true,
    native: { available: () => true, info: () => ({ deviceId: "d1" }), call: async () => ({ handled: false }) },
    on: (n, f) => { (handlers[n] = handlers[n] || []).push(f); },
    has: () => false, handle: () => {},
    notifications: {
      available: () => true,
      status: async () => { calls.status++; if (statusDelayMs) await new Promise((r) => setTimeout(r, statusDelayMs)); return { ...st }; },
      request: async () => { calls.request++; if (request) st = { ...st, ...request }; return { ...st }; },
      openSettings: async (o) => { calls.open.push(o); return openSettings(o); },
    },
  };
  w.app = { toast: (m, kind) => calls.toasts.push([m, kind]), t: (k) => k, api: async () => ({ ok: true, data: {} }) };
  w.eval(SRC);
  return { w, calls, handlers, setStatus: (s) => { st = { ...st, ...s }; }, close: () => w.close(), click: (sel) => w.document.querySelector(sel).click() };
}
const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));

test("buttons work at once: the handler is bound without waiting for app:ready or the status call", async (t) => {
  const e = await env({ statusDelayMs: 300 }); t.after(e.close);
  e.click('[data-android-open="calls"]');                          // clicked before the slow status call finished
  await tick();
  assert.deepEqual(JSON.parse(JSON.stringify(e.calls.open)), [{ channel: "calls" }]);
});

test("Calling Sound opens the Incoming Calls channel settings; Message Sound opens the Messages channel settings", async (t) => {
  const e = await env(); t.after(e.close);
  e.click('[data-android-open="calls"]'); await tick();
  e.click('[data-android-open="messages"]'); await tick();
  assert.deepEqual(JSON.parse(JSON.stringify(e.calls.open)), [{ channel: "calls" }, { channel: "messages" }]);
});

test("a failing native call shows a clear error, never silence", async (t) => {
  const e = await env({ openSettings: async () => false }); t.after(e.close);
  e.click('[data-android-open="messages"]'); await tick();
  assert.equal(e.calls.toasts.at(-1)[1], "error");
});

test("Allow: permission granted via the Android dialog -> real success message", async (t) => {
  const e = await env({ status: { permission: "denied", enabled: false, fullScreenIntent: "granted" }, request: { permission: "granted", enabled: true } }); t.after(e.close);
  e.click('[data-android-open="allow"]'); await tick(30);
  assert.equal(e.calls.request, 1);
  assert.equal(e.calls.toasts.at(-1)[0], "android.perm_granted");
  assert.equal(e.calls.open.length, 0);
});

test("Allow: denied / permanently denied -> the dialog is tried, then the app's Android Settings page opens, and the message says it is still off", async (t) => {
  const e = await env({ status: { permission: "denied", enabled: false, fullScreenIntent: "granted" }, request: { permission: "denied", enabled: false } }); t.after(e.close);
  e.click('[data-android-open="allow"]'); await tick(30);
  assert.equal(e.calls.request, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(e.calls.open)), [{}]);          // app notification settings
  assert.equal(e.calls.toasts.at(-1)[0], "android.opening_settings");
});

test("Allow: permission is granted but notifications are switched off in Android -> opens settings instead of asking again", async (t) => {
  const e = await env({ status: { permission: "granted", enabled: false, fullScreenIntent: "granted" } }); t.after(e.close);
  e.click('[data-android-open="allow"]'); await tick(30);
  assert.equal(e.calls.request, 0);
  assert.equal(e.calls.open.length, 1);
});

test("Allow: already allowed -> says so truthfully, opens nothing", async (t) => {
  const e = await env(); t.after(e.close);
  e.click('[data-android-open="allow"]'); await tick(30);
  assert.equal(e.calls.request, 0); assert.equal(e.calls.open.length, 0);
  assert.equal(e.calls.toasts.at(-1)[0], "android.perm_granted");
});

test("one click = one action (no duplicate handlers), and the button is busy while it runs", async (t) => {
  const e = await env({ openSettings: async () => { await tick(40); return true; } }); t.after(e.close);
  const btn = e.w.document.querySelector('[data-android-open="calls"]');
  btn.click(); btn.click(); btn.click();                        // double taps while busy
  assert.equal(btn.disabled, true);
  await tick(120);
  assert.equal(e.calls.open.length, 1);
  assert.equal(btn.disabled, false);
  for (const f of e.handlers["app:ready"] || []) f();           // late app:ready must not bind a second handler
  btn.click(); await tick(80);
  assert.equal(e.calls.open.length, 2);
});

test("returning from Android Settings re-reads the real permission state", async (t) => {
  const e = await env({ status: { permission: "denied", enabled: false, fullScreenIntent: "granted" } }); t.after(e.close);
  await tick(20);
  const note = e.w.document.querySelector("[data-android-status]");
  assert.equal(note.classList.contains("hidden"), false);
  e.setStatus({ permission: "granted", enabled: true });
  e.w.dispatchEvent(new e.w.Event("goppo:app-resume")); await tick(20);
  assert.equal(note.classList.contains("hidden"), true);
});

test("responsiveness: a button reacts within 50 ms even when the status call is slow", async (t) => {
  const e = await env({ statusDelayMs: 500 }); t.after(e.close);
  const t0 = Date.now();
  e.click('[data-android-open="messages"]');
  while (!e.calls.open.length && Date.now() - t0 < 1000) await tick(2);
  assert.ok(Date.now() - t0 < 50, "took " + (Date.now() - t0) + " ms");
});
