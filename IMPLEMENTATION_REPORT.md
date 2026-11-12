# Goppo – Implementation Report (Phases 1, 2 and 3)

**Phase 1:** verified push delivery acknowledgement, notification auto-read, per-user Clear All.
**Phase 2:** website-wide settings (maintenance mode and feature switches) in the Admin Panel.
**Phase 3:** database tools (health, backup, verify, download, safe clean-up, CLI restore), security page and sign-in log, device revoke, timed suspensions, announcement edit/delete, dashboard push numbers, Bengali beginner guide `DEPLOYMENT_GUIDE.md`.
**Not done (listed honestly):** deleting a user account, announcements to selected users, a separate reported-message moderation page, web-based restore (deliberate), push-acknowledgement failures stored in the database (they go to the server log on purpose: an unauthenticated endpoint must not be able to fill the database).

## READ THIS FIRST – what was and was not tested
The environment this was built in had **no internet**, so Flask-SQLAlchemy, Flask-Login, Flask-Migrate, Alembic, pywebpush and pytest could not be installed. Therefore:

| Check | Result |
|---|---|
| `node tests/browser/sw_ack.test.js` (runs the real `sw.js` in a sandbox) | **9/9 passed** |
| New/changed Jinja templates rendered with stub data (settings, unavailable, dashboard, database, security, announcement edit/list) | rendered |
| SQLite backup → integrity check → restore → name-validation logic, run against real files | worked (good file ok, junk file rejected, restore returned the old data, `../` names refused) |
| `python -m py_compile` on every changed Python file | passed (syntax only) |
| `node --check app/static/js/sw.js`, JSON validity of `en.json`/`bn.json` | passed |
| `pytest` (existing + new tests) | **NOT RUN** |
| `flask db upgrade` on a fresh DB / a copy of your live DB | **NOT RUN** |
| Real phone push test | **NOT RUN** |

New Python tests (`tests/test_delivery_ack.py`, `tests/test_notice_clear.py`, `tests/test_site_settings.py`, `tests/test_phase3.py`) were written but never executed, so they may contain mistakes. Run everything yourself (commands below) before deploying. **Back up `instance/chat.db` first.**

## 1. Verified delivery (Sent → Delivered)
* A message becomes **Delivered** only after the recipient's **service worker** posts a valid acknowledgement. A push provider "accepted" answer, a `PushDelivery` row, or opening the app are **not** proof.
* Every push send creates/refreshes ONE `push_deliveries` row per notice+device (unique pair, unchanged) and gets its own random single-use token. Only the SHA-256 hash is stored. Token lifetime = `PUSH_TTL_SECONDS + PUSH_ACK_EXTRA_SECONDS` (default 24 h + 5 min), because a phone that was off receives the push late.
* `POST /api/push/ack` (no session, CSRF-exempt – the token is the proof, cookies are not sent). Checks: delivery exists; token matches (constant time), unused, unexpired; the device endpoint equals the subscription the push went to (still active, same owner); notice exists and belongs to the same user; account active. The token is consumed with one conditional `UPDATE`, so simultaneous duplicates cannot both win. Every failure returns the same `400 {"ok": false}`; reasons go to the server log only (never the token).
* **Online rule:** at ack time, if the recipient is online by the existing presence system, the messages are marked Delivered immediately. If not, the receipt is stored and applied by `apply_pending_acks()` on the person's next authenticated `/api/summary` or `/api/chats` call (one indexed SELECT; a write only when something is pending).
* An ack covers only messages that existed when that push was sent (`message_upto_id`). Status only moves forward (`sent → delivered`), so duplicates and `seen` are never disturbed.
* **Multiple devices:** one row per device; any one valid ack can deliver; extra acks change nothing.
* **Foreground:** the worker tells the open page and then acknowledges; if telling the page throws, or `showNotification` fails, **no** ack is sent.
* Grouping is unchanged (one notice per chat, push `Topic` replaces queued pushes, count increases). A re-send for new activity issues a new token, so an older token can no longer be replayed.
* `PUSH_ACK_EXTRA_SECONDS` added to config and `.env.example`.

