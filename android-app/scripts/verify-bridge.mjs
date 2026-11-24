// Verifies, without a device, that the REMOTE website can reach the GoppoNative plugin through the real Capacitor
// bridge. It loads, in this order, inside a sandboxed JS context (the order Android uses):
//   1. Capacitor's own injected scripts, built exactly like JSExport.java does: global object, the REAL native-bridge.js
//      from node_modules/@capacitor/android, a legacy plugin proxy, and Capacitor.PluginHeaders
//   2. android/app/src/main/assets/goppo-bridge.js   (this project's shim)
//   3. chat-web/app/static/js/platform.js            (the real website script)
// A tiny fake "androidBridge" plays the native side by speaking Capacitor's real message protocol.
// It proves the JS <-> bridge wiring. It does NOT run the Kotlin code (that needs a device / GitHub Actions).
import vm from "node:vm";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repo = resolve(root, "..");
const read = (p) => readFileSync(p, "utf8");
const nativeBridgeJs = read(resolve(root, "node_modules/@capacitor/android/capacitor/src/main/assets/native-bridge.js"));
const shimJs = read(resolve(root, "android/app/src/main/assets/goppo-bridge.js"));
const platformJs = read(resolve(repo, "chat-web/app/static/js/platform.js"));
const mainActivity = read(resolve(root, "android/app/src/main/kotlin/com/goppo/chat/MainActivity.kt"));

// Same shape JSExport.getPluginJS() produces (addListener/removeListener have no rtype).
const methods = ["getCapabilities", "getPushToken", "notify", "openExternal", "requestMediaPermissions", "openAppSettings", "backResult"];
const headers = [{ name: "GoppoNative", methods: [...methods.map((name) => ({ name, rtype: "promise" })), { name: "addListener" }, { name: "removeListener" }] }];
const globalJs = "window.Capacitor = { DEBUG: false, isLoggingEnabled: false, Plugins: {} };";
const legacyPluginJs = `(function(w){ var a=(w.Capacitor=w.Capacitor||{}); var p=(a.Plugins=a.Plugins||{}); var t=(p['GoppoNative']={});
  t.addListener=function(e,cb){return w.Capacitor.addListener('GoppoNative',e,cb)}; t.getCapabilities=function(o){return w.Capacitor.nativePromise('GoppoNative','getCapabilities',o)}; })(window);
window.Capacitor.PluginHeaders = ${JSON.stringify(headers)};`;

function makeWindow({ userAgent, order }) {
  const sent = []; const listeners = []; const events = []; const timers = [];
  const sandbox = {
    console: { log() {}, warn() {}, error() {}, debug() {}, info() {} },
    setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms ?? 0, 20)), clearTimeout, setInterval: () => 0, clearInterval() {},
    Promise, JSON, Math, Date, Object, Array, Error, String, Number, Boolean, Symbol, URL, Map, Set, WeakMap,
    navigator: { userAgent, language: "en", platform: "Linux armv8l" },
    location: { href: "https://arnabchatweb.pythonanywhere.com/", origin: "https://arnabchatweb.pythonanywhere.com", pathname: "/", hostname: "arnabchatweb.pythonanywhere.com", protocol: "https:", search: "", hash: "" },
    document: { readyState: "complete", addEventListener() {}, removeEventListener() {}, querySelectorAll: () => [], querySelector: () => null, getElementById: () => null, createElement: () => ({ style: {}, setAttribute() {}, appendChild() {} }), documentElement: { style: {}, classList: { add() {}, remove() {} } }, body: { appendChild() {} }, cookie: "" },
    history: { length: 1, back() {} }, screen: {}, matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    Document: class Document {}, HTMLDocument: class HTMLDocument {}, XMLHttpRequest: function XMLHttpRequest() {},
    prompt: () => "false", fetch: () => Promise.resolve({}), Headers: class {}, Request: class {}, Response: class {},
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
    Event: class { constructor(type) { this.type = type; } },
  };
  const ctx = vm.createContext(sandbox);
  vm.runInContext("globalThis.window = globalThis; globalThis.self = globalThis;", ctx);
  sandbox.addEventListener = (t, fn) => listeners.push([t, fn]);
  sandbox.removeEventListener = () => {};
  sandbox.dispatchEvent = (e) => { events.push(e); return true; };
  // Native side: answers Capacitor's real postMessage protocol {callbackId, pluginId, methodName, options}.
  const callbacks = {};
  const native = {
    capabilities: ["nativePermissions", "appLifecycle", "openExternal"],
    reply(msg, data, ok = true) {
      setTimeout(() => sandbox.androidBridge.onmessage({ data: JSON.stringify({ callbackId: msg.callbackId, pluginId: msg.pluginId, methodName: msg.methodName, success: ok, ...(ok ? { data } : { error: data }) }) }), 0);
    },
  };
  sandbox.androidBridge = {
    postMessage(str) {
      const msg = JSON.parse(str); sent.push(msg);
      if (msg.pluginId !== "GoppoNative") return;
      switch (msg.methodName) {
        case "getCapabilities": return native.reply(msg, { capabilities: native.capabilities, appVersion: "1.0.0", appBuild: 1, deviceId: "11111111-2222-3333-4444-555555555555", platform: "android", sdkInt: 34, nativeApiVersion: 1 });
        case "getPushToken": return native.reply(msg, { configured: false, token: null, reason: "not_configured" });
        case "notify": return native.reply(msg, { ok: true });
        case "addListener": callbacks[msg.options.eventName] = msg.callbackId; return;
        case "backResult": return native.reply(msg, {});
        default: return native.reply(msg, {}, false);
      }
    },
  };
  native.emit = (eventName, data) => sandbox.androidBridge.onmessage({ data: JSON.stringify({ callbackId: callbacks[eventName], pluginId: "GoppoNative", methodName: "addListener", success: true, data }) });
  const scripts = {
    capacitor: () => [globalJs, nativeBridgeJs, legacyPluginJs].forEach((s, i) => vm.runInContext(s, ctx, { filename: ["globalJS", "native-bridge.js", "pluginJS"][i] })),
    shim: () => vm.runInContext(shimJs, ctx, { filename: "goppo-bridge.js" }),
    platform: () => vm.runInContext(platformJs, ctx, { filename: "platform.js" }),
  };
  for (const step of order) scripts[step]();
  return { ctx, sandbox, sent, events, native, listeners };
}
const tick = (ms = 80) => new Promise((r) => setTimeout(r, ms));
const UA = "Mozilla/5.0 (Linux; Android 14) Chrome/120 Mobile Safari/537.36 GoppoAndroid/1.0.0";
let passed = 0;
const check = (name, fn) => Promise.resolve(fn()).then(() => { passed++; console.log("  ok  " + name); }, (e) => { console.error("  FAIL " + name + "\n       " + e.message); process.exitCode = 1; });

