/* Goppo service worker.
   Privacy rule: pages, API answers and anything personal are NEVER cached. Only public static files
   (css, js, icons, manifest) are kept, so the app opens fast and shows a tiny offline notice.

   Also handles Web Push: shows notifications and opens the right page on click.
*/
const CACHE = "goppo-v6";
const ACK_CACHE = "goppo-pending-acks";   // receipts that could not be sent yet (phone just lost its connection)
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
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE && k !== ACK_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data === "clear-caches") {
    event.waitUntil(caches.keys().then((keys) => Promise.all(keys.map((k) => caches.delete(k)))));
  }
  if (event.data === "flush-acks") event.waitUntil(flushPendingAcks());
});

/* ---- Delivery receipt ----
   "Delivered" means: this phone's service worker really received the push. The signed token in the push is
   sent back to the server. No cookies are sent (credentials omitted): the token is the proof, and a background
   receipt must not make the person look "online". If the phone has no connection at this very moment, the
   token is kept and re-sent on the next push / app start. The server drops tokens that are too old. */
const ACK_URL = "/api/push/ack";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function postAck(token) {
  // true = done (stored or rejected for good), false = try again later
  try {
    const res = await fetch(ACK_URL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }), credentials: "omit", keepalive: true,
    });
    if (res.ok || res.status === 400) return true;     // 400 = expired / invalid: never retry it
    return false;
  } catch (_) { return false; }
}

async function sendAck(token) {
  if (!token || typeof token !== "string") return;
  for (const wait of [0, 1500, 4000]) {
    if (wait) await sleep(wait);
    if (await postAck(token)) return;
  }
  try {                                                  // still offline: keep it for later
    const cache = await caches.open(ACK_CACHE);
    await cache.put(new Request("/__ack/" + encodeURIComponent(token.slice(-40))), new Response(token));
  } catch (_) {}
}

async function flushPendingAcks() {
  try {
    const cache = await caches.open(ACK_CACHE);
    for (const req of await cache.keys()) {
      const hit = await cache.match(req);
      const token = hit ? await hit.text() : "";
      if (!token || await postAck(token)) await cache.delete(req);
    }
  } catch (_) {}
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/") && !url.pathname.startsWith("/static/uploads/")) {
    event.respondWith(
      fetch(req).then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
        return res;
      }).catch(() => caches.match(req))
    );
    return;
  }

  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("/static/offline.html")));
  }
});

/* ---- Web Push ---- */
// Only same-site paths are ever opened from a notification.
function safeTarget(url) {
  try {
    const u = new URL(url || "/notices", self.location.origin);
    return u.origin === self.location.origin ? u.pathname + u.search + u.hash : "/notices";
  } catch (_) { return "/notices"; }
}

self.addEventListener("push", (event) => {
  let data = { title: "Goppo", body: "New notification", url: "/notices", tag: "goppo" };
  try {
    if (event.data) data = Object.assign(data, event.data.json());
  } catch (e) {
    try { data.body = event.data ? event.data.text() : data.body; } catch (_) {}
  }
  const target = safeTarget(data.url);
  event.waitUntil((async () => {
    const open = await clients.matchAll({ type: "window", includeUncontrolled: true });
    // The app is open and in front: the in-app badge already shows it, so no duplicate system notification.
    // The page is told to refresh its counters right away.
    const inFront = open.filter((c) => c.visibilityState === "visible" && c.focused);
    if (inFront.length) {
      inFront.forEach((c) => c.postMessage({ type: "push-received", url: target }));
      await sendAck(data.ack);
      await flushPendingAcks();
      return;
    }
    await self.registration.showNotification(data.title || "Goppo", {
      body: data.body || "",
      icon: "/static/icons/icon-192.png",
      badge: "/static/icons/icon-192.png",
      tag: data.tag || "goppo",              // one notification per chat; a new message replaces the old one
      renotify: !!data.renotify,
      data: { url: target, notification_id: data.notification_id },
      vibrate: [80, 40, 80],
    });
    // Only after the notification was really handed to the system do we confirm receipt.
    await sendAck(data.ack);
    await flushPendingAcks();
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = safeTarget(event.notification.data && event.notification.data.url);
  event.waitUntil((async () => {
    const list = await clients.matchAll({ type: "window", includeUncontrolled: true });
    const same = list.find((c) => { try { return new URL(c.url).pathname === target.split("?")[0]; } catch (_) { return false; } });
    const client = same || list[0];
    if (client) {
      try {
        await client.focus();
        if (!same && "navigate" in client) await client.navigate(target);
        return;
      } catch (_) { /* fall through and open a new window */ }
    }
    if (clients.openWindow) await clients.openWindow(target);
  })());
});

// The browser replaced the subscription (expired / rotated). The worker cannot sign the request itself
// (it has no CSRF token), so it tells open pages; push.js saves the new one on its next run.
self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true })
    .then((list) => list.forEach((c) => c.postMessage({ type: "push-subscription-changed" }))));
});
