# Phase 3: native calling, Firebase push and audio: report

**Status in one line:** the code, the server delivery and 109 automated checks are in place; **nothing has run on a real phone, no APK was built here, and no real Firebase message has been sent.** Treat every device behaviour below as "implemented, not yet verified".

## 1. What was built
| Area | Where | What it does |
|---|---|---|
| FCM delivery (server) | `app/services/fcm_service.py`, hook in `push_service.notify_after_commit` | Firebase **HTTP v1** + OAuth2 service-account JWT (RS256, `cryptography`, no new dependency). Data-only messages. Calls: HIGH, 60 s TTL, collapse key = call id. Messages: collapse key = chat, TTL 24 h. Parallel per-device sends with 6 s timeouts inside the request: no worker/Redis/Celery. Dead tokens (`UNREGISTERED`, `INVALID_ARGUMENT`, `SENDER_ID_MISMATCH`) are invalidated and their token cleared; temporary errors only count up (token dropped after `FCM_MAX_FAILURES`=10). Reuses `native_devices`; **no migration**. Tokens/keys never logged. Admin push switch and each user's notification preferences apply. |
| Call events (server) | `calling_service.py` | Caller cancels / callee answers or declines elsewhere / ring timeout → FCM `call_cancelled` to the callee's phones (stale ringing screens close). Missed call → `missed_call` push to phones only (browser Web Push for missed calls is unchanged). |
| Browser vs native | `platform.webPushAllowed()` (Phase 1) + this phase | Browser Web Push untouched. Inside the app Web Push stays off once `nativePush` exists; the phone gets FCM only. One announcement per channel, never two on one channel (tested). |
| Plugin | `GoppoNativePlugin.kt` | Real `getPushToken`; `requestNotificationPermission`, `openNotificationSettings`, `getNotificationStatus`, `getAudioDevices`, `selectAudioDevice`, `setMicrophoneMuted`, `setSpeakerEnabled`, `acceptIncomingCall`, `rejectIncomingCall`, `endCall`, `getActiveCallState`; events `push:token`, `call:incoming/accepted/rejected/connected/ended/missed`, `audio:devicesChanged/routeChanged/muteChanged`. Phase 2 methods unchanged. Capabilities are computed live (below). |
| Firebase client | `push/FirebasePush.kt` | `FirebaseMessagingService`: validates payloads (`PushParser`), runs notifications/calls on the main thread before returning. Token refresh → `push:token` event; the **website** registers it (`PATCH /api/devices`) because it holds the session + CSRF. No second login. |
| Channels | `notify/NotificationChannels.kt` | `goppo_calls` (HIGH, **system default ringtone**, vibration, public on lock screen), `goppo_messages` (HIGH, default notification sound, private on lock screen), `goppo_missed_calls` (DEFAULT), `goppo_active_call` (LOW, silent). A channel is created **only if missing**, never changed or recreated, so the user's ringtone choice in *Settings → Apps → Goppo Chat → Notifications* survives updates. A future change of defaults needs a new channel id. No audio file ships (checked by `npm test`). |
| Messages | `notify/Notifications.kt` | `MessagingStyle`, sender avatar, one notification per chat (updates, count), group summary, tap opens `/chat/<id>`, opening the chat clears it, private lock-screen version, suppressed while that chat is on screen, receipt (`/api/push/ack`) posted. **No message text is in the payload** (existing privacy rule); the preview is the server's "N new messages" line. |
| Incoming call | `calls/*` | FCM (or the page's own `call:incoming`) → `NativeCallManager` → Telecom registration + `CallStyle` notification (default ringtone via channel, insistent, full-screen intent when allowed, timeout = ring time) + `IncomingCallActivity` (over the lock screen; caller, photo, voice/video, countdown, Accept/Decline). Accept from a locked phone first asks Android to unlock. |
| Accept path | `NativeCallManager`, `CommandBus` | Accept → **server check** (`GET /api/calling/state/<id>`; non-ringing/404 = refuse, offline = let the page re-check) → open the app → `call:accept` is delivered to the existing `calling.js` handler (retries while the page loads, waits for `app:ready`). **No native signaling, one WebRTC connection** (second Accept refused; tested in JS). Decline goes straight to `POST /api/calling/status` through a cookie-authenticated native request, so it works with no page open. |
| Stale/duplicate protection | `CallSessions` (pure, 12 unit tests), server | One call at a time; same call twice = ignored; finished/expired/cancelled calls remembered 10 min and can never ring again, even if their push arrives late; accept only while RINGING and before expiry; notification buttons of an old call do nothing. Server side: only the callee can accept (404 for others), cancelled/expired → 409 (tested). |
| Foreground service | `ActiveCallService` | Starts when the website reports the call connected. Types `phoneCall`+`microphone` (+`camera` for video), ongoing `CallStyle` notification with caller, running timer, **Mute** and **End**. Never sticky; stops when the call ends. |
| Telecom | `TelecomBridge.kt` | Self-managed `ConnectionService` (`MANAGE_OWN_CALLS`; not the dialer, no `CALL_PHONE`). Registers ringing/active calls, handles answer/reject/hang-up from headset or system, hold when a cellular call takes over (mic muted, call kept), audio route + focus while registered. Best effort: if Telecom refuses, the call continues without it. |
| Audio | `audio/AudioRouter.kt` | Earpiece / speaker / Bluetooth / wired. Telecom `CallAudioState` when registered; otherwise the Android 12+ communication-device API (legacy calls < 12), audio focus, restore on end. State exposed to the page (`GoppoPlatform.audio`) and a route button in the call overlay. |
| Website | `platform.js`, `device.js`, `calling.js`, settings page | New events/APIs, once-only contextual notification-permission prompt, settings card (permission, call sound, message sound, full-screen calls), audio-output button, richer `call:incoming`/`call:connected` details. Browser behaviour unchanged. |
| Build | `android-build.yml`, `build.gradle` | Firebase BoM 33.7.0 + `google-services` plugin **applied only if `google-services.json` exists** (decoded from a secret, validated, git-ignored). Actions moved to Node-24 versions: checkout v5, setup-java v5, setup-node v5, upload-artifact v6, setup-android v4, setup-gradle v5 (each confirmed against its `action.yml`). Debug build needs no secrets. |

**Why not Jetpack Core-Telecom:** it wraps this same `ConnectionService` (Android < 14) and `CallControl` (14+), needs the same permission, and its coroutine-scoped call objects do not fit a call whose lifecycle is owned by the website. More importantly it could not be compile-checked in the environment used here (no Google Maven), whereas the framework API was type-checked against `android.jar`. Moving to Core-Telecom is a reasonable Phase 4 step once the framework path is proven on devices.

**Capabilities are honest** (`getCapabilities`): `nativePush` only with `google-services.json` in the build **and** Google Play services on the phone; `nativeIncomingCall` only while notifications are on and the Incoming Calls channel is not switched off (otherwise the website keeps ringing itself); `nativeMessageNotifications` needs push + the Messages channel; `nativeCallNotifications` needs the ongoing-call channel; `audioRouting` always. Computed in `Capabilities.compute` (unit-tested).

## 2. Setup you must do (nothing works end-to-end without it)
**A. Firebase** (console.firebase.google.com): create/choose a project → *Add app → Android*, package `com.goppo.chat` → download `google-services.json`. In *Project settings → Cloud Messaging* make sure **Firebase Cloud Messaging API (V1)** is enabled (the legacy API is not used).

**B. GitHub secret**: `GOOGLE_SERVICES_JSON_BASE64` = `base64 -w0 google-services.json` (details in `docs/ANDROID_BUILD.md`). Existing signing secrets are unchanged.

**C. PythonAnywhere** (server key, a *different* file): Firebase *Project settings → Service accounts → Generate new private key*. Then
```
cd ~/arnab_chat_web-main && workon goppo-venv
base64 -w0 ~/Downloads/your-key.json          # paste the output into .env as the next line
nano .env                                     # FCM_SERVICE_ACCOUNT_B64=<paste>     (chmod 600 .env, as before)
```
Web tab → **Reload**. No new packages (`requests`, `cryptography` already in `requirements.txt`), **no database migration**, no worker. Check:
```
python - <<'PY'
from app import create_app
from app.services import fcm_service
app = create_app()
with app.app_context(): print(fcm_service.status())     # configured=True, project_id=..., never prints the key
PY
```
`GET /api/devices/config` then reports `"push_delivery": true`. Free PythonAnywhere accounts can only reach whitelisted hosts: `fcm.googleapis.com` and `oauth2.googleapis.com` must be reachable (I could not verify the current whitelist; a paid account has no limit). If FCM is unreachable, sends fail quietly and are counted; messages and calls still work.

| New environment variable | Default | Purpose |
|---|---|---|
| `FCM_SERVICE_ACCOUNT_B64` / `_JSON` / `_FILE` | empty | service-account key (one of the three). Empty = native push off |
| `FCM_PROJECT_ID` | from the key | override only if needed |
| `FCM_MESSAGE_PRIORITY` | `high` | `high` or `normal` for message pushes (calls always high) |
| `FCM_MAX_FAILURES` | `10` | consecutive temporary failures before a token is dropped (the app re-registers on next open) |

**D. Render**: nothing changes (calling server, signaling, TURN untouched).

## 3. What the person must enable on the phone (we cannot do it for them)
Notifications allowed for Goppo (Android 13+ asks once, in context) · *Incoming calls* channel not muted · on Android 14+ "Full-screen notifications" allowed (otherwise calls appear as a pop-up only) · Do-Not-Disturb rules apply · battery "Unrestricted"/not "Restricted" and, on Xiaomi/Huawei/Oppo/Vivo/Samsung, the vendor's auto-start/"protected app" switch. **Not guaranteed:** delivery after *Force stop*, with notifications disabled, or on devices whose maker blocks background starts. Ringtone/sound: *Settings → Apps → Goppo Chat → Notifications → Incoming calls / Messages* (also reachable from the app's Settings page).

