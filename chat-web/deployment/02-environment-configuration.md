# 02 – Environment configuration (`.env`)

All settings come from the file `~/arnab_chat_web-main/.env`. **That file is never inside the ZIP and must never be
committed or shared.** If your site already works (or worked), you already have a `.env`: keep it, and only
compare it with the list below.

Create one only if it does not exist:

```bash
cd ~/arnab_chat_web-main
cp .env.example .env
chmod 600 .env
nano .env
```

`.env.example` contains only placeholders; no real secret is in it.

## Required

| Variable | Meaning | Example (fake) |
|---|---|---|
| `SECRET_KEY` | Signs sessions, CSRF tokens and push receipts. **If missing, the app refuses to start.** Keep it the same forever: changing it signs everyone out and invalidates pending delivery receipts. | `a3f1…` (64 hex chars) |
| `APP_ENV` | `production` on PythonAnywhere (secure cookies, HSTS, 1 trusted proxy). | `production` |
| `ADMIN_EMAILS` | Comma-separated e-mails that become **super admins** when they log in. | `you@gmail.com` |

Create a secret key (run once, paste the output after `SECRET_KEY=`):

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## Database

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | Optional. Default is `sqlite:///<project>/instance/chat.db`. If you set it, use an **absolute** path with four slashes: `sqlite:////home/YOUR_USERNAME/arnab_chat_web-main/instance/chat.db`. (The repaired `app/config.py` also turns a relative path into a project-relative one, so the app can no longer silently open a new empty database from the wrong folder.) |

SQLite needs no server and no password. The database is one file. **Back it up before every deployment** (guide 3).

## E-mail (registration OTP, password reset)

| Variable | Meaning |
|---|---|
| `MAIL_PROVIDER` | `smtp` (default, Gmail), or `sendgrid` / `mailgun` / `brevo` / `resend` (HTTPS APIs). |
| `MAIL_SERVER`, `MAIL_PORT` | Gmail: `smtp.gmail.com`, `587` (STARTTLS) or `465` (implicit SSL). |
| `MAIL_USERNAME` | Your Gmail address. |
| `MAIL_PASSWORD` | A Gmail **App Password** (16 characters), **not** your normal password. |
| `MAIL_FROM_NAME`, `MAIL_FROM_ADDRESS` | Sender name / address (address defaults to `MAIL_USERNAME`). |
| `MAIL_API_KEY`, `MAIL_API_DOMAIN` | Only for the API providers (`MAIL_API_DOMAIN` only for Mailgun). |

Gmail App Password: Google Account → Security → turn on 2-Step Verification → Security → **App passwords** →
create one named "Goppo". Paste the 16 characters into `MAIL_PASSWORD` (spaces are automatically removed). Do not use your normal Gmail password. Port 587 uses STARTTLS; port 465 uses SSL.

Test without sending anything:

```bash
cd ~/arnab_chat_web-main && workon goppo-venv && export FLASK_APP=run.py
flask check-services
```

Expected: `Email (Gmail SMTP): OK`. If it says FAILED, the free plan may be blocking SMTP: set
`MAIL_PROVIDER=sendgrid` (or another API provider) and `MAIL_API_KEY=...`.

## Cloudinary (profile photos, chat media)

`CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` from your Cloudinary dashboard.
The `CLOUDINARY_FOLDER_*` names are optional. `flask check-services` also tests these (`Cloudinary: OK`).
Without them, text chat works; uploads do not.

## Web Push (notifications) – all three are required together

```bash
flask generate-vapid-keys          # prints VAPID_PRIVATE_KEY=... and VAPID_PUBLIC_KEY=...
```

Paste both lines into `.env` and set `VAPID_CONTACT=mailto:your-real-address@gmail.com`
(placeholder domains such as example.com are rejected). **Generate the pair once.** New keys later make every phone
re-enable notifications. Then check:

```bash
flask push-status        # "Web Push: configured."
flask push-selfcheck     # tests every server-side step
```

Push safety (new): the server only accepts subscription URLs from real browser push services
(Google FCM, Mozilla, Apple, Windows) that resolve to public addresses. If a genuine browser is ever refused, add
its push host (no `https://`) to `PUSH_ENDPOINT_EXTRA_HOSTS=host1,host2`. Never add your own domain or an IP address.

## Everything else (optional; defaults are fine)

`APP_NAME`, `LOG_LEVEL`, `DEFAULT_LANGUAGE`, `SUPPORTED_LANGUAGES`, `SESSION_LIFETIME_DAYS`, `SESSION_COOKIE_SECURE`,
`TRUSTED_PROXY_COUNT`, `LOGIN_ACCOUNT_LIMIT`, `RATELIMIT_STORAGE_URI` (default `memory://`: limits are per worker
process, which is fine here), `MESSAGE_MAX_CHARS`, `MAX_IMAGE_MB`, `MAX_VIDEO_MB`, `MAX_DOCUMENT_MB`, `MAX_VOICE_MB`,
`MAX_FILE_MB`, `MAX_VOICE_SECONDS`, `CHAT_PAGE_SIZE`, the `ALLOWED_*_EXTENSIONS` lists, the `OTP_*` limits,
`UNVERIFIED_ACCOUNT_TTL_MINUTES`, the `POLL_INTERVAL_*_MS` values, `PRESENCE_*`, `PUSH_*`, `SSO_*`, `MATH_CLASS_URL`,
`PASSWORD_HASH_METHOD`, `MAIL_DEV_CONSOLE` (development only), `REALTIME_MODE` (leave as `polling`).

`REALTIME_MODE=polling` is deliberate: PythonAnywhere does not give a normal web app WebSockets, so the chat asks the
server every few seconds. Do not install Flask-SocketIO there.

## Rules

* Never paste real values into this guide, into chat, into a ticket, or into Git.
* After editing `.env` you must **Reload** the web app (Web tab → green Reload button).
\n\nFor step-by-step diagnosis, see [Gmail OTP troubleshooting](GMAIL_OTP_TROUBLESHOOTING.md).\n