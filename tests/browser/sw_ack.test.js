/* Runs the REAL app/static/js/sw.js in a sandbox (no browser, no dependencies): node tests/browser/sw_ack.test.js
   Checks that the service worker acknowledges a push only after it really processed it.
   Contract (matches app/services/push_service.py and /api/push/ack): the push payload's `ack` is ONE signed token
   string; the worker POSTs {"token": "..."} with no cookies. A 400 is final; network errors and 5xx are retried
   (3 attempts in total); if all fail the token is kept in a cache and re-sent later. */
const fs = require("fs"), vm = require("vm"), path = require("path"), assert = require("assert");
const code = fs.readFileSync(path.join(__dirname, "../../app/static/js/sw.js"), "utf8");

function boot({ clientsList = [], showFails = false, endpoint = "https://push.example/dev1", fetchPlan = [] } = {}) {
  const handlers = {}, log = { shown: [], posted: [], fetches: [], stored: [] };
  let planIdx = 0;
  const self = {
    location: { origin: "https://goppo.test" },
    addEventListener: (n, f) => { handlers[n] = f; },
    registration: {
      showNotification: async (t, o) => { if (showFails) throw new Error("denied"); log.shown.push({ t, o }); },
      pushManager: { getSubscription: async () => (endpoint ? { endpoint } : null) },
    },
    skipWaiting() {},
  };
  const sandbox = {
    self, caches: { open: async () => ({ add: async () => {}, put: async (req, res) => { log.stored.push(req); } }), keys: async () => [], delete: async () => {}, match: async () => null },
    clients: { matchAll: async () => clientsList, claim: async () => {}, openWindow: async () => {} },
    fetch: async (url, opts) => {
      log.fetches.push({ url, opts });
      const step = fetchPlan[planIdx++] || { status: 200 };
      if (step.throws) throw new Error("offline");
      return { status: step.status, ok: step.status < 400 };
    },
    setTimeout: (f) => { f(); return 0; }, URL, console, Promise, JSON,
    Request: class { constructor(url) { this.url = url; } }, Response: class { constructor(body) { this.body = body; } },
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  async function push(data) {
    let work;
    await handlers.push({ data: { json: () => data }, waitUntil: (p) => { work = p; } });
    return work.then(() => null, (e) => e);
  }
  return { push, log };
}
const ACK = "t".repeat(43);              // the signed token string, exactly as the server puts it in the payload
const tests = [];
const t = (name, fn) => tests.push([name, fn]);

t("background: shows the notification, THEN acknowledges once, without cookies", async () => {
  const w = boot();
  await w.push({ title: "Goppo", body: "hi", url: "/chat/1", tag: "msg:1", ack: ACK });
  assert.strictEqual(w.log.shown.length, 1);
  assert.strictEqual(w.log.fetches.length, 1);
  const f = w.log.fetches[0];
  assert.strictEqual(f.url, "/api/push/ack");
  assert.strictEqual(f.opts.credentials, "omit");
  assert.deepStrictEqual(JSON.parse(f.opts.body), { token: ACK });
});
t("foreground: no system notification, page told, then acknowledged", async () => {
  const w = boot({ clientsList: [{ visibilityState: "visible", focused: true, postMessage: (m) => w.log.posted.push(m) }] });
  await w.push({ url: "/notices", ack: ACK });
  assert.strictEqual(w.log.shown.length, 0);
  assert.strictEqual(w.log.posted.length, 1);
  assert.strictEqual(w.log.fetches.length, 1);
});
t("foreground: if telling the page throws, nothing is acknowledged", async () => {
  const w = boot({ clientsList: [{ visibilityState: "visible", focused: true, postMessage: () => { throw new Error("gone"); } }] });
  await w.push({ url: "/notices", ack: ACK });
  assert.strictEqual(w.log.fetches.length, 0);
});
t("notification fails to show: NO acknowledgement", async () => {
  const w = boot({ showFails: true });
  await w.push({ url: "/notices", ack: ACK });
  assert.strictEqual(w.log.fetches.length, 0);
});
t("older push without a token: nothing sent", async () => {
  const w = boot();
  await w.push({ url: "/notices" });
  assert.strictEqual(w.log.shown.length, 1);
  assert.strictEqual(w.log.fetches.length, 0);
});
t("an old-style object payload is ignored: only a token string is ever sent", async () => {
  const w = boot();
  await w.push({ url: "/notices", ack: { id: 7, token: ACK } });
  assert.strictEqual(w.log.fetches.length, 0);
});
t("a refusal (400: invalid or expired) is final: no retry, nothing stored", async () => {
  const w = boot({ fetchPlan: [{ status: 400 }] });
  await w.push({ url: "/notices", ack: ACK });
  assert.strictEqual(w.log.fetches.length, 1);
  assert.strictEqual(w.log.stored.length, 0);
});
t("network error then success: exactly one retry", async () => {
  const w = boot({ fetchPlan: [{ throws: true }, { status: 200 }] });
  await w.push({ url: "/notices", ack: ACK });
  assert.strictEqual(w.log.fetches.length, 2);
  assert.strictEqual(w.log.stored.length, 0);
});
t("server error twice then success: three attempts in total", async () => {
  const w = boot({ fetchPlan: [{ status: 503 }, { status: 503 }, { status: 200 }] });
  await w.push({ url: "/notices", ack: ACK });
  assert.strictEqual(w.log.fetches.length, 3);
  assert.strictEqual(w.log.stored.length, 0);
});
t("still failing after three attempts: the token is kept for later, never lost", async () => {
  const w = boot({ fetchPlan: [{ status: 503 }, { status: 503 }, { status: 503 }] });
  await w.push({ url: "/notices", ack: ACK });
  assert.strictEqual(w.log.fetches.length, 3);
  assert.strictEqual(w.log.stored.length, 1);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   -", name); } catch (e) { failed++; console.log("FAIL -", name, "\n      ", e.message); }
  }
  console.log(`\n${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