## 4. Tests
**Executed here and passed**
| Suite | Result |
|---|---|
| Flask, whole suite | **711 passed, 49 failed.** The 49 are exactly `tests/KNOWN_PREEXISTING_FAILURES.txt` (compared by name: 0 new, none fixed). 711 = the 677 baseline + 34 new |
| New `tests/test_fcm.py` (backend, Google replaced by a recorder) | **34 / 34**: credentials (3 sources, 5 bad-key cases, no secret echoed), RS256 JWT verified with the public key, token cache + 401 refresh, OAuth failure, message payload (recipient devices only, no private text, string values, priority/ttl/collapse), receipt token works, preferences and admin switch, native failure never breaks a message, Web Push and FCM both exactly once, dead/invalid token invalidated (other devices still served, nothing logged), temporary-failure counting and re-registration, account switch on one install, logout, tokens never in API responses, incoming-call message (HIGH, 60 s, data-only), no duplicate on replay, no push for people without the app, cancel / answered-elsewhere / missed events, only the callee can accept, stale and expired calls refused |
| Kotlin unit tests (JVM, `kotlinc` + JUnit shim; CI runs real JUnit) | **44 / 44** (20 Phase 2 + 24 new: payload parsing/validation, safe paths, call state machine, duplicate/stale/expired/busy, audio model, capability computation) |
| Kotlin type-check of **all** Android sources | 0 errors against real `android.jar` (API 34), Capacitor 7.6.10 sources and the **real androidx `NotificationCompat`/`ServiceCompat`/Firebase sources** (only a few base classes stubbed); `R` generated from the real resources so a missing string/drawable fails. Negative control confirmed. Not a Gradle build. |
| JS (jsdom) | **41 / 41** (34 + 7 new: native audio events never echoed, malformed devices rejected, audio and notification APIs, native call events, native `call:incoming` triggers a poll, one WebRTC connection per call) |
| `npm run verify:bridge` | **14 / 14**: real `native-bridge.js` + shim + real `platform.js`; every `@PluginMethod` of the Kotlin plugin reachable by the page; `MainActivity`'s `COMMAND_SCRIPT` delivers Accept to `calling.js`, retries while the page loads, reports the answer; back script as before |
| `npm run verify:config` | **20 / 20** (exact permission list, nothing forbidden, only launcher + Telecom exported, no sound file, channels never recreated, no key material, Firebase conditional, workflow secrets handling) |

