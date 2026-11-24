# Phase 2: Capacitor Android foundation: report

Scope: a buildable Android app that loads the live site and talks to it through a real `GoppoNative` plugin.
Out of scope and **not implemented** (Phase 3+): FCM push, native incoming-call screen, Telecom/ConnectionService,
ringtone, notification channels, foreground calling service, Bluetooth routing, native WebRTC, background call reliability.

## What was built
| Area | Result |
|---|---|
| Project | `android-app/`: Capacitor **7.6.10** (core/android/cli pinned), Kotlin 1.9.25, AGP 8.7.2, Gradle 8.11.1, JDK 21, compile/target SDK 35, min SDK 23. App id `com.goppo.chat` (one line in `build.gradle` + `capacitor.config.json`), name "Goppo Chat", icons from `chat-web/app/static/icons`, portrait, splash, transparent bars. |
| Live site | `server.url = https://arnabchatweb.pythonanywhere.com`, `allowNavigation` = that host only, `cleartext:false`, `allowMixedContent:false`, network-security-config (no cleartext, system CAs only), no `startPath` (the site's own routing/login decides). Cookies/sessions are the WebView's own; no second login, no CORS change. Calling-server URL is **not** in the app: Flask supplies it at runtime (`CALLING_SERVER_URL` → CSP/config), exactly as in the browser. |
| Bridge to the remote site | Capacitor injects its native bridge into the site origin but gives a remote site only a deprecated "legacy" plugin proxy. `GoppoBridgeShim` injects (document-start, site origin only) the official `@capacitor/core` runtime + `registerPlugin("GoppoNative")` (`assets/goppo-bridge.js`, built by `npm run prepare:bridge`). Must be registered **after** Capacitor's own script (it starts with `window.Capacitor = {…}`); `MainActivity` does this and restarts the first load. `platform.js` needed no change for this. |
| Plugin | `GoppoNativePlugin.kt`: `getCapabilities` (only `nativePermissions`, `appLifecycle`, `openExternal`; app version/build, stable install id, platform, sdk), `getPushToken` (explicit `{configured:false, token:null, reason:"not_configured"}`), `notify` (allow-listed events, size-limited), `openExternal` (validated http/https), `requestMediaPermissions`, `openAppSettings` (recovery after "don't ask again"), `command` listener (native→web), internal `backResult`. Strict argument validation, short error codes. |
| Permissions | Manifest: `INTERNET`, `CAMERA`, `RECORD_AUDIO`, `MODIFY_AUDIO_SETTINGS` (normal, no prompt; WebRTC call audio mode). WebView `onPermissionRequest`: only from the site origin, only audio/video capture, only after Android granted the matching runtime permission, MIDI/protected-media always refused. Denial and "don't ask again" are reported to the page. Revocation in Settings restarts the process, and every request re-checks the OS state. |
| Back button | (1) close full-screen video → (2) `app:back` to the page (closes a `<dialog>`, refuses to leave a live call, else history back) → (3) if the page has nothing left: exit; if a call is active: move to background instead of closing. No answer in 1 s → WebView history/exit. |
| Lifecycle | `app:ready` (from the page), `app:resume`, `app:pause`: each real transition delivered once, nothing before `app:ready`, reset on every page load. Backgrounding never ends a call. |
| Links | Same origin stays in the app. Other http/https → system browser (main frame only). `javascript:`, `file:`, `content:`, `intent:`, custom schemes, credentials in URL, other ports/cleartext of the site host → blocked. Capacitor's default client opened *any* scheme; replaced by `GoppoWebViewClient`. The live site is never proxied by Capacitor's legacy proxy. |
| Uploads | Capacitor's own file chooser (inherited): gallery, camera (`capture`), documents, video, multiple, cancel. Cloudinary uploads are plain page XHR and untouched. |
| Layout | Edge-to-edge + own insets handler (Capacitor 7's ignores the keyboard): margins = system bars + cutout, bottom = max(bars, keyboard). `adjustResize`. Real full-screen video (Capacitor's default hides it) with free rotation only while full screen. |
| Security | No SSL-error override, no `addJavascriptInterface`, no WebView debugging in release (debug build only), `allowBackup=false` + no data extraction, `allowFileAccess=false`, mixed content never. No keystore/secret/Firebase file in the tree. |
| CI | `.github/workflows/android-build.yml`: dispatch + push; JDK 21, Android SDK, `npm ci`, `cap sync`, `npm test`, Kotlin unit tests, **Debug APK** (always) and **signed Release APK** (only with the 4 secrets) as artifacts. See `docs/ANDROID_BUILD.md`. |

Deviations from the brief, on purpose: the Capacitor Keyboard / StatusBar / SplashScreen **plugins are not used**. They only act on pages that load their JS, which the remote site does not, and Capacitor 7's edge-to-edge handler ignores the keyboard. The same jobs are done natively (`MainActivity`, `Theme.SplashScreen`, window insets).
Only change outside `android-app/`: `platform.js` `app:back` now also closes an open `<dialog>` that has no `cancel` handler (the site's own confirm dialog already handled it); +1 JS test.

## Tests: what was actually run (in this sandbox)
| Suite | Result |
|---|---|
| Flask `pytest` (whole suite) | **677 passed, 49 failed**: the 49 are exactly `tests/KNOWN_PREEXISTING_FAILURES.txt`. **0 new failures.** |
| Phase 1 JS (jsdom) | **34 / 34 passed** (33 Phase 1 + 1 new back-button/dialog test). |
| Android Kotlin unit tests (UrlPolicy, bridge contract, lifecycle dedupe, back decisions, media mapping) | **20 / 20 passed**, compiled with `kotlinc` 1.9.25 and run against a small JUnit shim (CI runs them with real JUnit 4.13.2 via Gradle). |
| Kotlin type-check of **all** Android sources | Compiled with `kotlinc` against the real `android.jar` (API 34) and Capacitor 7.6.10's real Java sources; androidx classes were hand-written stubs. **0 errors.** This is not a Gradle build. |
| `npm run verify:bridge` | **12 / 12**: real `native-bridge.js` + shim + real `platform.js` in a sandbox with a fake native side speaking Capacitor's message protocol: official proxy replaces the legacy one, capabilities exact, `app:ready`, resume/pause delivery, `MainActivity`'s back script → `backResult`, normal-browser fallback, and a negative control. |
| `npm run verify:config` | **16 / 16** (URL, cleartext, permissions, pinned versions, no secrets, no SSL bypass…). |

**Not executed**
* Gradle build / APK: this sandbox cannot reach Google's Maven. **No APK was built here; none is claimed.** Your GitHub Actions run is the first real build.
* Calling integration tests (4): need the Go calling-server binary (Go + Cargo not available here); they error at "binary not built". The calling server, signaling, WebSocket auth and TURN/ICE files were not touched, so no change is expected. Run `scripts/test_all.sh` on a full machine/CI to confirm.
* Everything that needs a phone (below).

## Real-device checklist (not done: no device available)
Install the Debug APK, then tick off:
* **App**: launches, splash then login page; log in; kill and reopen the app → still logged in; chat list, profile, settings; airplane mode → offline page → Retry works.
* **Links**: an external link opens the system browser; same-site link stays in app.
* **Media**: first call asks mic (voice) / mic+camera (video); voice call; video call; switch camera; remote video; Deny → page explains, retry works; "Don't ask again" → `openAppSettings` path; revoke in Settings and call again.
* **Back**: closes a confirm dialog; during a call does not leave/close; on a chat goes back; on home exits; full-screen video back closes it.
* **Uploads**: gallery image, camera photo, video, document (PDF/Word), cancel the picker, multiple files where the site allows.
* **UI**: keyboard does not cover the message box; chat scrolls; status/navigation bar colours and cutout; full-screen video rotates; leaving full screen returns to portrait.
* **Bridge** (`chrome://inspect` on a debug APK): `Capacitor.Plugins.GoppoNative.getCapabilities()` returns exactly 3 capabilities; `GoppoPlatform.has("nativePush") === false`; install id identical after app restart; background/foreground sends one `app:pause`/`app:resume` each; a call survives going to the home screen for a short time (best effort, not promised).
* **Things most likely to need a tweak after the first run**: keyboard/inset behaviour on Android 15 vs 10; the first-load restart after bridge injection; downloads (the site's file downloads are not wired to Android's download manager yet).

## Environment / backend
No environment-variable change. **No backend change, no PythonAnywhere or Render redeploy needed.**

## Remaining for Phase 3
FCM (Firebase project, `google-services.json` as a secret, token → `getPushToken`/`push:token`, notification channels), native incoming-call UI (Core-Telecom/ConnectionService, full-screen intent), ringtone, foreground service for active calls, audio routing/Bluetooth (`audioRouting`), background-call reliability and battery-optimisation handling, `POST_NOTIFICATIONS` runtime permission, DownloadListener for site downloads, target SDK 36 before Play submission, R8/minify review, Play listing/signing (App Signing), instrumented tests on a device farm.
