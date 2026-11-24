/**
 * Goppo platform adapter (Phase 1: Android readiness).
 *
 * ONE place that knows where the website is running (desktop browser, mobile browser, or later the Capacitor
 * Android app) and ONE contract for talking to a future native layer. The rest of the site never checks for
 * Android itself: it asks `GoppoPlatform.has("...")` or sends an event, and everything still works without
 * Capacitor because every call has a browser fallback.
 *
 * Nothing here is native code. Without a native layer: `native.call()` answers {handled:false}, `has()` is false,
 * events only reach web listeners. See docs/ANDROID_READINESS.md for the full contract.
 *
 * WEB -> NATIVE  (GoppoPlatform.emit(name, detail), delivered to the plugin's notify({event, detail}))
 *   app:ready  call:incoming  call:connected  call:ended  call:muted-changed
 * NATIVE -> WEB  (GoppoPlatform.receive(command, detail), or the plugin's "command" listener)
 *   call:accept {call_id}  call:reject {call_id}  call:hangup {call_id}  call:set-muted {muted}
 *   call:audio-output-changed {output}  app:back  app:resume  app:pause  push:token {token}  push:received {notification_id}
 * Capabilities the native layer may report: nativeIncomingCall nativeCallNotifications nativeMessageNotifications
 *   audioRouting nativePermissions appLifecycle nativePush openExternal
 */
