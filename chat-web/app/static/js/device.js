/**
 * Android device registration client (Phase 1). Does nothing in a normal browser.
 *
 * Inside the Android app, once the native layer reports the "nativePush" capability and a push token, this sends the token to
 * POST /api/devices (session + CSRF, same as every other API call) and keeps it current (PATCH on rotation).
 * The token is never stored in the page and never logged. Web Push subscriptions are a different system (push.js).
 */
(function () {
  "use strict";
  const P = window.GoppoPlatform;
  if (!P || !P.isNative() || !document.body || !document.body.dataset.auth) return;

  const ID_KEY = "goppo_install_id";
  const uuid = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID()
    : "i" + Date.now().toString(36) + Math.random().toString(36).slice(2, 12));

  function installId() {
    const fromNative = P.native.info().deviceId;
    if (fromNative) return fromNative;
    try {
      let id = localStorage.getItem(ID_KEY);
      if (!id) { id = uuid(); localStorage.setItem(ID_KEY, id); }
      return id;
    } catch (_) { return uuid(); }
  }

  let registered = false;
  let lastToken = null;

  async function sync(token) {
    if (!token || !window.app || !P.has("nativePush")) return { ok: false, error: "not_ready" };
    if (registered && lastToken === token) return { ok: true, unchanged: true };
    const info = P.native.info();
    const body = { device_id: installId(), platform: "android", push_token: token, app_version: info.appVersion || undefined };
    const res = registered && lastToken !== token
      ? await window.app.api("/api/devices/" + encodeURIComponent(body.device_id), { method: "PATCH", json: { push_token: token, app_version: body.app_version } })
      : await window.app.api("/api/devices", { method: "POST", json: body });
    if (res.ok) { registered = true; lastToken = token; }
    return res;
  }

  async function unregister() {
    const res = await window.app.api("/api/devices/" + encodeURIComponent(installId()), { method: "DELETE" });
    if (res.ok) { registered = false; lastToken = null; }
    return res;
  }

  P.handle("push:token", (d) => sync(d && d.token).then((r) => ({ ok: !!r.ok })));
  function fetchToken() {
    // Ask the native layer for the current token; it may also push "push:token" by itself.
    P.native.call("getPushToken", {}, 20000).then((r) => { if (r.handled && r.result && r.result.token) sync(r.result.token); });
  }
  P.on("app:ready", fetchToken);
  if (P.has("nativePush")) fetchToken();       // the native layer was already ready before this script ran

  /* ---- Android notification permission: asked once, in context, with a reason; never at app start ---- */
  const ASKED_KEY = "goppo_notif_asked_v1";
  const T = (k, fb) => (window.app && window.app.t && window.app.t(k)) || fb;
  async function askForNotificationsOnce() {
    if (!P.notifications.available()) return;
    let asked = false;
    try { asked = !!localStorage.getItem(ASKED_KEY); } catch (_) {}
    if (asked) return;
    const st = await P.notifications.status();
    if (!st || st.permission !== "denied") return;                 // already allowed, or not needed on this Android version
    try { localStorage.setItem(ASKED_KEY, "1"); } catch (_) {}
    const yes = await window.app.confirmDialog({
      title: T("android.ask_title", "Allow notifications?"),
      text: T("android.ask_text", "Goppo uses notifications to ring you for calls and to tell you about new messages."),
      confirmLabel: T("android.ask_yes", "Allow"),
    });
    if (yes) await P.notifications.request();
  }
  P.on("app:ready", () => { setTimeout(askForNotificationsOnce, 2500); });

  /* ---- Settings page: sound / lock-screen controls that only exist in the Android app ----
   * Audit fixes (Task 2): (1) the click handler is bound as soon as the page is ready - it no longer waits for the native
   * "app:ready" event or for the (up to 3 s) status call, which made the buttons look dead; (2) bound once (no duplicate
   * handlers); (3) every button shows an immediate busy state and a real result; (4) permission state is re-read when the
   * app comes back from Android Settings; (5) no false "success": the message comes from the state Android reports. */
  const SECTION_ID = "android-settings";
  function toast(key, fb, kind) { if (window.app && window.app.toast) window.app.toast(T(key, fb), kind); }

  async function refreshAndroidStatus(root) {
    const st = await P.notifications.status();
    const note = root.querySelector("[data-android-status]");
    const fs = root.querySelector('[data-android-open="fullScreenIntent"]');
    if (!st) { if (note) { note.textContent = T("android.status_unknown", "Could not read the notification state."); note.classList.remove("hidden"); } return null; }
    const bits = [];
    if (st.permission === "denied") bits.push(T("android.status_denied", "Notification permission is not granted."));
    else if (!st.enabled) bits.push(T("android.status_off", "Notifications are off for Goppo."));
    if (st.fullScreenIntent === "denied") bits.push(T("android.status_fullscreen", "Calls will appear as a pop-up only; allow full-screen calls to see them over the lock screen."));
    if (note) { note.textContent = bits.join(" "); note.classList.toggle("hidden", bits.length === 0); }
    if (fs) fs.classList.toggle("hidden", st.fullScreenIntent !== "denied");
    return st;
  }

  async function handleAllow() {
    let st = await P.notifications.status();
    if (st && st.permission === "denied") {
      const res = await P.notifications.request();                 // the Android dialog; the user decides
      st = res || (await P.notifications.status());
      if (st && st.permission === "denied") {
        // dialog suppressed / permanently denied: the only way is the app's Android settings page
        const ok = await P.notifications.openSettings({});
        toast(ok ? "android.opening_settings" : "android.settings_unavailable", ok ? "Opening Android settings..." : "Android settings could not be opened.", ok ? undefined : "error");
        return;
      }
    } else if (st && !st.enabled) {
      const ok = await P.notifications.openSettings({});             // permission fine, but switched off in Settings
      toast(ok ? "android.opening_settings" : "android.settings_unavailable", ok ? "Opening Android settings..." : "Android settings could not be opened.", ok ? undefined : "error");
      return;
    }
    st = await P.notifications.status();
    if (st && st.permission !== "denied" && st.enabled) toast("android.perm_granted", "Notifications are allowed.");
    else if (st) toast("android.perm_denied", "Notifications are still not allowed.", "error");
  }

  async function onAndroidClick(root, e) {
    const b = e.target.closest("[data-android-open]");
    if (!b || b.disabled) return;
    const which = b.dataset.androidOpen;
    b.disabled = true; b.setAttribute("aria-busy", "true");
    try {
      if (which === "allow") await handleAllow();
      else {
        const opts = which === "fullScreenIntent" ? { target: "fullScreenIntent" } : which === "all" ? {} : { channel: which };
        const ok = await P.notifications.openSettings(opts);
        if (!ok) toast("android.settings_unavailable", "Android settings could not be opened.", "error");
      }
    } finally {
      b.disabled = false; b.removeAttribute("aria-busy");
      refreshAndroidStatus(root);
    }
  }

  function initAndroidSettings() {
    const root = document.getElementById(SECTION_ID);
    if (!root || root.dataset.bound === "1") return;
    if (!P.notifications.available() && !P.native.available()) return;
    root.dataset.bound = "1";
    root.hidden = false;
    root.addEventListener("click", (e) => { onAndroidClick(root, e); });          // bound immediately, once
    refreshAndroidStatus(root);
    window.addEventListener("goppo:app-resume", () => refreshAndroidStatus(root)); // back from Android Settings
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initAndroidSettings); else initAndroidSettings();
  P.on("app:ready", initAndroidSettings);                                           // late bridge: bind then (guarded, once)

  window.GoppoDevices = { sync, unregister, installId };
})();
