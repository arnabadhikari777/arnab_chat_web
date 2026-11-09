# Architecture

Flask application factory, one blueprint per feature, business rules in `services/`, all settings in `.env`.

```
app/
  config.py            every setting, read from .env (sizes, extensions, folders, OTP rules ...)
  extensions.py        db, migrate, login, csrf, limiter (+ SQLite foreign keys ON)
  models/              user, chat (conversation, message, attachment, deletion), social (request, notification), system (otp, announcement, report, audit)
  blueprints/          main, auth, people, chat (pages + JSON api), notices, settings, admin
  services/            auth, otp, email, upload_validator, cloudinary, message, conversation,
                       message_request, notification, presence, profile, people, report, admin
  realtime/            base.py (interface), polling.py (today)   <- swap here for WebSockets
  middleware/guards.py auth_required, admin_required, safe_next
  i18n/                en.json, bn.json, t()
  templates/, static/{css,js}
migrations/            Alembic (batch mode, safe on SQLite)
tests/                 pytest (359 tests) + tests/browser (jsdom, runs the real JavaScript)
```

## Database (SQLite now, any SQL database later)
| Table | Purpose / key rules |
|---|---|
| users | unique email + username (lowercase, username locked by a model validator), role, status (active/suspended/banned), is_public, notification switches, last_seen_at |
| otp_verifications | hashed code, expiry, attempts, used_at, purpose (register / reset_password / change_password) |
| conversations | one row per pair: `user_low_id < user_high_id` + unique pair ⇒ no duplicates |
| messages | `unique(sender_id, client_uuid)` (no duplicates on retry), status sent→delivered→seen, delete-for-everyone flag |
| message_attachments | Cloudinary id/url/type/size/duration |
| message_deletions | "delete for me" (per user) |
| message_requests | partial unique index: one *pending* request per sender→receiver; rejected ones don't block a new request |
| notifications | `unique(user_id, dedupe_key)`; JSON payload with names/ids only |
| announcements, reports, admin_audit_logs | admin features |

Indexes: `messages(conversation_id,id)`, `messages(conversation_id,sender_id,status)`, `notifications(user_id,is_read,created_at)`, plus FKs/uniques above. Foreign keys cascade where it is safe.

## Who decides what (security)
- **Every** check is on the server: conversation membership (404 for outsiders), delete-for-everyone = sender only (403), admin routes (`before_request` on the whole admin blueprint), private-account rule (`start_conversation`), upload type/size/signature.
- Passwords hashed (Werkzeug). OTP: HMAC-hashed, 10 min, single use, 5 attempts, 60 s resend wait, 5 sends/hour.
- CSRF on all forms and API calls (`X-CSRFToken`), CSP without inline script/style, `textContent` for all user text, secure cookies in production.
- Emails are shown only to admins. Admins cannot read chat text (no such page or endpoint).
- Rate limits on login, register, OTP, reset, send-message.

## Message status (server is the only authority)
- `sent`: saved. The sender's HTTP response never changes this.
- `delivered`: the **recipient's** app asked for its chat list (`/api/chats`) or acknowledged the message from an open chat (`/delivered`).
- `seen`: the recipient's chat screen is visible and called `/seen`.
- Status only moves forward. The sender learns about changes from `/poll` (`updates` since the last `server_time`).

## Real-time layer
`realtime/base.py::RealtimeBackend` has `publish`, `set_typing`, `is_typing`. `PollingBackend` implements typing in memory; `publish` is a no-op because browsers poll. To add Socket.IO: implement the same class, authorise `join` by `Conversation.has_member`, call `publish("message"|"deleted", conversation_id, payload)` (already called from `message_service`), and emit status changes from `mark_*`. Pages keep working with polling as a fallback.

## Postponed features (hooks already in place)
Reply/forward/edit/pin/reactions: add columns or tables referencing `messages.id`. Block: new table checked in `start_conversation`/`send_*`. Groups: `conversations` + a participants table. Multi-device: session table + revoke. E2EE: attachments and bodies are already isolated in the service layer. Calls: a new blueprint plus the Socket.IO backend.

## Performance review (honest)
- Chat list does 2 small queries per conversation (last message, unread count) for at most 100 chats — fine for a small group; replace with one grouped query if lists grow.
- Polling every 2.5 s per open chat, 12 s on other pages; presence writes are throttled to one per 10 s per user.
- All queries use the indexes above. SQLite handles this load; move to PostgreSQL for many concurrent writers.