console.log("verify-bridge: real native-bridge.js + shim + real platform.js");

// ---- production order
{
  const w = makeWindow({ userAgent: UA, order: ["capacitor", "shim", "platform"] });
  const cap = w.sandbox.Capacitor;
  await check("Capacitor native platform is detected", () => assert.equal(cap.getPlatform(), "android"));
  await check("official plugin proxy replaced the legacy one (addListener returns a Promise)", async () => {
    const r = cap.Plugins.GoppoNative.addListener("probe", () => {});
    assert.ok(r instanceof Promise, "legacy proxy would return a plain object");
    (await r).remove?.();
  });
  await check("registerPlugin is available on the remote page", () => assert.equal(typeof cap.registerPlugin, "function"));
  await tick(300);
  const P = w.sandbox.GoppoPlatform;
  await check("platform.js sees the native app and the plugin", () => { assert.ok(P, "GoppoPlatform missing"); assert.equal(P.info.native, true); });
  await check("getCapabilities reaches native and only implemented capabilities are exposed", () => {
    for (const c of ["nativePermissions", "appLifecycle", "openExternal"]) assert.equal(P.has(c), true, c);
    for (const c of ["nativePush", "nativeIncomingCall", "audioRouting", "nativeCallNotifications"]) assert.equal(P.has(c), false, c);
  });
  await check("app:ready reached native via notify()", () => assert.ok(w.sent.some((m) => m.methodName === "notify" && m.options.event === "app:ready")));
  await check("native -> web command (app:resume) reaches the page", async () => {
    w.native.emit("command", { command: "app:resume", detail: {} }); await tick();
    assert.ok(w.events.some((e) => /resume/.test(e.type)), "events: " + w.events.map((e) => e.type));
  });
  await check("native -> web command (app:pause) reaches the page", async () => {
    w.native.emit("command", { command: "app:pause", detail: {} }); await tick();
    assert.ok(w.events.some((e) => /pause/.test(e.type)));
  });
  await check("MainActivity's BACK_SCRIPT drives app:back and reports through backResult()", async () => {
    const block = mainActivity.slice(mainActivity.indexOf("const val BACK_SCRIPT"));
    const literal = [...block.slice(0, block.indexOf("\n    }")).matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1].replace(/\\"/g, '"')).join("");
    const script = literal.replace(/__SEQ__/g, "7");
    const answer = vm.runInContext(script, w.ctx);
    assert.equal(answer, "pending");
    await tick(150);
    const m = w.sent.filter((x) => x.methodName === "backResult").pop();
    assert.ok(m, "backResult not called");
    assert.equal(m.options.seq, 7);
    assert.equal(m.options.answered, true);
    assert.equal(typeof m.options.consumed, "boolean");
  });
  await check("device id from native is a valid server device_id", () => assert.match("11111111-2222-3333-4444-555555555555", /^[A-Za-z0-9._-]{8,64}$/));
}

// ---- wrong order proves why GoppoBridgeShim installs after super.onCreate()
{
  const w = makeWindow({ userAgent: UA, order: ["shim", "capacitor"] });
  await check("shim BEFORE Capacitor's script is wiped (documented reason for the install order)", () =>
    assert.equal(typeof w.sandbox.Capacitor.registerPlugin, "undefined"));
}

// ---- plain browser: no bridge at all
{
  const w = makeWindow({ userAgent: "Mozilla/5.0 (Windows NT 10.0) Chrome/120", order: ["platform"] });
  await tick(300);
  const P = w.sandbox.GoppoPlatform;
  await check("normal browser: platform.js still works with no native layer", () => { assert.ok(P); assert.equal(P.info.native, false); assert.equal(P.has("openExternal"), false); });
}
console.log(`verify-bridge: ${process.exitCode ? "FAILED" : "all " + passed + " checks passed"}`);
