# Audit notes (November 2026)

## CPU / polling
- Open chat: 2.5 s while active, easing to 5 s after 30 s / 2 min of quiet (`POLL_INTERVAL_CHAT_MAX_MS`); back to fast on any message, typing or tap.
- Chat list: 5 s (`POLL_INTERVAL_LIST_MS`), slower (>=12 s) after six unchanged answers.
- Hidden tab: **no** polling at all (chat, list, counters); in-flight requests are cancelled; on return everything syncs at once. Phone unlock / back-forward cache / network back also trigger an immediate sync.
- One timer and one request at a time per feature; failures back off exponentially; 401/404 stop the loop.
- `/api/chats`: 2 grouped queries instead of 2 per chat; `mark_all_delivered` is a read unless something is waiting. `mark_seen`/`mark_delivered` write only when something changed. The seen call replaces the delivered call while the chat is visible.
- Typing: stop-typing is sent only if typing was sent; sent with `keepalive` on hide/leave.
- Presence: database written at most every 30 s (was 10 s); online timeout 75 s and never below write interval + 15 s. Uses a targeted UPDATE (no `updated_at` change).
- Message reliability: cursors unchanged (`after` id + server-time `since`); `poll` returns `more` when a full page of 100 came back and the browser asks again at once. Messages are keyed by client uuid, so nothing is duplicated.

## Fixed
- `.env.example` was missing **and** listed in `.gitignore` (now un-ignored; `instance/` and `*.db` ignored).
- Service worker: it was registered from an inline script that the CSP blocks, and lived under `/static/js/` (scope too small) while precaching `/` with cookies. Now served from `/sw.js`, caches only public static files, never pages or API; old caches/worker are removed; logout clears caches. Authenticated pages/API send `Cache-Control: no-store`.
- Uploads: streamed to Cloudinary in 64 KB pieces (was `stream.read()` + a second copy). File names in Bengali/Unicode no longer lose their extension (was rejected as "bad type"). Text/CSV must be real text; HTML/SVG/EXE/script content types are refused; stored MIME is derived from the name, not the client.
- Email: provider switch (Gmail SMTP or HTTPS APIs), timeouts, one safe retry before the message is handed over, generic error to the person.
- Sessions: new session at login (fixation), cleared at logout, password change/reset signs out all other sessions, rate-limit keys are per account when signed in, per IP otherwise (with `ProxyFix` so the IP is the real one), per-account login limit.
- API: the other person's account must be active to read/send; rate limits on poll, typing, seen/delivered, delete, uploads, message requests, reports, profile edits.
- Database: index on `message_deletions.user_id` (used by every history/unread query) and OTP lookup; `busy_timeout` for SQLite. WAL mode was deliberately **not** enabled (unsafe on PythonAnywhere's network file system).

## Checked and already fine
CSRF (global `CSRFProtect`, token on every form and in JS), membership checks on every conversation/message route, delete-for-everyone is sender-only and blanks body + removes attachments + deletes the Cloudinary asset, OTP is HMAC-hashed / expiring / single-use / attempt- and send-limited, password hashing via Werkzeug, open-redirect guard, strict CSP.

## Not changed on purpose
Media URLs are public but unguessable Cloudinary URLs (changing that would need signed URLs / a different storage design). No backups, Redis, Firebase or paid services were added.
