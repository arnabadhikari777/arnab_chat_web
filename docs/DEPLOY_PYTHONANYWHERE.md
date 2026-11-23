# Deploy the Flask app to PythonAnywhere

Needs: a PythonAnywhere account (the free plan works for the chat; see "Free plan notes"), HTTPS (automatic on `*.pythonanywhere.com`), and the Render calling server already deployed (`DEPLOY_RENDER.md`) so you know its URL.
**Back up first** if you already have a live database (step 0).

## 0. Back up (existing site)
```bash
cd ~/arnab_chat_web && python tools/db_backup.py            # verified copy in backups/
```
Also download `instance/chat.db` from the Files tab. Never upgrade production without a backup.

## 1. Upload
Upload the contents of the ZIP folder **`goppo-full/chat-web/`** (it contains `wsgi.py`, `run.py`, `app/`, `migrations/`, `requirements.txt`) to
`/home/YOURUSERNAME/arnab_chat_web/`. Do **not** upload `calling-server/`, `audio-c/`, `media-engine/`, `secure-media-rs/` (those are for Render).
When updating an existing site, keep your `instance/chat.db` and `.env`; overwrite the rest.

## 2. Python and virtualenv (Python 3.10–3.12; the project was tested on 3.12)
```bash
cd ~/arnab_chat_web
mkvirtualenv --python=/usr/bin/python3.12 goppo-env        # or 3.10/3.11 if 3.12 is not offered
pip install -r requirements.txt
```
Web tab → *Virtualenv*: `/home/YOURUSERNAME/.virtualenvs/goppo-env`.

## 3. Environment file
Copy `chat-web/.env.example` to **`~/arnab_chat_web/.env`** (same folder as `wsgi.py`) and fill it in. Minimum for production:

| Variable | Value |
|---|---|
| `APP_ENV` | `production` |
| `SECRET_KEY` | long random string |
| `DATABASE_URL` | `sqlite:////home/YOURUSERNAME/arnab_chat_web/instance/chat.db` (4 slashes) |
| `TRUSTED_PROXY_COUNT` | `1` |
| `SESSION_COOKIE_SECURE` | `1` |
| `ADMIN_EMAILS` | your admin e-mail(s) |
| `MAIL_*` | your mail provider (existing OTP setup, unchanged) |
| `CLOUDINARY_*` | existing Cloudinary credentials (unchanged) |
| `VAPID_*` | existing push keys (unchanged) |
| **`CALLING_SERVER_URL`** | `https://YOUR-SERVICE.onrender.com` (no trailing slash) |
| **`CALLING_HMAC_SECRET`** | the **same** ≥32-char secret you set on Render |
| `CALLING_TOKEN_TTL_SECONDS` / `CALLING_RING_SECONDS` / `CALLING_MAX_CALL_SECONDS` / `CALLING_WAKE_TIMEOUT_MS` / `CALLING_HEALTH_POLL_MS` | optional; defaults 120 / 60 / 14400 / 90000 / 2000 |

Every variable the app reads is listed in `.env.example` (a test enforces this). Variables that exist only on Render are **not** here (`ALLOWED_ORIGINS`, `TURN_*`, `STUN_URLS`, `REQUIRE_NATIVE`, …).

## 4. Database migration (one valid head: `c3d5e7f9a1b2`)
```bash
cd ~/arnab_chat_web && workon goppo-env
export FLASK_APP=run.py
flask db heads        # must print exactly one line:  c3d5e7f9a1b2 (head)
flask db upgrade      # fresh DB: creates everything; existing DB: only adds what is missing (additive, no data loss)
flask db current      # c3d5e7f9a1b2 (head)
python tools/db_inspect.py     # read-only check: revision, tables, row counts
```
Chain: `768997e88532 → 1b0f7999dbc9 → 9c1d2e3f4a5b → a4f8c2e1b9d0 → b7e3d1a2c4f6 → c7e1a5d3f2b8 → c9a1e5f7b2d3 → d2a9b4e6f1c7 → d3b8f2a6c1e4 → e5b3c8a1d7f4 → f1a2b3c4d5e6 → a9f0c3d7b1e2 → b2c4e6f8a1d3 → c3d5e7f9a1b2`.
Existing users, messages and call records are preserved (tested on a copy of a pre-calling database with a call record).

## 5. WSGI file (Web tab → WSGI configuration file) — replace everything with:
```python
import sys
path = "/home/YOURUSERNAME/arnab_chat_web"
if path not in sys.path:
    sys.path.insert(0, path)
from wsgi import application
```
(`wsgi.py` loads `.env`, builds the app with `create_app()`, registers all blueprints including `calling`.)

## 6. Static files (Web tab → Static files)
| URL | Directory |
|---|---|
| `/static/` | `/home/YOURUSERNAME/arnab_chat_web/app/static` |

The service worker is served by Flask at **`/sw.js`** (root scope, `Cache-Control: no-cache`); do not map it as a static file. Force HTTPS on the Web tab.

## 7. Reload and verify
Click **Reload**, then:
1. Log in → open a chat → you see the phone and camera icons in the header.
2. `https://YOURNAME.pythonanywhere.com/api/calling/config` (logged in) → `"enabled": true`.
3. Browser dev tools → Console: no CSP errors. Application → Service Workers: `sw.js` active.
4. Open `https://YOUR-SERVICE.onrender.com/ready` and `/components` (should list C, C++, Rust as verified).
5. Place a test call between two accounts (two devices, see `DEPLOYMENT_CHECKLIST.md`).

## Free plan notes
* Free web apps run one worker: each logged-in user polls `/api/calling/incoming` (every 3 s visible, 10 s hidden; limit 60/min). Fine for a small group; use a paid plan for more.
* Outbound requests from PythonAnywhere are not needed for calling (the **browser** talks to Render). Mail/Cloudinary/push keep their existing restrictions.

## Troubleshooting
| Symptom | Cause / fix |
|---|---|
| No call icons | `CALLING_SERVER_URL`/`CALLING_HMAC_SECRET` empty (config endpoint says `enabled:false`); reload after editing `.env` |
| Console: "Refused to connect … Content Security Policy" | `CALLING_SERVER_URL` wrong or has a path; fix and Reload |
| "Permission denied" for mic/camera | site not on https, or blocked in browser settings; the app sends `Permissions-Policy: camera=(self), microphone=(self)` |
| 503 "Calling is not configured" | `CALLING_HMAC_SECRET` shorter than 32 chars |
| `flask db upgrade` error | restore the backup, run `python tools/db_inspect.py`, see `chat-web/deployment/07-troubleshooting-and-rollback.md` |
| Incoming call never rings when the tab is closed | push not enabled for that user/browser (Settings → notifications); iOS needs the site added to the Home Screen |
