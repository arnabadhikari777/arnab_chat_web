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

  /* ---- Settings page: sound / lock-screen controls that only exist in the Android app ---- */
  async function initAndroidSettings() {
    const root = document.getElementById("android-settings");
    if (!root || !P.notifications.available()) return;
    root.hidden = false;
    const st = await P.notifications.status();
    const note = root.querySelector("[data-android-status]");
    if (st && note) {
      const bits = [];
      if (!st.enabled) bits.push(T("android.status_off", "Notifications are off for Goppo."));
      if (st.fullScreenIntent === "denied") bits.push(T("android.status_fullscreen", "Calls will appear as a pop-up only; allow full-screen calls to see them over the lock screen."));
      note.textContent = bits.join(" ");
      note.classList.toggle("hidden", bits.length === 0);
      const fs = root.querySelector('[data-android-open="fullScreenIntent"]');
      if (fs) fs.classList.toggle("hidden", st.fullScreenIntent !== "denied");
    }
    root.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-android-open]");
      if (!b) return;
      const which = b.dataset.androidOpen;
      if (which === "allow") {
        const res = await P.notifications.request();
        if (res && res.permanentlyDenied) await P.notifications.openSettings({});
      } else if (which === "fullScreenIntent") await P.notifications.openSettings({ target: "fullScreenIntent" });
      else if (which === "all") await P.notifications.openSettings({});
      else await P.notifications.openSettings({ channel: which });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => P.on("app:ready", initAndroidSettings));
  else P.on("app:ready", initAndroidSettings);

  window.GoppoDevices = { sync, unregister, installId };
})();