**Not executed**
* Gradle build/APK (no Google Maven here): **the GitHub Actions run is the first real build of Phase 3**. No successful build is claimed.
* Android framework tests (channel creation, permission prompts, notification construction, foreground-service lifecycle, Telecom, audio): they need Robolectric or a device; I did not add tests I cannot run. They are covered by the manual list below.
* The 4 calling integration tests (need the Go binary; Go/Cargo not available): the Go server is untouched.
* Real Firebase delivery, real two-phone calls: **not tested**.

## 5. Manual checklist (two phones, A calls B)
Build with Firebase secrets · B: log in, allow notifications · A→B voice call: B rings with its system ringtone, screen shows caller+photo; **B locked** (screen off) · **B app open** · **B app swiped away** · Accept → one call, audio both ways · Decline → A sees declined · A cancels while B rings → B's screen/ringtone stop · let it ring out → missed-call notification · tap a stale notification after the call ended → "already ended", no join · two devices for B → answering on one silences the other · message from A: B gets a notification, tap opens the chat, reply, grouping with several messages, hidden content on lock screen, none while B is inside that chat · change the ringtone in Android settings, update the app, check it is kept · revoke notifications / full-screen permission → pop-up fallback, website rings by itself when `nativeIncomingCall` disappears · background B during a call: ongoing notification with timer, Mute and End work · video call with camera permission · Bluetooth headset connect/disconnect mid-call, speaker toggle, wired headset, route shown in the call screen · cellular call arrives during a call · logout and re-login on one phone / switch account · token refresh (clear app data) · browser calls and browser push still work.

