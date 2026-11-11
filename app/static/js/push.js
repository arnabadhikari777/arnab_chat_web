/* Web Push subscription + offline sync + PWA install detection.
   Permission is requested only after an explicit user action on Settings. */
(function () {
  "use strict";
  if (!document.body.dataset.auth) return;

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(base64);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  async function getReg() {
    if (!("serviceWorker" in navigator)) return null;
    return navigator.serviceWorker.ready;
  }

  async function currentSubscription() {
    const reg = await getReg();
    if (!reg || !reg.pushManager) return null;
    return reg.pushManager.getSubscription();
  }

  function permissionState() {
    if (!("Notification" in window)) return "unsupported";
    return Notification.permission; // granted | denied | default
  }

  async function subscribe() {
    if (!("Notification" in window) || !("serviceWorker" in navigator) || !("PushManager" in window)) {
      return { ok: false, error: "unsupported" };
    }
    if (Notification.permission === "denied") {
      return { ok: false, error: "denied" };
    }
    if (Notification.permission !== "granted") {
      const result = await Notification.requestPermission();
      if (result !== "granted") return { ok: false, error: result || "denied" };
    }
    const keyRes = await window.app.api("/api/push/vapid-public-key");
    if (!keyRes.ok || !keyRes.data.publicKey) {
      return { ok: false, error: "not_configured" };
    }
    const reg = await getReg();
    if (!reg) return { ok: false, error: "no_sw" };
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(keyRes.data.publicKey),
      });
    }
    const json = sub.toJSON();
    const save = await window.app.api("/api/push/subscribe", {
      method: "POST",
      body: JSON.stringify({
        endpoint: json.endpoint,
        keys: json.keys,
      }),
    });
    if (!save.ok) return { ok: false, error: "save_failed" };
    return { ok: true, subscription: sub };
  }

  async function unsubscribe() {
    const sub = await currentSubscription();
    if (sub) {
      const endpoint = sub.endpoint;
      try { await sub.unsubscribe(); } catch (_) {}
      await window.app.api("/api/push/unsubscribe", {
        method: "POST",
        body: JSON.stringify({ endpoint }),
      });
    } else {
      await window.app.api("/api/push/unsubscribe", {
        method: "POST",
        body: JSON.stringify({}),
      });
    }
    return { ok: true };
  }

  async function syncPending() {
    if (!navigator.onLine) return;
    try {
      await window.app.api("/api/push/sync", { method: "POST", body: "{}" });
    } catch (_) {}
  }

  /* ---- Settings page UI ---- */
  function renderSettingsUI() {
    const root = document.getElementById("push-settings");
    if (!root) return;
    const perm = permissionState();
    const statusEl = root.querySelector("[data-push-status]");
    const enableBtn = root.querySelector("[data-push-enable]");
    const disableBtn = root.querySelector("[data-push-disable]");
    const helpEl = root.querySelector("[data-push-help]");

    function setVisible(el, on) {
      if (el) el.classList.toggle("hidden", !on);
    }

    currentSubscription().then((sub) => {
      const subscribed = !!sub;
      if (statusEl) {
        if (perm === "unsupported") statusEl.textContent = root.dataset.msgUnsupported || "Not supported";
        else if (perm === "denied") statusEl.textContent = root.dataset.msgDenied || "Permission denied in browser settings";
        else if (subscribed) statusEl.textContent = root.dataset.msgOn || "Push notifications are on";
        else if (perm === "granted") statusEl.textContent = root.dataset.msgGranted || "Permission granted — enable to finish setup";
        else statusEl.textContent = root.dataset.msgOff || "Push notifications are off";
      }
      setVisible(enableBtn, perm !== "denied" && perm !== "unsupported" && !subscribed);
      setVisible(disableBtn, subscribed);
      setVisible(helpEl, perm === "denied");
    });
  }

  document.addEventListener("click", async (e) => {
    const enable = e.target.closest("[data-push-enable]");
    const disable = e.target.closest("[data-push-disable]");
    if (enable) {
      enable.disabled = true;
      const res = await subscribe();
      enable.disabled = false;
      if (res.ok) {
        if (window.app && window.app.toast) window.app.toast(document.getElementById("push-settings")?.dataset.msgOn || "Enabled");
        renderSettingsUI();
      } else if (res.error === "denied") {
        renderSettingsUI();
      } else if (window.app && window.app.toast) {
        window.app.toast(res.error || "Failed", "error");
      }
    }
    if (disable) {
      disable.disabled = true;
      await unsubscribe();
      disable.disabled = false;
      renderSettingsUI();
    }
  });

  /* ---- PWA install ---- */
  let deferredPrompt = null;
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e;
    document.querySelectorAll("[data-pwa-install]").forEach((el) => el.classList.remove("hidden"));
  });
  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    document.querySelectorAll("[data-pwa-install]").forEach((el) => el.classList.add("hidden"));
    try { localStorage.setItem("goppo_pwa_installed", "1"); } catch (_) {}
  });

  function isStandalone() {
    return window.matchMedia("(display-mode: standalone)").matches
      || window.navigator.standalone === true;
  }

  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-pwa-install]");
    if (!btn || !deferredPrompt) return;
    deferredPrompt.prompt();
    try { await deferredPrompt.userChoice; } catch (_) {}
    deferredPrompt = null;
    btn.classList.add("hidden");
  });

  function hideInstallIfNeeded() {
    if (isStandalone()) {
      document.querySelectorAll("[data-pwa-install]").forEach((el) => el.classList.add("hidden"));
    }
  }

  /* ---- online sync ---- */
  window.addEventListener("online", () => { syncPending(); });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      renderSettingsUI();
      hideInstallIfNeeded();
      if (navigator.onLine) syncPending();
    });
  } else {
    renderSettingsUI();
    hideInstallIfNeeded();
    if (navigator.onLine) syncPending();
  }

  window.appPush = { subscribe, unsubscribe, permissionState, currentSubscription, syncPending };
})();