### Decisions you should confirm
1. **Removed** the old behaviour in `/api/chats` that marked every waiting message Delivered when the app opened (your stated problem). Two existing tests were changed to match (`test_hardening.py`, `test_messaging.py`).
2. **Kept** `POST /api/conversations/<id>/delivered`: the open chat page reports the exact messages it just rendered. That is real device receipt, and without it people with notifications switched off could never get a double tick. If you want strictly push-acknowledged only, remove that call from `chat.js`; say so and I will do it.
3. A person with **no registered push device** therefore gets Delivered only via decision 2 (or Seen).

### Limitations
* Clear All deletes the notice's push records, so a receipt that had not been applied yet is lost (rare).
* Pushes cannot be acknowledged on iOS unless the PWA is installed to the Home Screen (platform rule).
* Browsers that drop a push without running the worker (e.g. battery-saver kills) never acknowledge; the message stays at one tick. That is the intended safe outcome.
* Delivery proves device/browser receipt, never that a human saw it.

## 2. Notifications
* Opening the Notice Box calls `POST /api/notices/read-all` (one `UPDATE … WHERE user_id = me AND is_read = 0`) and updates the bell badge immediately. The old per-id `mark-displayed` endpoint and the `/notices/read-all` form route are kept for compatibility (there was no visible duplicate "Mark all" button in the templates).
* **Clear All** button (only when there is something to clear) → confirmation dialog → `POST /api/notices/clear-all`. It takes **no ids or user parameter**; the query is `DELETE FROM notifications WHERE user_id = <signed-in user>`. Login + CSRF + 10/min rate limit. Rows are really deleted; their push-delivery rows go via `ON DELETE CASCADE`; registered devices stay.
* New en/bn strings: `notice.clear_all`, `clear_title`, `clear_text`, `clear_confirm`, `cleared`. **Please have the Bengali wording checked.**

## 3. Database migration
`migrations/versions/c7e1a5d3f2b8_push_delivery_ack.py` – additive only: six nullable columns and one index on `push_deliveries`. No data is touched. Existing rows keep NULLs.

```bash
cp instance/chat.db instance/chat.db.backup-$(date +%F)
flask db upgrade            # applies c7e1a5d3f2b8
# rollback if needed:
flask db downgrade a4f8c2e1b9d0
```

## 4. Files changed
`app/models/push.py`, `app/config.py`, `app/services/push_service.py`, `app/services/notification_service.py`, `app/services/message_service.py` (removed `mark_all_delivered`), `app/blueprints/chat/api.py`, `app/templates/notices/index.html`, `app/static/js/sw.js` (cache `goppo-v6`), `app/static/css/components.css`, `app/i18n/en.json`, `app/i18n/bn.json`, `.env.example`, `migrations/versions/c7e1a5d3f2b8_push_delivery_ack.py`, tests (`test_delivery_ack.py`, `test_notice_clear.py`, `browser/sw_ack.test.js` new; `test_hardening.py`, `test_messaging.py` edited).

## 5. How to verify (do this before going live)
```bash
pip install -r requirements.txt
python -m pytest -q                      # expect all green; report any failure to me
node tests/browser/sw_ack.test.js        # 9/9
cd tests/browser && npm install && ./run.sh   # existing browser tests
```
**Real phone test:** install the PWA, enable notifications in Settings, log in as user B on the phone and keep the app closed. From user A send a message → A shows one tick. Open B's phone / bring it online → once the notification arrives, A's tick becomes double within a few seconds. Then switch the phone to airplane mode, send another message: it must stay one tick until the phone reconnects and receives the push. Check `PushDelivery.acked_at` in the DB (or the server log lines `push ack rejected`) if it does not change.

## 7. Phase 2 – Website settings (Admin Panel > Settings)
New admin page `/admin/settings` (collapsible groups, mobile friendly, confirmation before turning maintenance ON, submit button locks after one click, CSRF, 30 saves/hour). New table `site_settings` (migration `d2a9b4e6f1c7`, additive; rollback `flask db downgrade c7e1a5d3f2b8`). An empty table = everything open, so nothing changes until an admin saves.

