# Testing report

Environment: Linux sandbox (Ubuntu 24, Python 3.12, Go 1.22.2, Rust 1.75, gcc/g++ 13, libopus 1.4, Node 22). **No Docker daemon, no real
browser, no second device, no internet TURN/Render.** Run everything yourself with `./scripts/test_all.sh`.
Only tests that were actually executed and passed are listed as PASS.

## Executed and passed
| Area | What ran | Result |
|---|---|---|
| **C** | `audio-c/tests/test_audio.c` (PCM gain/limit/convert, ring buffer, Opus round trip) with `-Wall -Wextra -Werror`, and again under **AddressSanitizer + UBSan** | PASS |
| **C++** | `media-engine/tests/test_media.cpp` (quality levels, hysteresis, A/V skew, silent mic, invalid telemetry, frame self-test), `-Werror`, and under **ASan + UBSan** | PASS |
| **Rust** | `cargo test --release --locked`: 7 tests (SDP rules, ICE syntax, state machine/roles, size + rate limit, quality ordering, RTP header, FFI null/UTF-8 safety). No `unsafe` outside the FFI boundary; panics abort | 7/7 PASS |
| **Go** | `go vet`, `go build` with CGO, `go test -race`: 25 tests — token validation/forgery/replay, config safety, CORS exact origins + preflight, `/health` `/ready` `/components` `/ice` (ephemeral TURN, no secret leak), WebSocket auth (origin, missing/forged/expired/replayed token, **role from token not query**), **offer queued before callee connects then delivered**, ack → `delivered`, resend + reconnect replay, duplicate ids, Rust validation rejections, busy (409), replaced socket, hang-up/410, ring timeout, reconnect grace, message expiry, bounded backlog, injection from unregistered socket, C++/Rust counters advancing | 25/25 PASS, race detector clean (signaling suite repeated 5× to catch flakiness) |
| **Go native bridge** | binary built with `CGO_ENABLED=1`; `goppo-calling -selftest` → Rust, C, C++ `loaded=true verified=true`. **Negative tests:** build fails with the libraries removed (`cannot find -lgoppo_secure_media`) and with `CGO_ENABLED=0` | PASS |
| **Python / Flask** | `tests/test_calling.py` (12): login required, conversation required, role/token claims, incoming delivery + dedupe + notice, busy rules, accept authorization, full status table by role, server-side duration, idempotent completion, reject/cancel/busy, ring timeout → missed + notice, history privacy, 503 when unconfigured, CSP/Permissions-Policy | 12/12 PASS |
| **Migrations** | single head `d4f6a8c0e2b3`, no duplicates/missing/merges, fresh DB = models, re-run no-op, upgrade from every legacy state, **existing DB with users + a call record upgraded without loss, downgrade/upgrade round trip**; `flask db upgrade` on a fresh production-config DB | PASS (10 tests + CLI run) |
| **JavaScript** | `chat-web/tests/js` (jsdom + fake fetch/WebSocket/WebRTC): 20 tests — buttons, **wake-up polling/back-off, single flight, bounded timeout with Retry/Cancel, cancel**, permission denied, token as sub-protocol, outbox/resend after reconnect with same id, ack-first + seq de-duplication, **ICE buffering**, connected state/timer/status report, quality indicator + automatic video pause/resume, mute/camera/hang-up releasing devices, busy, remote reject/busy/hang-up, server timeout, **incoming-call event (dedupe, reject, poll)**, accept flow, busy while in call, CSRF header on every POST, reconnect in a connected call, ICE restart | 20/20 PASS |
| **End-to-end** | `integration-tests/test_calling_e2e.py`: the **real Go binary**, the **real Flask app** (it mints the tokens), real WebSocket clients: HTTP surface + CORS, full call (offer queued before callee exists → delivered after ack → answer → ICE → bad ICE rejected by Rust → quality via C++/Rust → hang-up), token replay refused, wrong origin/forged token refused, busy enforced across calls, native counters advanced | 4/4 PASS |
| **Production start** | `APP_ENV=production` WSGI import, 8 blueprints incl. `calling`, `/sw.js` 200 no-cache, CSP `connect-src` includes the Render origin, `camera=(self)`, HSTS, static JS/CSS served | PASS |
| **Existing chat regression** | whole existing Flask suite: **649 passed**. Compared with the ZIP as received: **0 new failures**, 8 previously failing tests fixed (migration/schema/env documentation) | see below |

