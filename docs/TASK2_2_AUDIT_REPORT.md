# Goppo Chat - Task 2.2 audit report

Scope note: Gradle build, two-phone calls, real-device notifications and media downloads are left to the owner (as requested).

## A. Bugs found and fixed (production code)
| Severity | Where | Root cause | Fix |
|---|---|---|---|
| High | `chat/api.py: send()` | A generic `except Exception` swallowed werkzeug's 413 `RequestEntityTooLarge` and answered a fake 500 | `except HTTPException: raise` before it |
| High | `site_gate.py` (login rule) | Gate runs before sign-in, so an admin was blocked when "login" was switched off, although the routes already decide per account | Rule removed; `/login`, `/api/auth/login`, `/sso` decide per account |
| High | Notice box | `chat.notices_read_all` / `chat.notices_clear_all` were referenced by the gate and spec but did not exist | Added `notification_service.clear_all`, `POST /api/notices/read-all`, `POST /api/notices/clear-all` (own rows only, push records removed explicitly), button + JS + bn/en texts |
| High | Security log | Page, `record()` and `prune()` existed but nothing wrote events | `login_failed` (never the password) and `login` (web, app, sso) are recorded |
| Medium | Admin dashboard | `i18n/*.json` had a nested `admin.stat` object the loader cannot read -> raw keys on screen | Flattened; new `tests/test_i18n_files.py` guards it |
| Medium | Site name setting | `site_name_display` was computed but no template used it | `app_name` context value now comes from the setting |
| Medium | Settings | Single-line texts accepted line breaks | `site_name`, `public_contact` reject them (multi-line text areas unchanged) |
| Medium | Announcements | Admin could publish while the switch was off (notices pointing to a blocked page) | Creation refused with a message |
| Medium | Audit log | A timed suspension ending by itself was not logged | `user.status_expired` entry (login, request hook, bulk expiry) |
| Medium | Permissions | DB admin endpoints were super-admin only only by the default | Explicit `staff.manage` entries |
| Low | Maintenance API | Error code was an i18n key | Stable code `maintenance` |
| Low | `migrations/env.py` | `fileConfig` disabled all app loggers after any in-process migration | `disable_existing_loggers=False` |
| High | `LocalStore.applyServerMessage` (Kotlin) | Server guarantees a uuid unique per SENDER only; a peer reusing my uuid could overwrite my waiting message and its outbox entry | uuid identifies only my own message; peers matched by server id; colliding uuid stored under a derived key; `Merge` keeps the existing local uuid |
| Medium | `SyncEngine.flushOutbox` (Kotlin) | After a temporary failure, later messages of the same chat were still sent -> wrong order on the server | Later sends of that chat wait for the next pass |

## B. The 49 failing Flask tests (all now pass)
Genuine application bugs / missing features (tests were right): all 9 `test_notice_clear`; `test_site_settings`: site name, single-line text, announcements switch, login switch (admin), push switch part; `test_phase3` (3: security events, suspension expiry audit); `test_admin` raw stat keys (2); `test_staff_levels` (1); `test_messaging` 413 (1); `test_admin_control` maintenance code (1).
Outdated test expectations (documented in the test files): `/` now redirects to Messages and the dashboard is `/profile` (6 tests); sign-up photo is optional by explicit code decision (3); settings-form helper lacked 4 newer switches (6 tests); audit keys `changed` / `days`; `/api/push/config` has the separate `enabled` field read by `push.js` (2); registration closed is answered by the route (redirect + notice) not by a 503 - this CHANGED a test that used to pass, please review; per-account login rate limit (8 per 15 min) now tested next to the per-address limit; admins cannot read other people's chats (404 is correct); admin test URL helper lacked `subscription_id`.
Test-harness bugs: OTP SMTP tests never reached the SMTP code (autouse fake replaced `send_email`) - 5 tests; delivery-ack test enabled CSRF before its own signed-in calls; presence test refreshed an uncommitted value.
Remaining unresolved: none. `KNOWN_PREEXISTING_FAILURES.txt` is now empty (kept because `scripts/test_all.sh` reads it).

## C. Missing / incomplete functionality
- Frontend bundle: only login, messages, chat, settings (+ offline page) are in the APK. Hosted (online-only): registration, e-mail OTP, password reset, people, profiles, notices, privacy/profile settings, admin. This is by design in `LocalRoutes` and safe (WebView only opens the app origin and the one backend host), but the "all pages bundled" requirement is NOT met. Offline copies of those pages need real work; none were faked.
- `capacitor.config.json` `allowNavigation` and the bundle builder default hard-code the PythonAnywhere host.
- Rust crate has no unit tests (`cargo test` reports 0 tests).

## D. Database and security
- Migration graph: 16 migrations, ONE head `e8a1c4b7d2f9`, no missing parents. Fresh `upgrade` on SQLite reaches the head and `compare_metadata` reports 0 schema differences. Not run against PostgreSQL (Render) or a copy of production. No schema change was needed, so no new migration; before deploying anyway back up the database (`flask` backup tool / provider snapshot).
- Security: CSRF on, one documented exemption (push ack, signed token); SSRF guard on push endpoints (allow-list + public IPs, rechecked on send); session cookies HttpOnly/SameSite=Lax/Secure in production; no user data in `innerHTML` templates; no secrets in tracked files found by grep (values not printed). Not done: dependency vulnerability scan, upload fuzzing.
- Findings left for your decision: (1) `sessionBegin` wipes a previous account's local database, including its UNSENT messages, when another account signs in (logout asks first, account switch does not). (2) SSO login creates the account before the "login switched off" check. (3) A malformed server message would stop history restore (parse errors are not skipped).

## E. Test results (actually executed here)
| Suite | Before | After |
|---|---|---|
| Flask (`pytest tests`) | 769 passed, 49 failed | **821 passed, 0 failed** (52 more tests) |
| JavaScript (jsdom) | 72 passed | 72 passed |
| Android static checks (`verify-config`, `verify-bridge`) | 23 + 14 | 23 + 14 passed |
| C, C++ native tests | passed | passed |
| Go `vet` + `-race` tests | passed | passed |
| Pure Kotlin `Merge` logic | - | 4 checks run with a JVM harness (kotlinc 1.3): uuid kept, tombstone sticky, status never backwards, new-to-phone |
Not executed: Gradle unit tests (including the new `LocalFirstLogicTest` case), APK builds, browser tests, `integration-tests`, PostgreSQL migration run, all device tests.

## F. You must test manually
APK build; two-phone call; push/notification channels; media download/auto-download; offline send then reconnect (check message order of several queued messages); logout with unsent messages; account switch; Delete for Everyone with the other phone offline; admin: Clear All in the notice box.
Deploy notes: run `bash scripts/fix_permissions.sh` once after uploading (git executable bits); generated `native/lib/*.a` stay out of git.
