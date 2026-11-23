/* Shared browser helpers: translations, safe fetch with CSRF, toast, confirm dialog. */
(function () {
  "use strict";

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  let messages = {};
  const ready = fetch("/i18n/bundle.json", { credentials: "same-origin" })
    .then((r) => r.json())
    .then((data) => { messages = data.messages || {}; })
    .catch(() => {});

  function t(key, values) {
    let text = messages[key] || key;
    if (values) Object.keys(values).forEach((k) => { text = text.replace("{" + k + "}", values[k]); });
    return text;
  }

  /* fetch wrapper: adds CSRF token, parses JSON, never throws raw errors at the UI */
  async function api(url, options = {}) {
    const opts = { credentials: "same-origin", ...options, headers: { Accept: "application/json", ...(options.headers || {}) } };
    if (opts.method && opts.method.toUpperCase() !== "GET") opts.headers["X-CSRFToken"] = csrfToken;
    if (opts.json !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.json);
    }
    try {
      const res = await fetch(url, opts);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return { ok: false, status: res.status, error: data.error || { message: t("toast.something_wrong") } };
      return { ok: true, status: res.status, data };
    } catch (e) {
      return { ok: false, status: 0, error: { message: t("toast.network_error") } };
    }
  }

  function toast(text, kind) {
    const box = document.getElementById("toasts");
    if (!box) return;
    const el = document.createElement("div");
    el.className = "toast" + (kind ? " toast--" + kind : "");
    el.textContent = text; // textContent = no HTML injection
    box.appendChild(el);
    setTimeout(() => el.remove(), 3500);
  }

  /* Returns a promise: true if the person confirms, false if they cancel. */
  function confirmDialog({ title, text, confirmLabel, danger }) {
    return new Promise((resolve) => {
      const dlg = document.createElement("dialog");
      dlg.className = "dialog";
      dlg.innerHTML = '<h3></h3><p class="muted"></p><div class="dialog__actions"></div>';
      dlg.querySelector("h3").textContent = title;
      dlg.querySelector("p").textContent = text || "";
      const actions = dlg.querySelector(".dialog__actions");
      const ok = document.createElement("button");
      ok.className = "btn" + (danger ? " btn--danger" : "");
      ok.textContent = confirmLabel || t("common.confirm");
      const cancel = document.createElement("button");
      cancel.className = "btn btn--ghost";
      cancel.textContent = t("common.cancel");
      actions.append(ok, cancel);
      const finish = (v) => { dlg.close(); dlg.remove(); resolve(v); };
      ok.addEventListener("click", () => finish(true));
      cancel.addEventListener("click", () => finish(false));
      dlg.addEventListener("cancel", (e) => { e.preventDefault(); finish(false); });
      document.body.appendChild(dlg);
      dlg.showModal();
    });
  }

  /* Any file input with data-preview-target shows a preview and checks the size first. */
  document.addEventListener("change", async (e) => {
    const input = e.target.closest('input[type="file"][data-preview-target]');
    if (!input || !input.files[0]) return;
    const file = input.files[0];
    if (input.dataset.maxBytes && file.size > Number(input.dataset.maxBytes)) {
      await ready;
      toast(t("upload.too_large", { size: input.dataset.maxMb }), "error");
      input.value = "";
      return;
    }
    const target = document.getElementById(input.dataset.previewTarget);
    if (target) {
      const img = document.createElement("img");
      img.alt = "";
      img.src = URL.createObjectURL(file);
      target.replaceChildren(img);
    }
  });

  /* ---- dates and times in the reader's own language and time zone ---- */
  const locale = document.documentElement.lang === "bn" ? "bn-BD" : "en";
  const sameDay = (a, b) => a.toDateString() === b.toDateString();
  const dayOffset = (d) => Math.round((new Date().setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 864e5);
  const fmtTime = (iso) => new Date(iso).toLocaleTimeString(locale, { hour: "numeric", minute: "2-digit" });
  const fmtDate = (iso) => new Date(iso).toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
  function fmtDay(iso) {              // Today / Yesterday / 5 Nov 2026
    const n = dayOffset(iso);
    return n === 0 ? t("chat.today") : n === 1 ? t("chat.yesterday") : fmtDate(iso);
  }
  function fmtStamp(iso) {            // list style: time today, otherwise the day
    return dayOffset(iso) === 0 ? fmtTime(iso) : fmtDay(iso);
  }
  function lastSeen(iso) {
    if (!iso) return "";
    const day = dayOffset(iso) === 0 ? "" : fmtDay(iso) + ", ";
    return t("status.last_seen", { time: day + fmtTime(iso) });
  }
  ready.then(() => document.querySelectorAll("time[data-ts]").forEach((el) => { el.textContent = fmtStamp(el.dataset.ts); }));

  /* ---- service worker: public static files only (see sw.js). Served from "/" so it covers every page. ---- */
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.getRegistrations()          // remove the old worker that lived under /static/js/
        .then((regs) => regs.forEach((r) => { if (r.scope.indexOf("/static/") !== -1) r.unregister(); }))
        .catch(() => {});
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    });
    // On logout wipe everything the worker kept, so nothing from this account stays on the device.
    document.addEventListener("submit", (e) => {
      const form = e.target;
      if (form && form.action && /\/logout\/?$/.test(new URL(form.action, location.href).pathname)) {
        if (navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage("clear-caches");
        if (window.caches) caches.keys().then((keys) => keys.forEach((k) => caches.delete(k))).catch(() => {});
      }
    });
  }

  window.app = { fmtTime, fmtDay, fmtStamp, lastSeen, sameDay, api, toast, confirmDialog, t, ready };
})();


/* Confirmation dialog for risky forms:  <form data-confirm="Are you sure?"> */
document.addEventListener("submit", (e) => {
  const message = e.target && e.target.dataset ? e.target.dataset.confirm : "";
  if (message && !window.confirm(message)) e.preventDefault();
});
