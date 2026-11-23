// Runs the REAL push.js against stubs: node --test tests/browser/push_state.test.js  (Node 18+, no npm install)
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

function load(extra = {}, browser = {}) {
  const listeners = {};
  const win = {
    addEventListener: (n, f) => { (listeners[n] = listeners[n] || []).push(f); },
    matchMedia: () => ({ matches: false }), navigator: {}, ...(browser.win || {}),
  };
  Object.assign(win.navigator, browser.nav || {});
  const doc = {
    body: { dataset: { auth: "1" } }, readyState: "complete", hidden: false,
    addEventListener() {}, getElementById: () => null, querySelectorAll: () => [],
  };
  const ctx = { window: win, document: doc, navigator: win.navigator, Notification: win.Notification, localStorage: { getItem: () => null, setItem() {} },
    sessionStorage: { getItem: () => null, setItem() {} }, setTimeout, console, atob: (s) => Buffer.from(s, "base64").toString("binary"), ...extra };
  win.window = win; win.document = doc; win.app = { t: (k) => k, ready: Promise.resolve(), api: browser.api || (async () => ({ ok: false, status: 0 })), toast() {} };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../../app/static/js/push.js"), "utf8"), ctx);
  return win.appPush;
}

const base = { apiSupported: true, ios: false, standalone: false, permission: "default", config: { configured: true }, subscribed: false, swFailed: false };
const state = (o) => load().computeState({ ...base, ...o });

test("unsupported browser vs iPhone in Safari (needs Home Screen)", () => {
  assert.equal(state({ apiSupported: false }).code, "unsupported");
  assert.equal(state({ apiSupported: false, ios: true }).code, "ios_install");
  assert.equal(state({ apiSupported: false, ios: true, standalone: true }).code, "unsupported");
});

test("blocked permission shows help and no Enable button", () => {
  const s = state({ permission: "denied" });
  assert.equal(s.code, "denied"); assert.ok(s.showHelp); assert.ok(!s.showEnable);
});

test("server not configured is an info state: no Enable button, even if permission was granted", () => {
  for (const permission of ["default", "granted"]) {
    const s = state({ permission, config: { configured: false } });
    assert.equal(s.code, "server_not_configured"); assert.ok(!s.showEnable);
  }
});

test("permission already granted but no subscription -> Enable (no new permission prompt needed)", () => {
  const s = state({ permission: "granted" });
  assert.equal(s.code, "granted"); assert.ok(s.showEnable); assert.ok(!s.showDisable);
});

test("subscribed -> Disable only", () => {
  const s = state({ permission: "granted", subscribed: true });
  assert.equal(s.code, "on"); assert.ok(s.showDisable); assert.ok(!s.showEnable);
});

test("default permission -> Enable; config unreachable -> Try again; worker failure -> Try again", () => {
  assert.equal(state({}).code, "off");
  const c = state({ config: null }); assert.equal(c.code, "config_unavailable"); assert.equal(c.enableLabel, "push.retry");
  assert.equal(state({ swFailed: true }).code, "sw_failed");
});

function supportedBrowser({ permission = "default", api, sub } = {}) {
  const asked = [];
  const Notification = { permission, requestPermission: async () => { asked.push(1); Notification.permission = "granted"; return "granted"; } };
  const subscription = sub || {
    endpoint: "https://push.example/abc", options: {},
    toJSON() { return { endpoint: this.endpoint, keys: { p256dh: "P", auth: "A" } }; },
  };
  const reg = { pushManager: { getSubscription: async () => null, subscribe: async () => subscription } };
  const nav = { serviceWorker: { getRegistration: async () => reg, register: async () => reg, ready: Promise.resolve(reg), addEventListener() {} } };
  const calls = [];
  const wrapped = async (url, opts) => { calls.push({ url, opts }); return api(url, opts); };
  const appPush = load({}, { win: { Notification, PushManager: function () {} }, nav, api: wrapped });
  return { appPush, asked, calls };
}

// A 65-byte public key encoded as base64url, enough for urlBase64ToUint8Array.
const KEY = Buffer.from([4, ...new Array(64).fill(7)]).toString("base64url");

test("server not configured: the permission prompt is never shown", async () => {
  const { appPush, asked } = supportedBrowser({ api: async () => ({ ok: true, data: { configured: false } }) });
  const res = await appPush.subscribe();
  assert.equal(res.ok, false); assert.equal(res.error, "server_not_configured"); assert.equal(asked.length, 0);
});

test("already granted permission is not asked for again", async () => {
  const { appPush, asked } = supportedBrowser({ permission: "granted", api: async () => ({ ok: true, data: { configured: true, publicKey: KEY } }) });
  const res = await appPush.subscribe();
  assert.equal(res.ok, true); assert.equal(asked.length, 0);
});

test("REGRESSION: the subscription is saved as JSON (the old code sent no Content-Type, so the server saw an empty body)", async () => {
  const { appPush, calls } = supportedBrowser({ permission: "granted", api: async (url) =>
    url.endsWith("/config") ? { ok: true, data: { configured: true, publicKey: KEY } } : { ok: true, data: { ok: true } } });
  await appPush.subscribe();
  const save = calls.find((c) => c.url === "/api/push/subscribe");
  assert.ok(save, "subscribe endpoint was called");
  assert.deepEqual(save.opts.json, { endpoint: "https://push.example/abc", keys: { p256dh: "P", auth: "A" } });
  assert.equal(save.opts.method, "POST");
});

test("blocked permission is reported as denied without prompting", async () => {
  const { appPush, asked } = supportedBrowser({ permission: "denied", api: async () => ({ ok: true, data: { configured: true, publicKey: KEY } }) });
  const res = await appPush.subscribe();
  assert.equal(res.error, "denied"); assert.equal(asked.length, 0);
});

test("a server refusal while saving is reported as save_failed, not as a configuration error", async () => {
  const { appPush } = supportedBrowser({ permission: "granted", api: async (url) =>
    url.endsWith("/config") ? { ok: true, data: { configured: true, publicKey: KEY } } : { ok: false, status: 400 } });
  assert.equal((await appPush.subscribe()).error, "save_failed");
});

for (const [status, expected] of [[403, "forbidden"], [429, "rate_limited"], [500, "server_error"], [401, "session"], [0, "network"]]) {
  test(`saving fails with HTTP ${status} -> reported as ${expected} (with the status kept for the screen)`, async () => {
    const { appPush } = supportedBrowser({ permission: "granted", api: async (url) =>
      url.endsWith("/config") ? { ok: true, data: { configured: true, publicKey: KEY } } : { ok: false, status } });
    const res = await appPush.subscribe();
    assert.equal(res.error, expected); assert.equal(res.status, status);
  });
}
