/* Web Push: permission, service worker, subscription and saving it on the server.
   These are SEPARATE states and each one gets its own, understandable message:
     browser support -> notification permission -> server VAPID setup -> service worker -> browser subscription -> saved on server
   Permission is requested only after an explicit tap on Settings, and never again once it is granted or blocked. */
(function () {
  "use strict";
  if (!document.body || !document.body.dataset.auth) return;

  const T = (key, values) => (window.app && window.app.t ? window.app.t(key, values) : key);
  const SYNC_KEY = "goppo_push_sync_at";
  const SYNC_EVERY_MS = 10 * 60 * 1000;

  /* ---------------------------------------------------------------- environment */
  function isIOS() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent || "")
      || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  }
  function isStandalone() {
    return (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches)
      || window.navigator.standalone === true;
  }
  function apiSupported() {
    return "Notification" in window && "serviceWorker" in navigator && "PushManager" in window;
  }
  function permissionState() {
    return "Notification" in window ? Notification.permission : "unsupported";   // granted | denied | default
  }
  function store(kind, key, value) {
    try {
      const s = kind === "local" ? localStorage : sessionStorage;
      if (value === undefined) return s.getItem(key);
      s.setItem(key, value);
    } catch (_) { /* private mode: fine */ }
    return null;
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const raw = atob((base64String + padding).replace(/-/g, "+").replace(/_/g, "/"));
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  function sameKey(sub, keyBytes) {
    try {
      const have = sub.options && sub.options.applicationServerKey;
      if (!have) return true;                       // cannot tell: assume it is fine
      const a = new Uint8Array(have);
      if (a.length !== keyBytes.length) return false;
      for (let i = 0; i < a.length; i++) if (a[i] !== keyBytes[i]) return false;
      return true;
    } catch (_) { return true; }
  }

  /* ---------------------------------------------------------------- service worker */
  async function getReg(timeoutMs) {
    if (!("serviceWorker" in navigator)) return null;
    try {
      let reg = await navigator.serviceWorker.getRegistration("/");
      if (!reg) {
        try { reg = await navigator.serviceWorker.register("/sw.js"); } catch (_) { return null; }
      }
      // .ready never settles if the worker cannot start, so it must not be awaited without a time limit.
      return await Promise.race([
        navigator.serviceWorker.ready,
        new Promise((resolve) => setTimeout(() => resolve(null), timeoutMs || 8000)),
      ]);
    } catch (_) { return null; }
  }
  async function currentSubscription(reg) {
    try {
      reg = reg || await getReg(4000);
      return reg && reg.pushManager ? await reg.pushManager.getSubscription() : null;
    } catch (_) { return null; }
  }

  /* ---------------------------------------------------------------- server */
  async function loadConfig() {
    const res = await window.app.api("/api/push/config");
    return res.ok ? res.data : null;                 // null = could not ask (offline / server error)
  }
  async function saveToServer(sub) {
    const json = sub.toJSON();
    const res = await window.app.api("/api/push/subscribe", {
      method: "POST", json: { endpoint: json.endpoint, keys: json.keys },
    });
    if (res.ok) return { ok: true };
    const fail = (error) => ({ ok: false, error, status: res.status });
    if (res.status === 0) return fail("network");
    if (res.status === 401) return fail("session");
    if (res.status === 403) return fail("forbidden");        // usually an expired CSRF token: reload fixes it
    if (res.status === 429) return fail("rate_limited");
    if (res.status === 503) return fail("server_not_configured");
    if (res.status >= 500) return fail("server_error");
    return fail("save_failed");
  }

  /* ---------------------------------------------------------------- the decision (pure, easy to test) */
  function computeState(i) {
    const none = { showEnable: false, showDisable: false, showHelp: false, enableLabel: "push.enable" };
    if (!i.apiSupported) return { ...none, code: i.ios && !i.standalone ? "ios_install" : "unsupported" };
    if (i.permission === "denied") return { ...none, code: "denied", showHelp: true, showDisable: !!i.subscribed };
    if (i.config === null) return { ...none, code: "config_unavailable", showEnable: true, enableLabel: "push.retry" };
    if (!i.config.configured) return { ...none, code: "server_not_configured", showDisable: !!i.subscribed };
    if (i.swFailed) return { ...none, code: "sw_failed", showEnable: true, enableLabel: "push.retry" };
    if (i.subscribed) return { ...none, code: "on", showDisable: true };
    if (i.permission === "granted") return { ...none, code: "granted", showEnable: true };
    return { ...none, code: "off", showEnable: true };
  }

  /* ---------------------------------------------------------------- subscribe / unsubscribe */
  async function subscribe(knownConfig) {
    if (!apiSupported()) return { ok: false, error: "unsupported" };
    if (Notification.permission === "denied") return { ok: false, error: "denied" };
    // The config was loaded when the page drew, so asking for permission below still counts as a tap.
    const cfg = knownConfig || await loadConfig();
    if (!cfg) return { ok: false, error: "network" };
    if (!cfg.configured || !cfg.publicKey) return { ok: false, error: "server_not_configured" };

    if (Notification.permission !== "granted") {
      let answer = "default";
      try { answer = await Notification.requestPermission(); } catch (_) { /* old Safari callback API */ }
      if (answer === "denied") return { ok: false, error: "denied" };
      if (answer !== "granted") return { ok: false, error: "dismissed" };
    }

    const reg = await getReg(8000);
    if (!reg || !reg.pushManager) return { ok: false, error: "sw_unavailable" };
    const key = urlBase64ToUint8Array(cfg.publicKey);
    let sub = await reg.pushManager.getSubscription();
    if (sub && !sameKey(sub, key)) {                 // the server's keys changed: the old subscription can never work
      try { await sub.unsubscribe(); } catch (_) {}
      sub = null;
    }
    if (!sub) {
      try {
        sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
      } catch (e) {
        return { ok: false, error: e && e.name === "NotAllowedError" ? "denied" : "subscribe_failed" };
      }
    }
    const saved = await saveToServer(sub);           // re-saving an existing endpoint is harmless (no duplicates)
    if (!saved.ok) return saved;
    store("session", "goppo_push_synced", sub.endpoint);
    return { ok: true, subscription: sub };
  }

  async function unsubscribe() {
    const sub = await currentSubscription();
    const endpoint = sub ? sub.endpoint : undefined;
    if (sub) { try { await sub.unsubscribe(); } catch (_) {} }
    store("session", "goppo_push_synced", "");
    const res = await window.app.api("/api/push/unsubscribe", { method: "POST", json: endpoint ? { endpoint } : {} });
    return { ok: res.ok };
  }

  async function syncPending(force) {
    if (!navigator.onLine || permissionState() !== "granted") return;
    const last = Number(store("local", SYNC_KEY) || 0);
    if (!force && Date.now() - last < SYNC_EVERY_MS) return;
    store("local", SYNC_KEY, String(Date.now()));
    try { await window.app.api("/api/push/sync", { method: "POST", json: {} }); } catch (_) {}
  }

  /* ---------------------------------------------------------------- Settings page */
  let renderGen = 0;
  let cachedConfig;                                  // undefined = not asked yet, null = could not ask

  async function renderSettingsUI(opts) {
    const root = document.getElementById("push-settings");
    if (!root) return;
    const gen = ++renderGen;
    await window.app.ready;                          // translations are loaded
    const el = (sel) => root.querySelector(sel);
    const setVisible = (node, on) => { if (node) node.classList.toggle("hidden", !on); };

    const permission = permissionState();
    const supported = apiSupported();
    if (supported && (cachedConfig === undefined || cachedConfig === null || (opts && opts.reloadConfig))) {
      cachedConfig = await loadConfig();
    }
    let reg = null, sub = null, swFailed = false;
    if (supported && permission !== "unsupported" && cachedConfig && cachedConfig.configured) {
      reg = await getReg(6000);
      swFailed = !reg;
      sub = reg ? await currentSubscription(reg) : null;
      // A subscription the server does not know about is useless: save it once per session (also revives
      // one the server had switched off after failures).
      if (sub && permission === "granted" && store("session", "goppo_push_synced") !== sub.endpoint) {
        const saved = await saveToServer(sub);
        if (saved.ok) store("session", "goppo_push_synced", sub.endpoint);
        else if (saved.error !== "network") sub = null;      // the server refused it: offer Enable again
      }
    }
    if (gen !== renderGen) return;                   // a newer render started meanwhile

    const state = computeState({
      apiSupported: supported, ios: isIOS(), standalone: isStandalone(), permission,
      config: supported ? cachedConfig : undefined, subscribed: !!sub, swFailed,
    });
    root.dataset.state = state.code;
    const status = el("[data-push-status]");
    if (status) status.textContent = T("push.state." + state.code);
    const hint = el("[data-push-hint]");
    if (hint) {
      const hintKey = { denied: "push.denied_help", ios_install: "push.ios_install_help", granted: "push.granted_help",
                        sw_failed: "push.sw_failed_help", server_not_configured: "push.server_not_configured_help" }[state.code];
      const text = [hintKey ? T(hintKey) : "", state.code === "granted" || state.code === "off" ? lastFailure : ""]
        .filter(Boolean).join(" ");
      hint.textContent = text;
      setVisible(hint, !!text);
    }
    const admin = el("[data-push-admin]");
    if (admin) {
      const bad = cachedConfig && !cachedConfig.configured
        ? [].concat(cachedConfig.missing || [], cachedConfig.problems || []) : [];
      admin.textContent = bad.length ? T("push.admin_fix", { details: bad.join(" | ") }) : "";
      setVisible(admin, bad.length > 0);
    }
    const enable = el("[data-push-enable]");
    if (enable) enable.textContent = T(state.enableLabel);
    setVisible(enable, state.showEnable);
    setVisible(el("[data-push-disable]"), state.showDisable);
    setVisible(el("[data-push-help]"), false);       // older markup; the hint line replaces it
  }

  const ERROR_TEXT = {
    unsupported: "push.state.unsupported", denied: "push.state.denied", dismissed: "push.err.dismissed",
    server_not_configured: "push.state.server_not_configured", sw_unavailable: "push.state.sw_failed",
    subscribe_failed: "push.err.subscribe_failed", save_failed: "push.err.save_failed",
    network: "toast.network_error", session: "push.err.session", forbidden: "push.err.forbidden",
    rate_limited: "push.err.rate_limited", server_error: "push.err.server_error",
  };
  let lastFailure = "";                              // stays on screen (a toast disappears after 3 seconds)

  document.addEventListener("click", async (e) => {
    const enable = e.target.closest("[data-push-enable]");
    const disable = e.target.closest("[data-push-disable]");
    if (enable) {
      if (enable.disabled) return;
      enable.disabled = true;
      const retry = document.getElementById("push-settings")?.dataset.state === "config_unavailable";
      if (retry) cachedConfig = undefined;
      const res = await subscribe(retry ? undefined : cachedConfig || undefined);
      enable.disabled = false;
      if (res.ok) {
        window.app.toast(T("push.enabled_toast"));
      } else if (res.error === "dismissed") {
        window.app.toast(T(ERROR_TEXT.dismissed));       // neutral, not an error
      } else if (res.error !== "denied" && res.error !== "server_not_configured" && res.error !== "unsupported") {
        lastFailure = T(ERROR_TEXT[res.error] || "toast.something_wrong") + (res.status ? " [HTTP " + res.status + "]" : "");
        window.app.toast(lastFailure, "error");          // real failures only
      }
      if (res.ok) lastFailure = "";
      renderSettingsUI({ reloadConfig: true });          // denied / not configured are explained in the card itself
    }
    if (disable) {
      disable.disabled = true;
      const res = await unsubscribe();
      disable.disabled = false;
      if (res.ok) window.app.toast(T("push.disabled_toast"));
      else window.app.toast(T("toast.network_error"), "error");
      renderSettingsUI();
    }
  });

  /* Coming back from the phone's site settings: show the new permission straight away. */
  const refresh = () => { if (!document.hidden) renderSettingsUI({ reloadConfig: true }); };
  document.addEventListener("visibilitychange", refresh);
  window.addEventListener("pageshow", refresh);
  window.addEventListener("focus", refresh);
  try {
    if (navigator.permissions && navigator.permissions.query && document.getElementById("push-settings")) {
      navigator.permissions.query({ name: "notifications" }).then((p) => { p.onchange = refresh; }).catch(() => {});
    }
  } catch (_) {}
  if (navigator.serviceWorker) {
    navigator.serviceWorker.addEventListener("message", (e) => {
      if (e.data && e.data.type === "push-subscription-changed") {
        store("session", "goppo_push_synced", "");
        renderSettingsUI({ reloadConfig: true });
      }
    });
  }

  /* ---------------------------------------------------------------- PWA install */
  let deferredPrompt = null;
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e;
    document.querySelectorAll("[data-pwa-install]").forEach((el) => el.classList.remove("hidden"));
  });
  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    document.querySelectorAll("[data-pwa-install]").forEach((el) => el.classList.add("hidden"));
  });
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-pwa-install]");
    if (!btn || !deferredPrompt) return;
    deferredPrompt.prompt();
    try { await deferredPrompt.userChoice; } catch (_) {}
    deferredPrompt = null;
    btn.classList.add("hidden");
  });
  function hideInstallIfNeeded() {
    if (isStandalone()) document.querySelectorAll("[data-pwa-install]").forEach((el) => el.classList.add("hidden"));
  }

  /* ---------------------------------------------------------------- start */
  window.addEventListener("online", () => { syncPending(true); });
  function start() {
    renderSettingsUI();
    hideInstallIfNeeded();
    syncPending(false);                              // at most once every 10 minutes
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();

  window.appPush = { subscribe, unsubscribe, permissionState, currentSubscription, syncPending, computeState, refresh: renderSettingsUI };
})();
