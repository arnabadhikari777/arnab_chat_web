/* Runs the REAL app/static/js/sw.js in a sandbox (no browser, no dependencies): node tests/browser/sw_ack.test.js
   Checks that the service worker acknowledges a push only after it really processed it. */
const fs = require("fs"), vm = require("vm"), path = require("path"), assert = require("assert");
const code = fs.readFileSync(path.join(__dirname, "../../app/static/js/sw.js"), "utf8");

function boot({ clientsList = [], showFails = false, endpoint = "https://push.example/dev1", fetchPlan = [] } = {}) {
  const handlers = {}, log = { shown: [], posted: [], fetches: [] };
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
    self, caches: { open: async () => ({ add: async () => {}, put: async () => {} }), keys: async () => [], delete: async () => {}, match: async () => null },
    clients: { matchAll: async () => clientsList, claim: async () => {}, openWindow: async () => {} },
    fetch: async (url, opts) => {
      log.fetches.push({ url, opts });
      const step = fetchPlan[planIdx++] || { status: 200 };
      if (step.throws) throw new Error("offline");
      return { status: step.status, ok: step.status < 400 };
    },
    setTimeout: (f) => { f(); return 0; }, URL, console, Promise, JSON,
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
const ACK = { id: 7, token: "t".repeat(43) };
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
  assert.deepStrictEqual(JSON.parse(f.opts.body), { id: 7, token: ACK.token, endpoint: "https://push.example/dev1" });
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
t("no subscription endpoint available: nothing sent", async () => {
  const w = boot({ endpoint: "" });
  await w.push({ url: "/notices", ack: ACK });
  assert.strictEqual(w.log.fetches.length, 0);
});
t("a refusal (4xx) is final: no retry", async () => {
  const w = boot({ fetchPlan: [{ status: 400 }] });
  await w.push({ url: "/notices", ack: ACK });
  assert.strictEqual(w.log.fetches.length, 1);
});
t("network error then success: exactly one retry", async () => {
  const w = boot({ fetchPlan: [{ throws: true }, { status: 200 }] });
  await w.push({ url: "/notices", ack: ACK });
  assert.strictEqual(w.log.fetches.length, 2);
});
t("server error twice: stops after two attempts", async () => {
  const w = boot({ fetchPlan: [{ status: 503 }, { status: 503 }, { status: 200 }] });
  await w.push({ url: "/notices", ack: ACK });
  assert.strictEqual(w.log.fetches.length, 2);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   -", name); } catch (e) { failed++; console.log("FAIL -", name, "\n      ", e.message); }
  }
  console.log(`\n${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
