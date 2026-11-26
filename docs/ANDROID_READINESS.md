# Android readiness (Phase 1)

Phase 1 prepares the **existing website** to run inside a Capacitor Android WebView later. No APK, no Android project, no FCM sending,
no native call UI. The browser site behaves as before; everything new is optional and falls back to browser behaviour.

## Architecture
```
Android app (Phase 2+)                       PythonAnywhere (Flask, unchanged hosting)      Render (Go signaling, unchanged)
 Capacitor WebView ── loads https://<site> ─▶ pages, session cookie, CSRF, APIs, Web Push ─  token ─▶ wss://…/ws
 Kotlin plugin "GoppoNative"  ◀── platform.js contract ──▶ website JS (calling.js, push.js, device.js)
```
**Required rule for Phase 2:** the WebView must load the live site (`server.url = https://<your PythonAnywhere domain>`), not bundled copies
of the pages. Then every request is same-origin and first-party: the existing `Secure; HttpOnly; SameSite=Lax` session cookie, the CSRF token
in the page and the CSP work unchanged, and **no CORS, no token login and no second auth system are needed**. Bundled assets
(`capacitor://localhost`) would make every call cross-site and would need exactly the changes we avoided. Also set
Capacitor `appendUserAgent: "GoppoAndroid/<version>"` (platform.js uses it as one detection signal) and keep `allowNavigation` to the site only.

Findings of the audit that matter for Android (no code change needed): sessions are `permanent`, so Flask-Login "strong" protection does not log
a phone out when it moves between Wi-Fi and mobile data; the service worker is network-first for `/static/`; chat already handles the on-screen
keyboard (`--vvh`), offline banner and resend; `viewport-fit=cover` and `env(safe-area-inset-*)` are already used.

## Frontend layer (new files in `chat-web/app/static/js/`)
* **`platform.js`** (loaded first on every page): detection (`GoppoPlatform.info.kind`: `browser | mobile-browser | capacitor-android`), capability list,
  event bus, native bridge. Without a native layer: `has()` is false, `native.call()` returns `{handled:false}`, nothing throws.
* **`device.js`** (logged-in pages): inert in browsers; in the app it registers/updates the push token with `/api/devices`.
* Small hooks (each guarded, each optional): `calling.js` (events + commands), `push.js` (`apiSupported()` is false only when the app reports `nativePush`),
  `app.js` (offline / back-online notice on every page).

### Contract with the future Kotlin plugin (Capacitor plugin name `GoppoNative`)
| Direction | Name | Detail |
|---|---|---|
| plugin methods | `getCapabilities()` → `{capabilities[], appVersion, deviceId}`; `notify({event, detail})`; `addListener("command")`; optional `getPushToken()`, `requestMediaPermissions({audio, video})`, `openExternal({url})` | |
| web → native events | `app:ready`, `call:incoming`, `call:connected`, `call:ended`, `call:muted-changed` | ids and status only; never a call token |
| native → web commands (`GoppoPlatform.receive`) | `call:accept`, `call:reject`, `call:hangup` (`{call_id}`), `call:set-muted` (`{muted}`), `call:audio-output-changed` (`{output}`), `app:back`, `app:resume`, `app:pause`, `push:token` (`{token}`) | validated against the live call; repeats and wrong ids are refused |
| capabilities | `nativeIncomingCall nativeCallNotifications nativeMessageNotifications audioRouting nativePermissions appLifecycle nativePush openExternal` | |

`GoppoPlatform.firstSight(notification_id)` lets any channel (Web Push, native push, poll) show a notification once.

## New backend interfaces
All need a logged-in session and CSRF (same as the rest of the site); rate limited; the push token is accepted but **never returned**.
| Method + path | Purpose |
|---|---|
| `GET /api/devices/config` | `{enabled, platforms, push_delivery, max_devices}` (no secrets) |
| `POST /api/devices` | register/refresh `{device_id, platform:"android", push_token?, app_version?, os_version?, device_model?}` → 201 created / 200 refreshed |
| `PATCH /api/devices/<device_id>` | token rotation, new app/OS version → 200, 404 if not yours |
| `DELETE /api/devices/<device_id>` | unregister (also done automatically on logout) |
| `GET /api/devices` | list your devices (no tokens) |
Errors: `{ok:false, error:{code}}` with `bad_device_id | bad_token | bad_platform | bad_app_version | bad_os_version | bad_device_model | not_found | devices_disabled | server_error`.
Backend code: `models/device.py`, `services/device_service.py`, `blueprints/devices/`, `push_service.build_native_payload()` (data-only payload contract for Phase 3: same `tag`/`notification_id`/signed `ack` as Web Push, no message text, not sent by anything yet).

**Duplicate-notification rules:** browser subscriptions (`push_subscriptions`) and Android devices (`native_devices`) are different tables and never mix; inside the app,
`push.js` stops offering Web Push once the app reports `nativePush`; both channels share `tag`/`notification_id`/`ack`, so the delivery receipt (`/api/push/ack`) and the app's
`firstSight()` are one logic. Not solved here (Phase 3 decision): the *same phone* having both Chrome-with-push and the app.

## Database
One new migration `d4f6a8c0e2b3` (revises `c3d5e7f9a1b2`, single head): creates **only** table `native_devices` (guarded, reversible, no existing table or row touched).
## Environment (all optional, defaults shown) — `.env.example` updated
`NATIVE_DEVICES_ENABLED=1`, `NATIVE_PUSH_DELIVERY=0` (stay 0 until Phase 3), `NATIVE_MAX_DEVICES_PER_USER=5`. No CSP / Permissions-Policy / CORS / cookie change was needed.

## Deploy
* **PythonAnywhere:** back up the DB → upload `chat-web/` → `flask db heads` (one line `d4f6a8c0e2b3`) → `flask db upgrade` → Reload. No new package, no worker.
* **Render:** nothing to deploy; the Go server, its protocol, tokens, TURN/ICE and `/health` `/ready` are untouched.

## Not done (Phase 2 and later)
Phase 2: Capacitor project + `GoppoNative` plugin skeleton (`server.url`, user-agent suffix, WebView `onPermissionRequest` for mic/camera, `onBackPressed` → `app:back`, external-link handling, file chooser, keyboard resize mode), GitHub Actions APK build, real-device test of the website in the WebView.
Phase 3: FCM sender (+ Firebase credentials in server env only), device-token failure handling, notification channels, native incoming-call UI/Telecom, foreground service, audio routing, policy for "same phone has browser push and the app".

---
**Phase 2 status:** the Android shell and the real `GoppoNative` plugin exist in `android-app/` (see `docs/PHASE2_REPORT.md`, `docs/ANDROID_BUILD.md`). Implemented capabilities: `nativePermissions`, `appLifecycle`, `openExternal`. Everything else in this contract is still Phase 3.
**Phase 3 status:** `nativePush`, `nativeIncomingCall`, `nativeCallNotifications`, `nativeMessageNotifications` and `audioRouting` are now implemented in Kotlin, and each is reported only while it can really work on that phone (see `docs/PHASE3_REPORT.md`). None has been tested on a real phone yet.
