# Goppo – Implementation Report (Phase 1 of 3)

**Scope of this delivery:** verified push delivery acknowledgement, notification auto-read, per-user Clear All.
**Not in this delivery:** the Admin Control Center work (site settings, maintenance mode, bans, moderation, database tools, security pages). Those are Phases 2 and 3.

## READ THIS FIRST – what was and was not tested
The environment this was built in had **no internet**, so Flask-SQLAlchemy, Flask-Login, Flask-Migrate, Alembic, pywebpush and pytest could not be installed. Therefore:

| Check | Result |
|---|---|
| `node tests/browser/sw_ack.test.js` (runs the real `sw.js` in a sandbox) | **9/9 passed** |
| `python -m py_compile` on every changed Python file | passed (syntax only) |
| `node --check app/static/js/sw.js`, JSON validity of `en.json`/`bn.json` | passed |
| `pytest` (existing + new tests) | **NOT RUN** |
| `flask db upgrade` on a fresh DB / a copy of your live DB | **NOT RUN** |
| Real phone push test | **NOT RUN** |

New Python tests (`tests/test_delivery_ack.py`, `tests/test_notice_clear.py`) were written but never executed, so they may contain mistakes. Run everything yourself (commands below) before deploying. **Back up `instance/chat.db` first.**

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

## 6. Still to do (not started)
Phase 2: site settings (maintenance, registration, login, chat, requests, sending, push toggles), ban/suspend enforcement with expiry, admin audit expansion, last-admin protection. Phase 3: user/device management pages, moderation of reported content, announcement edit/delete/targeting, security monitoring, dashboard caching, database health/backup/restore, `DEPLOYMENT_GUIDE.md` and README updates.
