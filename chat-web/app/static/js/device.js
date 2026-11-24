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
    P.native.call("getPushToken").then((r) => { if (r.handled && r.result && r.result.token) sync(r.result.token); });
  }
  P.on("app:ready", fetchToken);
  if (P.has("nativePush")) fetchToken();       // the native layer was already ready before this script ran

  window.GoppoDevices = { sync, unregister, installId };
})();