## Pre-existing failures in the supplied ZIP (not caused by this work, not fixed)
The ZIP as received already failed **57** of its own tests; after this work **49** fail (the 8 fixed ones were the migration-head/inspector/.env documentation tests).
The remaining 49 are listed in `chat-web/tests/KNOWN_PREEXISTING_FAILURES.txt` and `scripts/test_all.sh` fails only on failures **not** in that list.
Causes seen (each needs a product decision, so they were left alone rather than guessed): notice endpoints tested at `/api/notices/read-all|clear` that return 404
(`test_notice_clear.py`); site-settings keys/behaviour that evolved (`people_enabled`, `profile_edit_enabled`; `test_site_settings.py`, `test_admin_control.py`);
OTP tests written for SMTP while the code now uses a mail API (`test_otp_email.py`); login/redirect expectations (`test_auth.py`, `test_phase3.py`, `test_people_profile.py`);
admin permission table, push config, an upload-size 413 vs 500, a delivery-ack CSRF case. The earlier reports in the ZIP admitted the suite had never been run.
**Recommendation:** triage these before relying on those specific features; they are outside the calling work.

## NOT executed (be aware)
| Not run | Why | What to do |
|---|---|---|
| **Docker image build / container run** | no Docker daemon in the sandbox | The Dockerfile steps were mirrored by hand (`scripts/build_all.sh` = same native build, tests, CGO build, `-selftest`) and pass; the image itself is untested. Render's first deploy is the real test — see `DEPLOY_RENDER.md` §5 |
| Two real devices: voice and video call, Accept/Reject, camera switching, full-screen, mobile browser layout | no browsers/devices | manual checklist in `DEPLOYMENT_CHECKLIST.md` |
| Web Push for an incoming call with the browser closed/background | needs real push services + device | manual test A3 |
| Render cold start (real 30–60 s) and token refresh over a real sleep | needs Render | manual test B1 |
| TURN connectivity (UDP/TCP/TLS), mobile carrier NAT | needs a TURN server and real networks | manual test C1–C3; do not assume universal connectivity |
| Network interruption on real radios | simulated only (socket drops in tests) | manual test D1 |
| Android Chrome / iOS Safari / desktop browsers | no browsers | manual tests E1–E3 |
| Load / many concurrent calls | not tested | run a trial with your expected users |
| Rust Miri / cargo-clippy | not installed | optional |


## Phase 1 (Android readiness) — executed on the modified tree
| Suite | Result |
|---|---|
| Flask `pytest` (whole suite) | **677 passed, 49 failed**. The 49 are exactly the 49 in `KNOWN_PREEXISTING_FAILURES.txt` (identical list before and after; baseline re-run first: 649 passed / 49 failed). 0 new failures; +28 tests (23 `test_devices.py`, 1 migration, 4 auto-generated `test_static.py` checks for the new JS files). 28 new tests = 23 (`test_devices.py`) + 1 migration + 4 (`test_static.py` automatically covers the two new JS files). |
| New Flask tests | `tests/test_devices.py` (23: auth, CSRF, validation, token never returned, rotation, one token = one row, ownership, logout cleanup, bound, switch, page script order, CSP/Permissions-Policy, cookie flags, native payload) + 1 migration test (data survives, constraint, downgrade/upgrade round trip) |
| JS (jsdom) | **33/33** (20 existing calling tests unchanged + 13 new in `platform.test.js`), run 3 times |
| Browser `push_state` / `sw_ack` | 16/16 and 10/10 |
| Go (`go vet`, `go test -race`), C/C++ (ASan/UBSan), Rust | all pass (unchanged code) |
| End-to-end (real Go binary + modified Flask) | 4/4 |
Not executed: Docker build, real Android/WebView/device, real FCM, live PythonAnywhere/Render, real two-device call.
