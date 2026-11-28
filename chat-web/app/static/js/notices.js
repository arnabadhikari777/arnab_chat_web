/* Notice Box: mark the notices on screen as read once the page has loaded.
   This used to be an inline <script>, which the Content Security Policy (script-src 'self') blocks. */
(function () {
  "use strict";
  function init() {
    var list = document.getElementById("notice-list");
    if (!list || !window.app) return;
    var ids = Array.prototype.slice.call(list.querySelectorAll("[data-notice-id][data-unread='1']"))
      .map(function (el) { return Number(el.dataset.noticeId); })
      .filter(Boolean);
    if (!ids.length) return;
    window.app.api(list.dataset.markUrl, {
      method: "POST",
      body: JSON.stringify({ ids: ids }),
    }).then(function (res) {
      if (!res.ok) return;
      list.querySelectorAll(".notice--unread").forEach(function (el) {
        el.classList.remove("notice--unread");
        var dot = el.querySelector(".notice__dot");
        if (dot) dot.remove();
      });
      if (window.appBadge && res.data && typeof res.data.notices === "number") {
        window.appBadge.set("notices", res.data.notices);
      }
    }).catch(function () {});
  }
  function wireClear() {
    var btn = document.getElementById("notice-clear");
    var list = document.getElementById("notice-list");
    if (!btn || !list || !window.app) return;
    btn.addEventListener("click", function () {
      window.app.confirmDialog({
        title: window.app.t("notice.clear_all"),
        text: window.app.t("notice.clear_confirm"),
        confirmLabel: window.app.t("notice.clear_all"),
        danger: true,
      }).then(function (yes) {
        if (!yes) return null;
        btn.disabled = true;
        return window.app.api(list.dataset.clearUrl, { method: "POST", json: {} }).then(function (res) {
          if (!res.ok) {
            btn.disabled = false;
            window.app.toast((res.error && res.error.message) || window.app.t("toast.something_wrong"), "error");
            return;
          }
          if (window.appBadge) window.appBadge.set("notices", 0);
          window.app.toast(window.app.t("notice.cleared"));
          window.location.reload();
        });
      });
    });
  }
  function boot() { init(); wireClear(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
