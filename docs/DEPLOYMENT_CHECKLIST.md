# Deployment checklist

**A. Prepare secrets** — generate one `CALLING_HMAC_SECRET` (≥32 chars). Decide TURN (provider/coturn) — recommended.
**B. Render** (`DEPLOY_RENDER.md`): push repo → Docker web service, Dockerfile `./Dockerfile`, context `.`, Health Check `/health` → set `CALLING_HMAC_SECRET`, `ALLOWED_ORIGINS`, (`TURN_URLS`, `TURN_SHARED_SECRET`) → deploy → `curl /ready` = 200, `/components` = c, c++, rust verified.
**C. PythonAnywhere** (`DEPLOY_PYTHONANYWHERE.md`): back up DB → upload `chat-web/` → `pip install -r requirements.txt` → `.env` (`CALLING_SERVER_URL`, same `CALLING_HMAC_SECRET`) → `flask db heads` (one line `d4f6a8c0e2b3`) → `flask db upgrade` → WSGI file → static `/static/` → Reload.
**D. Smoke** — log in, header shows call icons, console has no CSP errors, `sw.js` active.

## Manual acceptance tests (not executable in the build sandbox)
| # | Test | Expected |
|---|---|---|
| A1 | Two accounts, two devices on Wi-Fi: voice call, accept | audio both ways, timer runs, quality bars show |
| A2 | Same with video; mute, camera off/on, switch camera (phone), full-screen | each works; remote side sees it |
| A3 | Receiver's browser in background / tab closed with push enabled | notification "… is calling you" → tap → incoming screen → Accept |
| A4 | Reject; caller cancels while ringing; no answer for 60 s | caller sees declined / receiver sees nothing more / both get "missed" + notice |
| A5 | Call someone who is in a call | caller sees "busy"; the busy person is not interrupted |
| B1 | Let Render sleep ≥20 min, then place a call | "Waking up…" with seconds, then connects by itself; Cancel and Retry work; never longer than 90 s |
| B2 | Same, but receiver accepts after >2 min | token refreshed automatically, connects |
| C1 | Phone on mobile data ↔ laptop on Wi-Fi, TURN configured | connects (relay candidate used) |
| C2 | Same without TURN | note result (may fail on some carriers) |
| C3 | TURN over TCP/TLS only (block UDP) | connects via `turn:…tcp` / `turns:` |
| D1 | Toggle airplane mode 5–10 s during a call | "Reconnecting…", call resumes (≤20 s), else ends cleanly |
| E1–E3 | Android Chrome, desktop Chrome/Edge, Firefox, (iOS Safari) | permissions prompts, layout, controls |
| F1 | Chat regression: send text/image/voice, delivery ticks, online status, admin panel | unchanged |
| F2 | Admin dashboard shows call statistics | counts match |
