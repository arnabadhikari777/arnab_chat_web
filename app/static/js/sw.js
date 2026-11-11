/* Goppo service worker.
   Privacy rule: pages, API answers and anything personal are NEVER cached. Only public static files
   (css, js, icons, manifest) are kept, so the app opens fast and shows a tiny offline notice. */
const CACHE = "goppo-v3";            // new name: the activate step below deletes the old caches (they held pages)
const PRECACHE = [
  "/static/css/tokens.css",
  "/static/css/base.css",
  "/static/css/components.css",
  "/static/js/app.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/manifest.webmanifest",
  "/static/offline.html",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => Promise.all(PRECACHE.map((u) => cache.add(u).catch(() => {}))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

/* The page tells us to wipe everything when the person logs out. */
self.addEventListener("message", (event) => {
  if (event.data === "clear-caches") {
    event.waitUntil(caches.keys().then((keys) => Promise.all(keys.map((k) => caches.delete(k)))));
  }
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Public static files: network first (never a stale buggy file), cache only as an offline fallback.
  if (url.pathname.startsWith("/static/") && !url.pathname.startsWith("/static/uploads/")) {
    event.respondWith(
      fetch(req).then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
        return res;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // Pages: always the network. Offline = a plain notice that contains no private data.
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("/static/offline.html")));
  }
  // Everything else (/api/..., /i18n/..., forms) is not touched: the browser handles it normally.
});
