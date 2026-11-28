# Phase 3 Bug-Fix Report — Goppo Chat Native Calling

Scope: audit and stabilisation only. No architecture change, no new calling engine, Phase 4 not started.
Flask, the Go/C/C++/Rust servers, DB, auth, CSRF and the WebRTC client were **not modified**.

## 1. Bugs found and fixed

| # | Sev | Bug | Fix | File |
|---|-----|-----|-----|------|
| 1 | High | `test_all.sh` / `build_all.sh` ran sibling scripts directly; the ZIP had mode 644 → `Permission denied` | Scripts are 755 (git index mode 100755) **and** now invoke dependents with `bash`, so a lost exec bit cannot break them | `scripts/*.sh` |
| 2 | Med | Audio-focus listener not retained; `abandonAudioFocus(null)` (API < 26) left focus active | One persistent listener used for request **and** abandon; request guarded against duplicates; result/exception handled; abandoned in every teardown | `audio/AudioRouter.kt` |
| 3 | High | Default audio route was **never applied**: `refresh()` called `select()` before publishing the new device list, so `select()` found no endpoint | State published first, then default chosen | `AudioRouter.kt` |
| 4 | High | No `MODE_IN_COMMUNICATION`: speaker/SCO routing unreliable | Mode set for the call, restored only if we changed it | `AudioRouter.kt` |
| 5 | Med | Route change reported success without checking; disconnected device could stay selected; headset hot-plug ignored; mute leaked into next call; teardown steps could skip each other on an exception | 2.5 s verification emits `route_not_applied` on mismatch; vanished device dropped; new headset preferred over earpiece; mute reset; each cleanup step independent; methods synchronized | `AudioRouter.kt` |
| 6 | Critical | `ActiveCallService` started with `startForegroundService()` but, if the call had already ended, stopped **without** `startForeground()` → Android 8+ kills the app | Enters foreground with a minimal notification, then stops; `stop()` uses `stopService` | `calls/ActiveCallService.kt`, `notify/Notifications.kt` (`buildEnding`) |
| 7 | High | A `call:ended` for an unrelated/old call cancelled the timers, notification and screen of the call currently ringing | Ringing side-effects only for the current call | `calls/NativeCallManager.kt` |
| 8 | High | Late Telecom connection (async `addNewIncomingCall`) for an already-ended call stayed ringing forever | Connection refused unless that call is the live session | `calls/TelecomBridge.kt` |
| 9 | High | Accept could stay in "accepting" forever if the page never loaded (queue expiry only checked on drain; `clear()` dropped callbacks) → every later call "busy" | Per-command expiry timer; `clear()` fails queued commands | `calls/CommandBus.kt` |
| 10 | Med | Repeated `call:connected` (ICE restart) reset the call clock and restarted audio/service | Ignored when already active | `calls/CallSessions.kt` |
| 11 | Med | Stale notification intent could auto-accept via `onNewIntent` | Validated against the live ringing call | `calls/IncomingCallActivity.kt` |
| 12 | Med | Stale/late push for an ended call rang for ~4 s; reject/hang-up POST lost on a short network drop; 5xx on the CSRF page treated as "signed out" | First server check after 1 s; status POST retried (3×, backoff); 5xx ≠ signed out | `NativeCallManager.kt`, `net/SiteClient.kt` |
| 13 | Low | Un-hold unmuted a call the user had muted; avatar cached for ended calls; avatar callback after Activity destroyed | Previous mute restored; guards added | `NativeCallManager.kt`, `IncomingCallActivity.kt` |
| 14 | Med | Any exception in FCM `init`/parse would crash the app | Guarded | `push/FirebasePush.kt` |
| 15 | Med | Android 12+ Bluetooth headset routing needs `BLUETOOTH_CONNECT`, which was not declared | Declared; requested **once**, when a call connects; refusal remembered, never nagged | `AndroidManifest.xml`, `MainActivity.kt` |

**Judgement call to confirm:** #15 reverses an earlier design assertion ("BLUETOOTH_CONNECT must not be declared"). I updated `verify-config.mjs` for it. If you prefer no Bluetooth prompt, revert that one permission; calls still work on earpiece/speaker/wired.

