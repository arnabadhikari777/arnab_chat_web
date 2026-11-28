# TASK 2 — Local-first Android APK: implementation report

Baseline: `Goppo_Chat_Phase3_Bugfixed.zip` (Flask 711 pass / 49 known failures, JS 41 pass).
**Read section 1 first: what is verified and what is not.**

## 1. Honest status

| Level | What |
|---|---|
| **Executed here and passing** | Backend (Flask) change feed / tombstones / idempotency / JSON login / migration; bundle build + its checks; page-side JS (`local-first.js`, Settings buttons, call timer); Android SQL schema + every SQL statement; pure-Kotlin rules (merge, auto-download policy, download validation, back-off, URL/route policy); Android static checks (`verify-config`, `verify-bridge`). |
| **Compiled, not run** | The Android-dependent Kotlin I wrote (`local/*`, `net/ApiClient.kt`) was compiled with `kotlinc` 1.9.25 against the real API-33 `android.jar`, **with hand-written stubs for Capacitor and WorkManager** (so signatures of those two libraries are from my memory, not verified). No errors. |
| **Not compiled at all here** | My edits to Phase-3 files: `MainActivity`, `GoppoWebViewClient`, `GoppoRuntime`, `GoppoBridgeShim`, `FirebasePush` (they need the full Gradle project). **The first real Gradle compile is your GitHub Actions run.** |
| **Not executed** | Gradle build, `testDebugUnitTest` (existing `Phase3LogicTest`/`BridgeContractTest` + my additions), APK build, instrumented tests, anything on a phone, real FCM, production server, `tests/browser`. |

So: **the APK was not built and nothing was run on a device.** Features marked "implemented" below are written and statically checked; they are
not proven on Android until the CI build succeeds and the two-phone checklist (section 9) is done.

## 2. Architecture decision and conflict (please read)

**Conflict.** The website is server-rendered (Jinja, session cookie, CSRF, flash messages, form POSTs). It cannot simply be copied into the APK,
and pages served from the APK (`https://localhost`) cannot call the website directly (different origin, SameSite cookies, no CORS).

**Chosen (safest, additive) solution** — no rewrite, website unchanged:
1. `chat-web/tools/build_android_web.py` renders the **website's own templates** (login, chat list, chat room, settings) with a placeholder user and
   a `bundle` flag, copies the website's CSS/JS/icons **byte for byte** (a test hashes them), adds bundled fonts and the language files, writes `android-app/www`.
   The pages fill themselves from the local database at run time. CI runs it before every build (`npm run build:web`), so frontend changes reach the next APK.
2. `capacitor.config.json` has **no `server.url`** any more. The backend address is `assets/goppo-backend.json` (public URL, no secrets).
3. All API traffic goes through a native HTTPS client (`ApiClient`) via the `GoppoLocal` plugin: one fixed origin, normal certificate checks, the session
   cookie from login, CSRF token from the new `/api/auth/csrf`, `Referer/Origin` sent. **No CORS or cookie weakening was needed on the website.**
