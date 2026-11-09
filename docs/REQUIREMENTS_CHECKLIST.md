# Requirements checklist (your 30 sections) — honest status

✅ built and tested · ⚠️ built, with a limit you should know · ❌ not built (and why)

| # | Requirement | Status |
|---|---|---|
| 1 | Flask, HTML/CSS/vanilla JS, SQLite, sessions, Werkzeug hashing, Gmail OTP, Cloudinary, `.env`, modular | ✅ (Flask-SocketIO → see 8/24) |
| 2 | Math Class reference project | ⚠️ The ZIP was never provided, so the `.env`/Cloudinary pattern is my own design (everything configurable in `.env`). Nothing copied. |
| 3 | Register (name, email, unique username, password, photo), OTP mandatory, login/logout, forgot password by OTP, OTP expiry/one-use/cooldown/rate limit/attempt limit/hashed, no account deletion, no multi-device | ✅ |
| 4 | Public/Private, message requests (no preview), accept/reject, notice to sender, re-request after reject, no duplicate pending | ✅ |
| 5 | People page (all accounts, no bio on cards, optional username search), profile view, Message / Send Request, no follow | ✅ |
| 6 | Profile edit (name, bio, photos; username locked), Cloudinary, validation, no secrets in JS | ✅ |
| 7 | All upload sizes, extensions, folders in `.env`, documented in `.env.example` | ✅ Plan limits not assumed — `flask check-services` tests your keys; check your plan's limits yourself. |
| 8 | Private 1-to-1 chat, persistent history, no duplicates | ✅ Real-time = polling (see 24). Flask-SocketIO ❌ not built: it cannot run on PythonAnywhere Free and could not be tested here. |
| 9 | Text, emoji, image, video, PDF, documents, other files, voice (record, pause, delete, preview, send, playback) | ✅ ⚠️ Voice was tested with a *simulated* recorder in jsdom. **Test once on your real phones** (Android Chrome → webm, iPhone Safari → m4a). HTTPS is required for the microphone. |
| 10 | Sent / Delivered / Seen decided by the server | ✅ |
| 11 | Delete for me / for everyone, enforced on the backend | ✅ |
| 12 | Postponed features not built, architecture ready | ✅ (docs/ARCHITECTURE.md) |
| 13 | Online, offline, last seen, typing, seen; recovers from disconnect/refresh | ✅ 2.5–12 s delay because of polling. Typing state is in memory (1 worker). |
| 14 | Notice Box: read/unread, time, click-through, mark all read, history, no duplicates, no request content; announcements, system notices | ✅ |
| 15 | Chat list (photo, name, last message, time, unread, online dot), sorted by activity | ✅ |
| 16 | No in-chat message search | ✅ (none) |
| 17 | Settings: edit profile, change password with OTP, privacy, language EN/BN, notification settings | ✅ |
| 18 | Central EN/BN translation system, simple English | ✅ (en.json / bn.json have identical keys — tested) |
| 19 | Admin panel: dashboard, users, user info, account status, announcements, reports, statistics; backend-protected; first admin from `.env` | ✅ Admins cannot read chat text. |
| 20 | Dashboard structure | ✅ Profile overview, Messages, People, Notice Box, Settings, Logout |
| 21 | Mobile-first, light only, design system, loading/empty/error states, confirmations, touch targets | ✅ ⚠️ Layout checked in code and jsdom only — **not on real devices or screens**. |
| 22 | Security list | ✅ see docs/ARCHITECTURE.md. Rate-limit counters are in memory (reset on restart). |
| 23 | Schema, constraints, indexes, migrations, no duplicate conversations | ✅ (two Alembic migrations, tested on a database that already had users) |
| 24 | PythonAnywhere Free assessment + fallback | ✅ docs/DEPLOY_PYTHONANYWHERE.md — Cloudinary reachability on the free allow-list is **unverified** until you run `flask check-services` there. |
| 25 | Project structure | ✅ |
| 26 | Error handling, friendly messages, server logs | ✅ |
| 27 | Tests | ✅ 359 pytest tests + 55 browser checks (tests/browser). Every item in your list is covered. Real Gmail, real Cloudinary and real phones are the only untested parts. |
| 28 | Phased workflow | ✅ (all phases delivered) |
| 29 | Code quality rules | ✅ no TODOs, no dead buttons |
| 30 | Final expectation | ✅ except the items marked ⚠️/❌ above |

## Known limits
- Polling, not push (typing/online/status lag by a few seconds).
- One server process assumed (typing state + rate limits in memory).
- Chat list uses 2 queries per chat (fine for small groups).
- Delete-for-everyone has no time limit.
- No "remember me", no message search, no push notifications (all outside your list).
- Browser `pagehide` does not mark someone offline instantly: they turn offline after `PRESENCE_TIMEOUT_SECONDS` (30 s).
