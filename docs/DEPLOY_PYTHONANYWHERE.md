# Deploying on PythonAnywhere (Free)

## What the free plan can and cannot do (checked November 2026 — plans change, re-check)

| Thing | Free plan | What it means for this app |
|---|---|---|
| WebSockets | **Not supported** for web apps (HTTP only) | Real-time uses **polling** (the browser asks every 2.5 s). Works, but it is not instant push. |
| Web workers | 1 worker | One request at a time. Fine for a small group; many users at once will queue. |
| CPU-seconds | 100 per day, but **web requests do not use them** (only consoles/tasks) | Polling does not burn the quota. |
| Outbound internet | **Allow-list only** | Gmail SMTP is allowed. **Cloudinary: run `flask check-services` on your account** — if it says FAILED, uploads cannot work on the free plan. |
| App lifetime | 1 month, then you must press the "extend" button on the Web tab | Put a reminder in your calendar. |
| Disk | 512 MiB | SQLite database only; all media is on Cloudinary. |
| Scheduled tasks / MySQL | Not for accounts created after 15 Jan 2026 | Not needed by this app. |

Not promised: scalability. This is a good place to **try** the app with friends, not to run it for thousands of people.

## Steps

1. **Upload the code.** In a Bash console: `git clone <your repo> chat_app` (or upload the ZIP and unzip it).
2. **Virtualenv.**
   ```bash
   cd ~/chat_app
   mkvirtualenv --python=python3.12 goppo     # pick a Python version shown in the Web tab
   pip install -r requirements.txt
   ```
3. **Settings.** `cp .env.example .env` then `nano .env` and set at least:
   ```
   APP_ENV=production
   SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
   ADMIN_EMAILS=you@gmail.com
   MAIL_USERNAME=<your gmail>    MAIL_PASSWORD=<gmail app password>
   CLOUDINARY_CLOUD_NAME=...     CLOUDINARY_API_KEY=...     CLOUDINARY_API_SECRET=...
   ```
   `APP_ENV=production` turns on secure cookies, so the site must be opened with `https://`.
4. **Database and checks.** Run migrations from the project directory, using the
   same virtualenv and `.env` configuration as the web app:
   ```bash
   cd ~/chat_app
   workon goppo
   python -m flask --app run.py db upgrade
   python -m flask --app run.py db current
   python -m flask --app run.py check-services  # Gmail: OK?  Cloudinary: OK?
   ```
   `db upgrade` creates the tables on a fresh install and applies pending
   migrations on later updates. The app does not create or migrate tables when
   it starts. Keep any `DATABASE_URL` override in the app's `.env` (not only
   exported in the Bash console), so both this command and the web app use the
   same database.
5. **Web tab → Add a new web app → Manual configuration → same Python version.** Then set:
   - *Source code* and *Working directory*: `/home/<you>/chat_app`
   - *Virtualenv*: `/home/<you>/.virtualenvs/goppo`
   - *WSGI configuration file* — replace its contents with:
     ```python
     import sys
     project = "/home/<you>/chat_app"
     if project not in sys.path:
         sys.path.insert(0, project)
     from wsgi import application
     ```
   - *Static files*: URL `/static/` → directory `/home/<you>/chat_app/app/static`
   - Turn on **Force HTTPS**.
6. Press **Reload**. Open `https://<you>.pythonanywhere.com`, register with the email you put in `ADMIN_EMAILS`, enter the OTP — you are now an admin (an "Admin panel" button appears on your profile page).

## Login or registration returns an internal server error

If the PythonAnywhere error log says `no such table: users`, the web app is
connected to a database whose migrations have not been applied. In a Bash
console, run the database commands from step 4 in `~/chat_app`, after `workon
goppo`, then reload the web app. If the error remains, check that `DATABASE_URL`
is not set differently for the console and the web app; do not delete the
database or run `flask db init`.

## Updating later
```bash
cd ~/chat_app
git pull
workon goppo
pip install -r requirements.txt
python -m flask --app run.py db upgrade
```
then press **Reload** on the Web tab.

## Cloudinary notes
- Check your plan's real upload limits and keep `MAX_*_MB` in `.env` at or below them.
- If PDF or ZIP files will not open after upload, look in Cloudinary *Settings → Security* for the option that restricts delivery of PDF/ZIP files and allow it.

## Backups and logs
- Database: download `instance/chat.db` regularly (Files tab).
- Errors: Web tab → *Error log*, and `logs/app.log` in the project.

## Moving off the free plan later
- **Real-time:** only `app/realtime/` talks to the transport. Add a Socket.IO backend implementing `RealtimeBackend` (see `docs/ARCHITECTURE.md`) on a host with WebSockets (a VPS, Render, Fly.io, Railway ...).
- **Database:** set `DATABASE_URL` (e.g. PostgreSQL) and run `flask db upgrade`.
- **Rate limit / typing state:** they are in memory (one worker). With several workers use Redis: set `RATELIMIT_STORAGE_URI=redis://...` and move typing state into Redis.
