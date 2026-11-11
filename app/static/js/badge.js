/* Keeps the unread counters in the bottom bar fresh (simple polling).
   Polls only while the tab is visible, one request at a time, backs off after errors, and does nothing on
   pages that have no counters (e.g. the chat room). */
(function () {
  "use strict";
  const body = document.body;
  if (!body.dataset.auth) return;
  if (!document.querySelector("[data-badge]")) return;
  const base = Number(body.dataset.pollIdle) || 12000;
  let timer = null, busy = false, failures = 0, ctl = null, gen = 0;

  function show(name, count) {
    document.querySelectorAll('[data-badge="' + name + '"]').forEach((el) => {
      el.textContent = count > 99 ? "99+" : String(count);
      el.classList.toggle("hidden", !count);
    });
  }
  function schedule() {
    clearTimeout(timer); timer = null;
    if (document.hidden) return;
    timer = setTimeout(tick, Math.min(base * Math.pow(2, failures), 120000));
  }
  async function tick() {
    clearTimeout(timer); timer = null;
    if (busy || document.hidden) return;
    busy = true;
    const mine = ++gen;
    ctl = new AbortController();
    const signal = ctl.signal;
    const res = await window.app.api("/api/summary", { signal });
    if (mine === gen) busy = false;
    if (signal.aborted || mine !== gen) return;       // hidden meanwhile: drop the answer
    if (res.status === 401) return;                   // signed out: stop for good
    if (res.ok) { failures = 0; show("messages", res.data.messages); show("notices", res.data.notices); }
    else failures = Math.min(failures + 1, 4);
    schedule();
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clearTimeout(timer); timer = null; if (ctl) ctl.abort(); gen++; busy = false; }
    else tick();                                      // back again: refresh the counters at once
  });
  window.addEventListener("online", tick);
  window.addEventListener("pageshow", (e) => { if (e.persisted) tick(); });

  /* The chat list already knows the unread total: it tells us, and our next request is postponed. */
  window.appBadge = { set: show, postpone: schedule };
  tick();
})();
