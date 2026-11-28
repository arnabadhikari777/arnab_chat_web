/* Android app only: the start page. Decides between the chat list and the login page - from the local session first, so a person
 * with no internet still gets into the app. Nothing is shown to the user except the brief splash. */
(function () {
  "use strict";
  var go = function (page) { location.replace("/" + page + (window.GOPPO_LANG === "bn" ? ".bn" : "") + ".html"); };
  var plugin = function () { return window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.GoppoLocal; };

  function waitForBridge(ms) {
    return new Promise(function (resolve) {
      var t0 = Date.now();
      (function tick() { if (plugin()) return resolve(plugin()); if (Date.now() - t0 > ms) return resolve(null); setTimeout(tick, 50); })();
    });
  }

  (async function () {
    var L = await waitForBridge(3000);
    if (!L) return go("login");                                   // not inside the app (development in a browser)
    var info = null;
    try { info = await L.sessionInfo(); } catch (e) { /* ignore */ }
    var res = null;
    try { res = await L.http({ method: "GET", path: "/api/auth/me" }); } catch (e) { /* ignore */ }
    if (res && res.code === 200) {                                // signed in on the server: make sure the local database is this person's
      try { var u = JSON.parse(res.body).user; await L.sessionBegin({ userId: u.id }); return go("messages"); } catch (e) { /* fall through */ }
    }
    if (res && res.code === 0 && info && info.userId) return go("messages");     // offline, but this phone has the person's data: open it
    go("login");                                                  // signed out (401), or nothing on this phone yet
  })();
})();
