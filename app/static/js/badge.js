/* Keeps the unread counters in the bottom bar fresh on every page (simple polling). */
(function () {
  "use strict";
  const body = document.body;
  if (!body.dataset.auth) return;
  const idle = Number(body.dataset.pollIdle) || 12000;

  function show(name, count) {
    document.querySelectorAll('[data-badge="' + name + '"]').forEach((el) => {
      el.textContent = count > 99 ? "99+" : String(count);
      el.classList.toggle("hidden", !count);
    });
  }
  async function tick() {
    const res = await window.app.api("/api/summary");
    if (res.ok) {
      show("messages", res.data.messages);
      show("notices", res.data.notices);
    }
    setTimeout(tick, document.hidden ? idle * 3 : idle);
  }
  tick();
})();