| Switch | What it does (admins are never blocked) |
|---|---|
| Maintenance mode (+ optional message) | Everyone else gets a 503 maintenance page (JSON error for `/api/`). Sign-in, forgot/reset password, logout, `/health`, `/sw.js`, language and translations stay reachable, so an admin can always sign in. |
| New sign-ups | Closes `/register` (page and POST). |
| Sign in | Non-admins cannot sign in (also the Math Class SSO link). People already signed in stay signed in. |
| Chat | Blocks all chat pages/APIs and starting a chat. Badge counters, the Notice Box and push set-up keep working. |
| Message requests | Blocks sending and accepting requests. |
| Sending new messages | Blocks `POST …/messages`; reading history still works. |
| Push notifications | No push is sent or retried, devices cannot register, config reports "not configured". In-site notices still work. (The browser says "server not configured" in this state.) |
| Announcements | New announcements cannot be sent; announcement pages close. |
| Website name / public contact | Name appears in page titles and headers; empty = default `APP_NAME`. (Email subjects still use `APP_NAME`.) Contact is stored for later use only. |

Every change is written to the audit log with old and new value (no secrets involved). Only the listed keys can be written (mass-assignment safe). Settings are read with one tiny SELECT per request.

**Already true and now covered by tests:** a suspended/banned person loses access on the next request (the user loader refuses inactive accounts); an admin cannot suspend themselves or another admin, so the last admin cannot be removed this way.

**Deliberately not done in Phase 2:** temporary bans with an expiry date (needs a change to the sign-in path, so it belongs with the Phase 3 user-management work).

Files: `app/models/system.py` (SiteSetting), `app/services/settings_service.py`, `app/middleware/site_gate.py`, `app/__init__.py`, `app/i18n/__init__.py`, `app/blueprints/admin/routes.py`, `app/blueprints/auth/routes.py`, `app/services/{admin_service,push_service}.py`, `app/blueprints/chat/api.py`, templates `admin/settings.html`, `errors/unavailable.html`, `admin/layout.html`, `admin/dashboard.html`, en/bn strings (please have the Bengali checked), migration `d2a9b4e6f1c7`, `tests/test_site_settings.py`.

Migration commands (both phases): `cp instance/chat.db instance/chat.db.backup-$(date +%F) && flask db upgrade`

## 8. Phase 3
Migration `e5b3c8a1d7f4` (additive: table `security_events`, column `users.status_until`; rollback `flask db downgrade d2a9b4e6f1c7`, back up first because SQLite rebuilds `users` to drop a column).

* **Database page** (`/admin/database`): counts, file size, `PRAGMA quick_check`, create backup (SQLite online-backup API, file mode 600, `instance/backups/goppo-YYYYMMDD-HHMMSS.db`), integrity check, download (audited, `no-store`), delete. Backup names must match a strict pattern and live inside the backup folder (no path tricks). Works only for a SQLite file; the in-memory test DB shows a notice. **Clean-up** can delete only read notices older than 30+ days or old sign-in log lines. No SQL console, nothing deletes users or messages.
* **Restore** is CLI only: `python -m flask --app run.py restore-db <file>` (verifies the file, asks you to type RESTORE, makes a `-pre-restore` safety copy first).
* **Security page** (`/admin/security`): recent sign-ins, failed sign-ins (typed username + IP, never the password), failed/expired push deliveries (status and tries only), recent admin actions, push numbers. Sign-in events are written by the login and SSO routes; a logging failure cannot break sign-in. Rate limits already bound the writes.
* **Timed suspension/ban:** optional "number of days" (1-3650). The account turns active again at the next sign-in or request (`release_if_expired`, audited as `user.status_expired`). Blank = until an admin changes it.
* **User page:** list of the person's push devices and "Remove device" (the device must belong to the user in the URL, so ids of other people's devices do nothing).
* **Announcements:** edit (does not resend) and delete (also removes everyone's notice for it).
* **Dashboard:** notices total/unread, active push devices, device-confirmed pushes, failed pushes, accepted-but-unconfirmed after 1 h. Global counts cached 60 s (no per-person data in the cache).
* `tests/test_admin.py` URL filler updated for the new route placeholders.
* New strings en/bn (Bengali needs a native check).

Files: `app/services/{db_admin_service,security_service,admin_service,auth_service}.py`, `app/blueprints/{admin/routes,auth/routes}.py`, `app/models/{system,user}.py`, `app/cli.py`, `app/__init__.py`, templates `admin/{database,security,announcement_edit,announcements,user,dashboard,layout}.html`, i18n, migration `e5b3c8a1d7f4`, `tests/test_phase3.py`, `DEPLOYMENT_GUIDE.md`.

## 6. Remaining ideas (not started)
Account deletion with safe cascade, announcements to selected users, reported-message moderation page with evidence view, optional web restore.