Audited and found OK (no change): server-side dead-FCM-token cleanup, logout token removal, recipient targeting, push payload units/expiry, double-accept (no `acceptCallId` consumer), reject-after-accept (ignored), cancelled call cannot be accepted, full-screen-intent fallback to heads-up, Android 14 typed foreground service, non-exported components, CSRF/auth on native requests.

## 2. Regression tests added
- `Phase3LogicTest.kt` → `CallSessionsBugfixTest` (5): repeated connected, `isLive`, unrelated call ending, double accept/busy, cancelled call.
- `android-app/scripts/verify-config.mjs`: +1 check (scripts executable, retained focus listener, call-mode, no unsafe service start); permission list updated.

## 3. Test results (actually executed)
| Suite | Result |
|---|---|
| `bash scripts/test_all.sh` | **ALL MANDATORY SUITES PASSED** |
| C / C++ (ASan+UBSan) | passed |
| Rust | 7 passed |
| Go vet/build, `go test -race` | all packages ok* |
| Flask | **711 passed, 49 failed = exactly the 49 in `KNOWN_PREEXISTING_FAILURES.txt`; 0 new, 0 fixed** |
| JS calling client (jsdom) | 41/41 |
| Integration (real Go + Flask + WebSockets) | 4/4 |
| `android-app` `npm test` (verify-config 21 + verify-bridge 14) | passed |
| Kotlin unit tests (24 existing + 5 new) | 29/29 — run with kotlinc 1.9.25 and a **minimal JUnit shim** (real JUnit/Gradle unavailable here); CI runs them under real JUnit |
| Kotlin type-check of edited files | compiled against `android.jar` (API 33): AudioRouter fully type-checks; other files showed only unresolved AndroidX/Capacitor/Firebase references (libraries absent here). One real error I introduced (duplicate `onNewIntent`) was found this way and fixed |

\*Sandbox quirk: `proxy.golang.org` is blocked, so Go modules were fetched with `GOPROXY=direct GOSUMDB=off`; `go.sum` is unchanged. The sandbox also lacked `websockets` for the integration tests (installed with pip).

**Could not execute:** Gradle build / APK, instrumented Android tests, anything on real hardware, live Firebase, Render, TURN. The Gradle build is covered only by your GitHub Actions run.

## 4. Remaining known issues / limits
- With notifications denied on Android 13+, a background incoming call cannot be shown (Android forbids background Activity launches); visible only while the app is open.
- Telecom and audio-route behaviour, BT SCO delay (up to ~2.5 s) and OEM battery killers are hardware-dependent.
- FCM token refresh while the page is closed is picked up the next time the app page opens (the page re-registers on load).
- Server marks a token dead on FCM `INVALID_ARGUMENT` (existing behaviour, untouched).
- Git: the sandbox ZIP had no `.git`. In your repo run once: `git update-index --chmod=+x scripts/*.sh android-app/android/gradlew` and commit.

## 5. Real-device checklist (two phones) — **none performed; all need hardware**
A. Incoming voice, app open · B. background · C. locked
D. Accept / Reject from notification and full-screen UI (after reject: no lingering notification or service)
E. Outgoing voice · F. Incoming + outgoing video
G. Mute/unmute (in-app and notification button) · H. Camera on/off
I. Speaker ⇄ earpiece (UI matches real sound)
J. Bluetooth connect/disconnect mid-call (Android 12+: permission prompt appears once at connect)
K. Wired headset plug/unplug mid-call; after the call, normal media volume/routing is back
L. Wi-Fi ⇄ mobile / airplane-mode blip: call recovers, call clock does not reset, no duplicate screens
M. End from either side: both sides close, notification gone
N. Notification permission off · O. App swiped away, then incoming call
Also: second call while one is active (caller hears busy); caller cancels while ringing (screen disappears).

## 6. Existing functionality
Website chat and calling code (Flask, JS client, Go signaling, WebRTC, ICE buffering) were not touched; their suites pass unchanged.