4. Server-rendered pages that have no bundled twin (people, profiles, notices, registration, e-mail verification, password reset, admin, "more
   privacy/notification options") **stay hosted pages opened inside the app, online only** (`/settings?hosted=1` etc.). Links to pages that *do* have a twin
   (`/messages`, `/chat/12`, `/settings`, `/login`, notification taps) are redirected to the local page (`LocalRoutes`).
   *This is a real limitation of Requirement 2/Part A: not every HTML page is bundled.* Converting registration/OTP/people to JSON-driven pages would be a
   separate backend + frontend project.

## 3. Features implemented

- **A Bundled UI**: build tool + 4 pages × 2 languages + start/offline pages, fonts, icons; start router decides login/list from the local session so the UI opens offline; offline banner; "needs internet" messages on online-only actions.
- **B Local DB**: per-account SQLite in app-private storage (`goppo_u<id>.db`), versioned migrations (v1→v2), repository `LocalStore`; messages, attachments (server URL and local path kept separate), conversations, outbox, seen-queue, sync cursor. History restore: `GET /api/sync/head` then `/api/sync/changes` pages of 200, cursor saved after every page (resumable), unique server id + unique client uuid (no duplicates).
- **C Sync**: text and media saved locally first (status *Waiting*), persistent outbox, `SyncEngine` (single lock + re-run flag, idempotent by client uuid), WorkManager one-time job (network constraint, exponential back-off) + 15-min periodic job + `NetworkCallback` that wakes the outbox when a network returns (debounced), FCM message/“sync” pushes trigger a pass. Statuses: Sending, Waiting, Sent (only after server accepts), Delivered, Read, Failed (permanent 400/404/413/415/422; tap to retry).
- **D Delete**: Delete for Everyone is queued, shown as "Deleting…" until the **server** confirms, then tombstoned; server bumps a revision + sends a data-only FCM "sync" hint to the other person; offline recipient gets the tombstone from the change feed; tombstones are sticky (retries/history cannot resurrect text); exact text *This message was deleted for everyone.* Uninstall/reinstall never calls delete.
- **E Media**: Cloudinary kept. New incoming photo/video: auto-download only if message is new to this phone (after restore began), not mine, master + type switch ON, valid URL; old/restored media only on tap. Private storage, `.part` file → size + magic-byte check → rename, stale-download cleanup, free-space check, host allow-list (Cloudinary only), 3 redirects max. Served to the page by id through the local origin only (path confined to the account's media dir, Range supported).
- **F Settings**: master / new photos / new videos switches (persist in private SharedPreferences), storage usage, Clear Downloaded Media (local copies only, confirmation).
- **G/H Notifications**: Settings buttons now bind immediately and once, busy state, real permission result (granted / denied → dialog → app settings), state re-read on return, 60 s native wait cut to 30 s. Channel audit (static): Ongoing/Incoming/Messages/Missed use their own channels, missed-call notification is tagged per call id (no duplicates); channels are never recreated.
- **I Bugs**: Bug 1,2,3,4,5,6 as above; **Bug 7** call timer: `screen()` and `closeOverlay()` reset the timer for every non-active screen, `startTimer` never restarts a running timer, interval nulled on teardown; **Bug 8** no Phase-3 file logic removed (additions only).

## 4. Files created
`chat-web/tools/build_android_web.py`; `app/static/js/{local-first,bundle-boot,bundle-index}.js`; migration `e8a1c4b7d2f9_local_first_sync_rev.py`;
tests `test_local_sync.py`, `test_android_bundle.py`, `test_android_sql.py`, `tests/js/{local_first,settings_notifications}.test.js`;
Kotlin `local/{LocalDb,LocalStore,LocalModel,MediaRules,MediaManager,Session,SyncEngine,SyncScheduler,GoppoLocalPlugin}.kt`, `net/ApiClient.kt`, `test/.../LocalFirstLogicTest.kt`;
`android-app/bundle-assets/` (fonts + OFL licence, offline page); `goppo-backend.json`; generated `android-app/www` and `assets/public` (regenerated by CI).

## 5. Files modified
Backend: `models/chat.py`, `models/__init__.py`, `services/message_service.py`, `services/fcm_service.py`, `blueprints/chat/api.py`, `blueprints/auth/routes.py`, `i18n/{en,bn}.json`, `tools/db_inspect.py`, `tests/test_migrations.py` (HEAD constant).
Frontend: `templates/base.html`, `templates/settings/index.html` (bundle-only conditionals; website output unchanged — a test checks it), `chat.js` (seams: local media, waiting tick, upload/delete), `calling.js` (timer fix + bundled transport), `device.js` (Settings buttons), `platform.js` (timeout).
Android: `capacitor.config.json`, `AndroidManifest.xml` (+`ACCESS_NETWORK_STATE`), `build.gradle` (+`work-runtime 2.9.1`), `UrlPolicy.kt`, `GoppoWebViewClient.kt`, `GoppoRuntime.kt`, `GoppoBridgeShim.kt`, `MainActivity.kt`, `FirebasePush.kt`, `PushParser.kt`, `scripts/{build-bridge,verify-config}.mjs`, `package.json`, `.github/workflows/android-build.yml`, `UrlPolicyTest.kt`, `Phase3LogicTest.kt`; `docs/DEPLOY_RENDER.md`. `cap sync` also regenerated some Gradle/Capacitor config files.

## 6. Database changes (migration `e8a1c4b7d2f9`, additive, reversible)
`messages.sync_rev BIGINT NOT NULL DEFAULT 0` + index; new one-row table `sync_counter`. Back-fills `sync_rev = id`, counter = max id. No row is changed otherwise. Android: new local SQLite DB (section 3). **Back up the production DB before `flask db upgrade`.**

## 7. API changes (all backward compatible)
New: `GET /api/sync/head`, `GET /api/sync/changes?rev&id&limit`, `GET /api/auth/csrf`, `GET /api/auth/me`, `POST /api/auth/login` (JSON; same service, limits, switches; **CSRF not exempt**). Changed: `/api/chats?limit=` (≤500) and `peer_id`; message JSON gained `conv, sender, rev, delivered_at, seen_at, deleted_at`, attachment `id, w, h, thumb`; delete-for-everyone/-me, seen and delivered bump `sync_rev`; delete-for-everyone sends a data-only FCM `sync` hint. Existing message-send idempotency (client uuid) is reused.
**Behaviour changes to note:** (1) English `chat.deleted` text is now exactly *This message was deleted for everyone.* (Bengali text unchanged). (2) Delete permissions and Cloudinary retention are **unchanged**: sender-only, and the existing code already deletes the Cloudinary asset on Delete for Everyone. (3) *Delivered* still comes only from the existing signed FCM ack — sync never claims delivery.

## 8. Tests executed and actual results
| Suite | Result |
|---|---|
| Flask `pytest tests --ignore=tests/browser` | **769 passed, 49 failed** (baseline 711 / 49). The 49 are exactly the known `KNOWN_PREEXISTING_FAILURES` (compared by name, none new). +58 passed: 52 are in the three new files (`test_local_sync` 23, `test_android_bundle` 21, `test_android_sql` 8); the other 6 are existing parametrised tests that now also cover new keys/routes (not broken down further). |
| JS `npm test` | **72 passed, 0 failed** (was 41; +1 timer test, +10 Settings buttons, +20 local-first). |
| Android `npm test` | `verify-config` 23/23, `verify-bridge` 14/14. |
| Pure Kotlin (via `kotlinc` + stub JUnit, **not** Gradle) | **35 passed**: 19 new (`LocalFirstLogicTest`) + the whole existing `UrlPolicyTest` incl. 5 new local-origin/route/Range cases; PushParser sync-hint checked separately. |
| Regression proof | The new timer test **fails on the original `calling.js`, passes on the fixed one**. The 10 Settings tests **fail 10/10 on the original `device.js`** (its handler never binds in a harness that doesn't emit `app:ready`, which is the delayed-registration defect). |
No existing test was deleted, skipped or weakened. Two test *constants* changed because the migration head changed (`HEAD`, `db_inspect` expected schema).
**Could not execute:** everything in the "Not executed" row of section 1. **Needs a physical phone:** section 9. **Needs production Firebase/server:** FCM delivery of message/sync pushes, calling against the Render server.
Covered only indirectly: Kotlin `LocalStore`/`SyncEngine`/`MediaManager` logic (SQL validated, logic not executed — needs Robolectric or a device test); no automated test of the Capacitor bridge, WorkManager scheduling, or WebView media serving.

## 9. Manual tests still required (two phones, A and B) — none performed
1 Fresh install + login, history restores (cursor resumes if you kill the app mid-restore) · 2 Uninstall/reinstall A: history returns, B's history intact, server intact · 3 Online send; ticks Waiting→Sent→Delivered→Read · 4 Airplane mode on A, send text + photo: stay *Waiting*, kill the app, reopen, turn network on: delivered once, no duplicate · 5 B sends a NEW photo/video with auto-download ON (arrives downloaded, opens in airplane mode) and OFF (thumbnail + Download button; master OFF; photos OFF/videos ON and the reverse) · 6 After reinstall, old photos are NOT downloaded; tap one → downloads → opens offline · 7 Settings → Clear downloaded media: local copies gone, chat and server originals remain · 8 Delete for Everyone with B online, and with B in airplane mode (B shows the exact text after reconnecting; reinstall B → still tombstone) · 9 Settings: Allow notification (allow / deny / deny twice → Android settings), Calling sound, Message sound open the right channel screens; sound change takes effect; buttons react instantly · 10 Voice and video calls both directions, phone locked, backgrounded, Bluetooth/speaker, no stale timer on 2nd call · 11 Logout with an unsent message (warning appears) · 12 Open a chat from a notification tap · 13 Open People/Profile (hosted page inside the app) and return.

## 10. Known limitations / risks
- Not every page is bundled (section 2). Registration/verification/reset/people/profile/notices/admin need internet.
- First Gradle compile unverified (section 1). WorkManager/Capacitor API use is from memory.
- Delivered ticks depend on the existing push ack; a phone that only syncs (no push) does not mark delivered.
- Received voice messages/documents are not auto-downloaded and not available offline (only photos/videos per the spec); your own sent files are kept locally.
- `PATCH`/`DELETE` through `HttpURLConnection` (device registration) is untested on old Android versions.
- Video seeking from local files relies on Range support I wrote but did not test.
- Language choice in the app is local only (not saved to the server profile). Privacy/notification preference forms are the hosted page.
- Presence/typing need internet. Chat "realtime" in the app = local-DB polling + FCM wake-ups (there is no chat WebSocket in the project).
- Logout deletes this phone's local copy (shared-phone privacy); unsent messages require confirmation. Server history is never touched.
- Media outbox cap 64 MB on the phone; the server's own limits still apply (a refused file becomes *Failed*).
- Session expiry while offline: the app opens from local data; sync pauses and asks to log in again on the first 401.

## 11. Deployment steps (backend)
1. Back up DB → `flask db upgrade` (revision `e8a1c4b7d2f9`) → reload the web app. 2. **Render: add `https://localhost` to `ALLOWED_ORIGINS`** (comma-separated) or calls from the app are refused. 3. Optional repo variable `GOPPO_BACKEND_URL` (default production). 4. Push to GitHub: the workflow now installs Python deps, builds the bundle, syncs Capacitor, runs `npm test`, Kotlin unit tests, builds the APK. `google-services.json` secret unchanged.

## 12. Build instructions
CI: run **Android build** (workflow_dispatch). Local: `cd android-app && npm ci && npm run sync && npm test && cd android && ./gradlew testDebugUnitTest assembleDebug`. (`npm run sync` = build bundle → `cap sync` → rebuild bridge.) Python 3.12 + `pip install -r chat-web/requirements.txt` needed for the bundle step.
