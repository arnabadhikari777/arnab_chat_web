/* Android app only (loaded by the bundled pages, never by the website).
 * Runs first, in <head>: marks the page as the bundled UI, picks the language file, and serves the language bundle from the APK. */
(function () {
  "use strict";
  window.GOPPO_BUNDLE = true;
  var lang = "en";
  try { lang = localStorage.getItem("goppo_lang") || (/^bn/i.test(navigator.language || "") ? "bn" : "en"); } catch (e) { /* storage blocked */ }
  if (lang !== "bn") lang = "en";
  window.GOPPO_LANG = lang;

  // Every page exists once per language (it is rendered from the website's templates in that language): go to the right file.
  var m = /^\/(login|messages|chat|settings)(\.bn)?\.html$/.exec(location.pathname);
  if (m && (lang === "bn") !== !!m[2]) {
    location.replace("/" + m[1] + (lang === "bn" ? ".bn" : "") + ".html" + location.search + location.hash);
    return;
  }

  // app.js asks for /i18n/bundle.json: answer from the APK (works offline).
  var realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var u = typeof input === "string" ? input : (input && input.url) || "";
    if (u.split("?")[0] === "/i18n/bundle.json") return realFetch("/i18n/bundle-" + lang + ".json");
    return realFetch(input, init);
  };

  // The website's language links (/lang/bn?next=...) become a local switch: remember it, reload the page in that language.
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest('a[href*="/lang/"]') : null;
    if (!a) return;
    var mm = /\/lang\/(en|bn)(?:[?#]|$)/.exec(a.getAttribute("href") || "");
    if (!mm) return;
    e.preventDefault();
    try { localStorage.setItem("goppo_lang", mm[1]); } catch (x) { /* ignore */ }
    location.replace(location.pathname.replace(/(\.bn)?\.html$/, mm[1] === "bn" ? ".bn.html" : ".html") + location.search);
  }, true);

  // No service worker in the app: the files are already inside the APK.
  try { if (navigator.serviceWorker) navigator.serviceWorker.register = function () { return Promise.resolve(); }; } catch (e) { /* ignore */ }
})();
