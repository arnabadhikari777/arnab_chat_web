# Goppo – Code repair report

Date: 2026-11-17. Project: `arnab_chat_web-main.zip` → repaired `arnab_chat_web_FIXED.zip`.
Read `FINAL_TEST_REPORT.md` together with this file: it says exactly what was executed and what was not.

## 1. Cause of the outage (what the evidence supports)

* The migration history had **two heads** branching from `a4f8c2e1b9d0`:
  branch A `b7e3 → c9a1 → d3b8 → f1a2` and branch B `c7e1 → d2a9 → e5b3`.
* The models and services implement **branch A** (`users.suspended_until`, `users.session_epoch`, `users.admin_role`,
  `push_deliveries.acked_at`, `site_settings`, `moderation_notes`, `security_events`). Branch B added `users.status_until`
  and different push columns that **no application code uses**, and **never** adds `suspended_until`, `session_epoch`,
  `admin_role` or `moderation_notes`.
* A database that was upgraded along branch B (or stopped early) therefore lacks columns that `db.session.get(User, …)` selects.
  `_load_user()` runs on every authenticated request, SQLite answers `no such column`, and the request ends in
  "Unhandled Exception". **This fits every piece of evidence in the project and in your description, but it is not confirmed
  against your live database** (I cannot see it). `python tools/db_inspect.py` on the server confirms or rules it out in seconds.
* `app/__init__.py::_load_user()` is **correct and was not changed** (no exception hiding, no auth bypass).
* The old `RECOVERY_REPORT.md` claimed three branch-B files were deleted. They were all still in the ZIP. Corrected in that file.

## 2. Modified files

