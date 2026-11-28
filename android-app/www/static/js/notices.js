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
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
