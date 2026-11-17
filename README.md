# Goppo - private chat website

Mobile-first, WhatsApp-style private messaging. Flask + SQLite + vanilla JavaScript.
The name comes from `APP_NAME` in `.env`.

**Features:** secure sign-up with Gmail OTP, Public/Private accounts and message requests, People page,
profiles with cover photos, private 1-to-1 chat (text, emoji, photos, video, documents, files, voice),
Sent/Delivered/Seen, delete for me / for everyone, online / last seen / typing, Notice Box,
English + Bengali, Settings (change password by OTP, privacy, notifications), Admin panel (users,
announcements, reports, statistics, audit log).

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

Open `.env` and set `SECRET_KEY` (`python -c "import secrets; print(secrets.token_hex(32))"`).
Then fill in Gmail and Cloudinary (below), and put **your own email in `ADMIN_EMAILS`**.

```bash
export FLASK_APP=run.py            # Windows: set FLASK_APP=run.py
flask db upgrade                   # creates instance/chat.db
flask check-services               # tests Gmail + Cloudinary without sending anything
flask generate-vapid-keys          # new Web Push key pair for .env
flask push-status                  # says exactly which Web Push setting is missing or wrong
python run.py                      # http://localhost:5000
```

Open the site on a phone-sized screen. Voice recording needs **HTTPS or localhost** (browser rule).

### Gmail and Cloudinary
- Gmail: `MAIL_USERNAME` + `MAIL_PASSWORD` (a Gmail **App Password**; needs 2-step verification).
- Cloudinary: `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`.
- No Gmail yet? For local testing only, set `MAIL_DEV_CONSOLE=1` (with `APP_ENV=development`): OTP
  emails are printed in the terminal. Registration needs a profile photo, so Cloudinary keys are required.
- Every upload size, extension and folder is in `.env.example`. Change `.env`, not code.

### First admin
Put your email in `ADMIN_EMAILS` (comma-separated for more). Register with it and enter the OTP:
you become admin and see an **Admin panel** button on your profile page. Remove the email later and
the role is removed at the next login.

## Tests

```bash
pip install -r requirements-dev.txt        # pytest lives here, not in requirements.txt
python -m pytest -q                        # includes migrations, SSRF, push and admin tests
node --test tests/browser/push_state.test.js   # push.js logic, no npm install needed
cd tests/browser && npm install && ./run.sh   # 55 checks: the real JavaScript in jsdom (needs Node 18+)
```

## Deploy
Start at **`deployment/README.md`** (PythonAnywhere, step by step, with backup, migration, restore and rollback).
Repair history: `CODE_REPAIR_REPORT.md`; what was and was not tested: `FINAL_TEST_REPORT.md`.

Database tools that need only Python (work even when the app cannot start):

```bash
python tools/db_inspect.py     # read-only: revision, missing tables/columns, row counts
python tools/db_backup.py      # verified backup into backups/
python tools/db_restore.py backups/<file>.db --yes     # restore (saves the current DB first)
```

## Database changes
The migration history is **one straight line** (head `a9f0c3d7b1e2`). Every migration checks what already exists before
adding it, so it is safe on fresh and on existing databases. Rules for new migrations: one head only
(`flask db heads` must print one line), additive and guarded, never drop user data.
```bash
flask db heads                 # must be exactly one line
flask db migrate -m "describe the change" && flask db upgrade
```
Set `DATABASE_URL` in `.env` to move to PostgreSQL.
