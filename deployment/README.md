# Goppo – deployment guide (PythonAnywhere)

This folder is the complete, ordered guide for putting the repaired project on PythonAnywhere **without losing any
account, message or setting**.

> **Why the site showed "Something went wrong :-(" / "Unhandled Exception"**
> The migration history had **two heads** (two parallel branches of migrations). Depending on which branch was applied,
> the live database was missing columns that the application needs (`users.suspended_until`, `users.session_epoch`,
> `users.admin_role`). The first thing every page does is load the signed-in user (`_load_user()` in `app/__init__.py`).
> That query selects those columns, SQLite answered `no such column`, and the request crashed.
> The repaired migrations are now one single line, every step checks what already exists before adding anything,
> and a final step adds whatever is still missing. **Nothing is dropped, renamed or rewritten.**
> (This is the cause that fits all the evidence in the project. It was **not** confirmed against your live server,
> because I cannot see it: the first step below tells you in 10 seconds whether it is your cause.)

## The path in one screen

| Step | Guide | What you do |
|---|---|---|
| 1 | [01-pythonanywhere-setup.md](01-pythonanywhere-setup.md) | Account, web app, Python version, virtualenv, folders |
| 2 | [02-environment-configuration.md](02-environment-configuration.md) | `.env`, secret key, Gmail, Cloudinary, VAPID keys, database URL |
| 3 | [03-database-backup-and-migration.md](03-database-backup-and-migration.md) | **Back up first**, inspect, migrate, restore if needed |
| 4 | [04-wsgi-and-static-configuration.md](04-wsgi-and-static-configuration.md) | WSGI file and the `/static/` mapping |
| 5 | [05-install-and-deploy.md](05-install-and-deploy.md) | The exact commands in the exact order |
| 6 | [06-post-deployment-testing.md](06-post-deployment-testing.md) | Checklist to prove everything works |
| 7 | [07-troubleshooting-and-rollback.md](07-troubleshooting-and-rollback.md) | Logs, every known error, how to go back |
| 8 | [08-final-release-checklist.md](08-final-release-checklist.md) | Last tick-list before you call it done |

## If the live site is down right now (fastest path)

1. Open a **Bash console** on PythonAnywhere, then run:
   ```bash
   cd ~/arnab_chat_web-main        # your real project folder
   python tools/db_inspect.py      # read-only, changes nothing
   ```
   If it prints `MISSING COLUMN users.suspended_until` (or `session_epoch` / `admin_role`) you have found the cause.
2. Follow guides 3 → 5. In short: **backup → upload the fixed code → `pip install -r requirements.txt` → `flask db upgrade` → Reload**.

## Rules this project follows (so you can trust the steps)

* Your database file is never deleted, replaced or reset by any step here. The only step that replaces it is the
  explicit **restore**, which saves the current file first.
* Passwords, e-mails, messages, notices, push subscriptions, admin accounts and saved settings are never touched by
  the migrations. They only **add** missing columns and tables.
* `.env` and `instance/` (where the database lives) are **not** inside the ZIP, so unpacking the ZIP cannot overwrite them.

## What is and is not verified

See `FINAL_TEST_REPORT.md` in the project root. Short version: the code was statically checked and the standalone tools were
executed, but the full pytest suite and the real Alembic migrations could **not** be run in the environment this repair
was made in (no network to install Flask-SQLAlchemy / Alembic). Guide 5 therefore includes the one command
(`python -m pytest -q`) that runs them for the first time on your machine or on PythonAnywhere, and guide 3 contains
the verification commands that prove the database is right.