## 6. Existing-test baseline
Phase 2 baseline 677 passed + 49 known failures → now 711 passed + the same 49. The full suite caught two mistakes of mine during this phase (an undocumented env variable and an English string that tripped the Bengali-pages check); both fixed before delivery.

## 7. Implemented vs not yet verified
**Implemented (code + automated checks above):** FCM HTTP v1 sender with OAuth, token lifecycle, invalid-token handling, multi-device, message and call pushes, call-cancel/missed events, Firebase service, token flow via the website, channels, message/missed/incoming/ongoing notifications, incoming-call screen, accept/reject/hang-up/mute through the existing calling code, server validation, stale/duplicate protection, foreground service, Telecom registration, audio routing, notification-permission and settings flows, updated workflow.
**Not yet verified on a real device (assume bugs until tested):** every item in section 5; in particular full-screen intent over the lock screen, Accept from the lock screen, foreground-service start timing on Android 14/15, Telecom behaviour per manufacturer, Bluetooth routing, interaction between the WebView's own WebRTC audio handling and our routing, whether FCM and `fcm.googleapis.com` are reachable from your PythonAnywhere plan, and the avatar download.

## 8. Remaining for Phase 4
Run the checklist and fix what it finds · decide Telecom vs Core-Telecom after device results · "reply"/"mark read" notification actions and per-chat mute · message previews as an opt-in (needs a privacy decision) · native outgoing-call screen/ringback · download manager for site files · target SDK 36 + Play review of the full-screen-intent declaration · Robolectric/instrumented tests in CI · localisation of the call overlay strings · token cleanup on logout (`deleteToken`) · optional per-device notification category flags server-side · Doze/OEM battery guidance inside the app.