| File path | Original problem | Correction | Reason | Test |
|---|---|---|---|---|
| `migrations/versions/b7e3d1a2c4f6_push_delivery_ack.py` | Unguarded `add_column("acked_at")`; parallel duplicate of `c7e1` | Guarded (skips if exists); plain `ADD COLUMN` | Safe on any DB state; no table rebuild | Static: graph + AST column comparison. **Alembic run: not executed**; covered by `tests/test_migrations.py` (not run) |
| `migrations/versions/c7e1a5d3f2b8_push_delivery_ack.py` | Second branch from `a4f8`; added 6 columns + index that no code uses | Now child of `b7e3`; only guarantees `acked_at`; legacy columns, if present, are left alone | One history; schema equals models | same |
| `migrations/versions/c9a1e5f7b2d3_admin_control_center.py` | Re-parented; created `site_settings` unguarded; `batch_alter_table("users")` rebuilt the users table | Child of `c7e1`; every create/add guarded; plain `ADD COLUMN`; downgrade never drops `site_settings` | Duplicate-table failure; avoid copying user rows | same |
| `migrations/versions/d2a9b4e6f1c7_site_settings.py` | Duplicate `site_settings` creation in other branch | Child of `c9a1`; only ensures the table exists; downgrade is a no-op | Duplicate table creation removed | same |
| `migrations/versions/d3b8f2a6c1e4_staff_levels.py` | Child of `c9a1` | Child of `d2a9`; guarded `admin_role` | Linear chain | same |
| `migrations/versions/e5b3c8a1d7f4_phase3_security_and_timed_status.py` | Branch B head; created `security_events`; added unused `status_until` | Child of `d3b8`; guarded `security_events` + index; **does not add `status_until`**; downgrade no-op | `suspended_until` is the real timed-suspension column | same |
| `migrations/versions/f1a2b3c4d5e6_security_events.py` | Duplicate `security_events` creation | Child of `e5b3`; guarded | Duplicate removed | same |
| `app/config.py` | Relative `sqlite:///…` `DATABASE_URL` resolves against the working directory (opens an empty DB on PythonAnywhere); no push-endpoint settings | `_absolute_sqlite_url()`; `PUSH_ENDPOINT_EXTRA_HOSTS`, `PUSH_ENDPOINT_CHECK_DNS`; test config disables DNS check | Silent wrong-database hazard; SSRF settings | Helper executed on 7 URL forms (pass) |
| `app/services/site_settings.py` | `chat_enabled` and `sending_enabled` were aliases of `messaging_enabled` | `chat_enabled` is an independent canonical switch; `sending_enabled` stays an alias of `messaging_enabled` | Separate "new messages" from "whole chat" | Static; tests `test_admin_control.py`, `test_site_settings.py` (not run) |
| `app/middleware/site_gate.py` | `messaging_enabled=False` blocked **every** `chat.*` endpoint: history, polling, read receipts | `chat_enabled` gates the chat area; `messaging_enabled` gates only `POST chat.send` (503 `site.sending_closed`) | Old messages must stay readable | same |
| `app/services/push_service.py` | Only `startswith("https://")` + length check on the push URL; server posts to it | `push_endpoint.check_endpoint()` at subscribe and again before each send; blocked rows become `expired` and are never contacted; redirects disabled when installed pywebpush supports `requests_session`; generic exception → `failed` | SSRF | Validator: 30 parametrized + 22 ad-hoc cases executed with a Flask stub (pass). Full pytest: not run |
| `app/blueprints/admin/routes.py` | No routes for `database`, `database_backup/download/verify/delete/cleanup`, `security`, device revoke (templates would raise `BuildError`) | Added all, with audit entries, safe backup names, no-store downloads, only two cleanup kinds, no restore/delete-database action | Admin Database/Security pages | `url_for` cross-check: no unknown endpoints. Pytest: not run |
| `app/services/admin_service.py` | No helpers for security overview / device revoke | Added `security_overview`, `push_problem_deliveries`, `recent_admin_actions`, `devices_for`, `revoke_device` (only the target's own device) | Support the pages | Static |
| `app/services/db_admin_service.py` | No security-log cleanup; docstring referenced a non-existent `flask restore-db` | Added `cleanup_security_events` (≥30 days); docstring points to `tools/db_restore.py` | Dangling reference | Static |
| `app/services/permissions.py` | New endpoints unlisted | `admin.security` → `audit.view`; `admin.user_push_revoke` → `users.moderate`; database endpoints intentionally unlisted = super admin only (default `staff.manage`) | A backup contains every password hash | Static |
| `app/templates/admin/database.html` | Inline `onsubmit="return confirm(...)"` handlers blocked by CSP | `data-confirm` (handled by existing `app.js`) | CSP stays strict | CSP template rule script (pass) |
| `app/templates/admin/layout.html` | No navigation to Security/Database | Added both | Reachability | Jinja parse |
| `app/templates/admin/user.html` | No push-device list | Added list + revoke form | Device revoke feature | Jinja parse |
| `app/templates/notices/index.html` | Inline `<script>` blocked by `script-src 'self'` → notices never marked read | Script moved to `static/js/notices.js` | CSP | CSP rule script + `node --check` (pass) |
| `app/i18n/en.json`, `bn.json` | Missing: `site.*` gate/unavailable keys, all `admin.db.*`, `admin.sec.*`, `admin.nav.database/security`, `admin.stat.push_*`, `admin.set.chat_enabled/announcements_enabled`, device keys; label of `messaging_enabled` | 58 keys added to both languages; placeholders identical; label clarified | Raw keys were shown to users | Parity + placeholder + dynamic-family checks (pass) |
| `wsgi.py` | No path setup | Inserts project dir in `sys.path`, `chdir`, exposes `application` | Reliable on PythonAnywhere | Syntax only; **start-up not executed** |
| `requirements.txt` | `pytest` in runtime requirements | Removed; new `requirements-dev.txt` | Production installs only what it runs | n/a |
| `.gitignore` | Broken patterns (`docs`, `.md`, `.db` matched nothing useful / the wrong things) | Rewritten (`.env`, `*.db`, `instance/`, `backups/`, `logs/`, caches, `*.zip`) | Secrets/data must never be committed | n/a |
| `.env.example` | No production, mail-provider or push-guard notes | Added commented placeholders only (no secrets) | Documentation | Inspected |
| `README.md` | Pointed to non-existent `docs/` guide | Points to `deployment/`, tools, one-head rule | Accuracy | n/a |
| `RECOVERY_REPORT.md` | Claimed files were removed | Correction note prepended | Accuracy | n/a |
| `tests/test_delivery_ack.py` | Tested a design (hash columns, `{id,token,endpoint}`) that **no code implements** | Rewritten for the implemented signed-token design (13 test functions) | Test and code must describe the same product | **Not executed** (pytest unavailable) |
| `tests/test_admin_control.py` | Helper posted legacy alias keys, so `messaging_enabled=False` was overridden; expected the old blocking behaviour | Helper mirrors the real form (canonical keys only); new tests: new-message switch keeps history readable; chat switch is a separate full shutdown | Match the corrected semantics | **Not executed** |
| `tests/browser/sw_ack.test.js` | 5 of 9 checks failed: harness used the abandoned `{id,token,endpoint}` payload; `sw.js` correctly uses a token string | Harness rewritten to the real contract (+1 case: token kept after 3 failed attempts) | Stale harness | **Executed: 10/10 pass** |

## 3. Newly created files

| File | Purpose |
|---|---|
| `migrations/versions/a9f0c3d7b1e2_schema_reconcile.py` | New single head. Adds any table/column the models need and the DB lacks. Additive only. |
| `app/services/push_endpoint.py` | SSRF guard: https only, no userinfo, port 443, no IP literals, allow-listed push hosts, public resolved addresses only |
| `app/static/js/notices.js` | CSP-compliant Notice Box read-marking |
| `tools/db_inspect.py`, `tools/db_backup.py`, `tools/db_restore.py` | Stdlib-only read-only inspector, verified backup, guarded restore |
| `tests/test_migrations.py` | One head; fresh DB equals models; reproduces the production crash and the repair; two-version-row state; data preserved |
| `tests/test_push_endpoint.py` | SSRF tests (validator, subscribe API, send-time recheck) |
| `tests/test_schema_consistency.py` | Keeps the inspector's expected schema equal to the models |
| `requirements-dev.txt` | `pytest` |
| `deployment/README.md` + `01…08-*.md` | Deployment, backup/restore, troubleshooting, checklists |
| `CODE_REPAIR_REPORT.md`, `FINAL_TEST_REPORT.md` | These reports |

**Removed files:** none. **Renamed files:** none. (Nothing was deleted so a server that still holds old copies can never end up with a stale mixture.)

## 4. Database migration changes (summary)

Single chain: `768997e88532 → 1b0f7999dbc9 → 9c1d2e3f4a5b → a4f8c2e1b9d0 → b7e3d1a2c4f6 → c7e1a5d3f2b8 → c9a1e5f7b2d3 → d2a9b4e6f1c7 → d3b8f2a6c1e4 → e5b3c8a1d7f4 → f1a2b3c4d5e6 → a9f0c3d7b1e2 (head)`.
All historic revision ids are kept, so a database stamped with any of them can still upgrade. Whatever revision it is stamped at, the final
step adds anything missing. No `DROP`, no data update, no `batch_alter_table` rebuild of `users`.
Leftover columns from the abandoned branch (`users.status_until`, `push_deliveries.ack_token_hash`, `ack_expires_at`, `message_upto_id`,
`acked_upto_id`, `applied_upto_id`, index `ix_push_delivery_acked`) are intentionally left in place if present: nullable, unused, harmless.

**Behaviour change to be aware of:** if an admin had saved `messaging_enabled = off` before, it used to hide the whole chat; it now only stops
new messages (history stays readable). Use the new "Chat open" switch for a full shutdown.

## 5. Which design was "correct" (ack columns and `status_until` vs `suspended_until`)

| Item | Decision | Why |
|---|---|---|
| `acked_at` | Kept | Used by `push_service.acknowledge_receipt`, `admin_service.stats`, model |
| `ack_token_hash`, `ack_expires_at`, `message_upto_id`, `acked_upto_id`, `applied_upto_id` | Not part of the schema | No model attribute or code path uses them. The implemented design is a stateless signed token. The old tests described an unimplemented design; they were rewritten, not the code |
| `suspended_until` | Kept | In the model, `is_active`, `lift_expired_suspension`, admin pages |
| `status_until` | Not part of the schema | Nothing reads or writes it |
| `session_epoch`, `admin_role` | Kept | Used by `session_fingerprint()` / `staff_level` |

## 6. Unresolved limitations and things not audited

* The Flask application was **not started** and the **pytest suite and real Alembic migrations were not run** here (see the test report).
* The root cause on your live server is **inferred**, not observed. Run `python tools/db_inspect.py` there.
* Not read line by line: `auth_service`, `otp_service`, `email_service`, `message_service` (beyond the send gate), `conversation_service`,
  `message_request_service`, `people_service`, `realtime/*`, the Math Class `auth.sso_login`, upload validation, Cloudinary service.
  A targeted scan found: one CSRF exemption (the signed-token ack endpoint), no `|safe`, no user-controlled raw SQL, no secret logging.
  The privacy model was not changed.
* `tests/test_phase3.py`, `test_static.py`, `test_site_settings.py`, `test_push.py` and others were read where relevant but not modified (except as listed);
  whether they pass is unknown until pytest runs.
* Python 3.13 compatibility of the pinned dependency ranges is unverified.
* Redirect blocking on push sends depends on the installed pywebpush exposing `requests_session`; the host allow-list and public-address check apply regardless.
* Bengali strings were written by me and should be read by a Bengali speaker.
* External services (Gmail, Cloudinary, push services, PythonAnywhere itself) were not contacted.