(function () {
  "use strict";
  if (window.GoppoPlatform) return;

  const PLUGIN = "GoppoNative";            // Capacitor plugin name the future Kotlin layer registers
  const UA_TOKEN = /GoppoAndroid\//;       // Capacitor `appendUserAgent: "GoppoAndroid/<version>"`
  const NATIVE_TIMEOUT_MS = 3000;

  const ua = navigator.userAgent || "";
  const cap = window.Capacitor || null;
  const capNative = !!(cap && typeof cap.isNativePlatform === "function" && cap.isNativePlatform());
  const capPlatform = capNative && typeof cap.getPlatform === "function" ? cap.getPlatform() : "";
  const isAndroidShell = capPlatform === "android" || (capNative && /android/i.test(ua)) || UA_TOKEN.test(ua);
  const standalone = !!((window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) || navigator.standalone === true);
  const mobileUA = /android|iphone|ipad|ipod|mobile/i.test(ua);

  const info = {
    kind: isAndroidShell ? "capacitor-android" : capNative ? "capacitor-other" : mobileUA ? "mobile-browser" : "browser",
    native: isAndroidShell || capNative,
    os: /android/i.test(ua) ? "android" : /iphone|ipad|ipod/i.test(ua) ? "ios" : "other",
    standalone,
  };

  /* ---------------------------------------------------------------- small safe helpers */
  const guard = (fn) => { try { return fn(); } catch (e) { return undefined; } };
  const withTimeout = (promise, ms) => Promise.race([promise, new Promise((_, rej) => setTimeout(() => rej(new Error("native_timeout")), ms))]);

  /* ---------------------------------------------------------------- native plugin access (lazy, optional) */
  function plugin() {
    if (!info.native) return null;
    const c = window.Capacitor;
    return (c && c.Plugins && c.Plugins[PLUGIN]) || null;
  }

  let capabilities = new Set();
  let nativeInfo = {};
  const listeners = new Map();     // event name -> Set(fn)     (web side)
  const handlers = new Map();      // command name -> fn        (web side; one owner per command)

  /** Ask the native layer to do something. Never throws. {handled:false} when there is no native layer or it failed. */
  async function nativeCall(method, args) {
    const p = plugin();
    if (!p || typeof p[method] !== "function") return { handled: false };
    try { return { handled: true, result: await withTimeout(p[method](args || {}), NATIVE_TIMEOUT_MS) }; }
    catch (e) { return { handled: false, error: String((e && e.message) || e) }; }
  }

  /* ---------------------------------------------------------------- events: web -> (web listeners, native) */
  function on(name, fn) {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(fn);
    return () => listeners.get(name) && listeners.get(name).delete(fn);
  }

  function emit(name, detail) {
    (listeners.get(name) || []).forEach((fn) => guard(() => fn(detail)));
    guard(() => window.dispatchEvent(new CustomEvent("goppo:" + name, { detail })));
    const p = plugin();
    if (p && typeof p.notify === "function") {
      guard(() => Promise.resolve(p.notify({ event: name, detail: detail || {} })).catch(() => {}));
    }
  }

  /* ---------------------------------------------------------------- commands: native -> web */
  function handle(command, fn) { handlers.set(command, fn); }

  /** Entry point for the native layer. Returns a Promise of {handled, result}. Unknown commands are ignored. */
  async function receive(command, detail) {
    const fn = handlers.get(command);
    if (!fn) return { handled: false };
    try { return { handled: true, result: await fn(detail || {}) }; }
    catch (e) { return { handled: false, error: String((e && e.message) || e) }; }
  }

  /* ---------------------------------------------------------------- capabilities */
  const has = (name) => capabilities.has(name);
  /** Web Push must stay off inside the Android app once native push exists, or one phone would get every notice twice. */
  const webPushAllowed = () => !(info.native && has("nativePush"));

  async function init() {
    const p = plugin();
    if (!p) return;
    const r = await nativeCall("getCapabilities");
    if (r.handled && r.result) {
      capabilities = new Set(Array.isArray(r.result.capabilities) ? r.result.capabilities.filter((x) => typeof x === "string") : []);
      nativeInfo = { appVersion: r.result.appVersion || null, deviceId: r.result.deviceId || null };
    }
    if (typeof p.addListener === "function") {
      guard(() => p.addListener("command", (m) => { if (m && typeof m.command === "string") receive(m.command, m.detail); }));
    }
    emit("app:ready", { platform: info.kind });
  }

  /* ---------------------------------------------------------------- browser fallbacks that are useful everywhere */
  /** Back button: close an open dialog first, then go back in history. {handled:false} = nothing left to go back to
   *  (the native layer may then leave the app). The website behaves the same in any browser. */
  handle("app:back", () => {
    const dlg = document.querySelector("dialog[open]");
    if (dlg) {
      // Same as pressing Esc: let the dialog's own "cancel" handler decide; if it does not, close it.
      guard(() => { const ev = new Event("cancel", { cancelable: true }); dlg.dispatchEvent(ev); if (!ev.defaultPrevented && dlg.open && typeof dlg.close === "function") dlg.close(); });
      return { consumed: true };
    }
    const overlay = document.getElementById("goppo-call-overlay");
    if (overlay && !overlay.classList.contains("hidden")) return { consumed: true };   // never leave a live call by accident
    if (history.length > 1 && location.pathname !== "/") { history.back(); return { consumed: true }; }
    return { consumed: false };
  });
  handle("app:resume", () => { window.dispatchEvent(new CustomEvent("goppo:app-resume")); return {}; });
  handle("app:pause", () => { window.dispatchEvent(new CustomEvent("goppo:app-pause")); return {}; });

  /** Links to other sites: the native layer may open them in the system browser; otherwise normal browser behaviour. */
  document.addEventListener("click", (e) => {
    const a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
    if (!a || e.defaultPrevented) return;
    let u; try { u = new URL(a.href, location.href); } catch (_) { return; }
    if (!/^https?:$/.test(u.protocol) || u.origin === location.origin) return;
    if (a.target === "_blank") a.rel = ((a.rel || "") + " noopener noreferrer").trim();
    if (info.native && has("openExternal")) {
      e.preventDefault();
      nativeCall("openExternal", { url: u.href }).then((r) => { if (!r.handled) window.open(u.href, "_blank", "noopener,noreferrer"); });
    }
  });

  /* ---------------------------------------------------------------- one notification, shown once */
  const SEEN_KEY = "goppo_seen_notifications";
  /** True the first time an id is seen, false after. A notice that arrives by Web Push, by native push and by the
   *  in-page poll is shown once. Survives a page reload within the session. */
  function firstSight(id) {
    if (id === undefined || id === null) return true;
    let seen = [];
    guard(() => { seen = JSON.parse(sessionStorage.getItem(SEEN_KEY) || "[]"); });
    if (!Array.isArray(seen)) seen = [];
    if (seen.indexOf(String(id)) !== -1) return false;
    seen.push(String(id));
    guard(() => sessionStorage.setItem(SEEN_KEY, JSON.stringify(seen.slice(-200))));
    return true;
  }

  /** Microphone/camera: the browser asks by itself, the Android app may need to ask the OS first. Never blocks long. */
  async function prepareMediaPermissions(kinds) {
    if (!(info.native && has("nativePermissions"))) return { handled: false };
    return nativeCall("requestMediaPermissions", { audio: !!(kinds && kinds.audio), video: !!(kinds && kinds.video) });
  }

  window.GoppoPlatform = {
    version: 1,
    info,
    isNative: () => info.native,
    has,
    on, emit, handle, receive,
    native: { call: nativeCall, available: () => !!plugin(), info: () => ({ ...nativeInfo }) },
    webPushAllowed, firstSight, prepareMediaPermissions,
    _setCapabilitiesForTests: (list) => { capabilities = new Set(list); },
  };

  if (info.native) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
  }
})();
