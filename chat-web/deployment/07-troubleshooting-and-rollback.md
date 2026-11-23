# 07 – Troubleshooting and rollback

## 1. Where the logs are (Web tab → "Log files")

| File | What it shows |
|---|---|
| `/var/log/YOUR_USERNAME.pythonanywhere.com.error.log` | Python tracebacks, import errors, WSGI errors. **Look here first.** |
| `/var/log/YOUR_USERNAME.pythonanywhere.com.server.log` | Server start/stop/reload messages, worker crashes. |
| `/var/log/YOUR_USERNAME.pythonanywhere.com.access.log` | Every request and status code. |
| `~/arnab_chat_web-main/logs/app.log` | Application log (rotating, 1 MB × 5). Secrets and OTP codes are never written there. |

Read the **bottom** of the file (the newest lines). In a console: `tail -n 60 /var/log/YOUR_USERNAME.pythonanywhere.com.error.log`.
The page text "Error code: Unhandled Exception" only means "a traceback is in the error log".

## 2. Error → cause → fix

| You see | Cause | Fix |
|---|---|---|
| `sqlite3.OperationalError: no such column: users.suspended_until` (or `session_epoch`, `admin_role`) | Database behind the code (the original outage) | Guide 3 sections 1 → 5 |
| `no such column: push_deliveries.acked_at` | Same | Same |
| `no such table: site_settings` / `moderation_notes` / `security_events` | Same | Same (`flask db upgrade`) |
| `Multiple head revisions are present` | Old and new migration files are mixed | Re-unzip so `migrations/versions/` has exactly the 12 files (guide 5 step 4); `flask db heads` must show one line |
| `Can't locate revision identified by '…'` | A migration file the database remembers is missing | Re-unzip the ZIP over the project: all 12 files, including the old ids, are in it |
| `table … already exists` / `duplicate column name` | An old, unguarded migration file is still there | Re-unzip; the repaired files check first and skip |
| `RuntimeError: SECRET_KEY is missing` | No `.env` or no `SECRET_KEY` | Guide 2 |
| `ModuleNotFoundError: No module named 'wsgi'` / `'app'` | Wrong path in the WSGI file | Guide 4 section 2 |
| `ModuleNotFoundError: No module named 'flask_sqlalchemy'` (or `flask_migrate`, `flask_login`, `pywebpush`) | Virtualenv not set or requirements not installed | Set Virtualenv on the Web tab; `workon goppo-venv && pip install -r requirements.txt` |
| `ImportError … from werkzeug` / version conflicts | Mixed package versions | `pip install -r requirements.txt --upgrade` inside the virtualenv |
| Site has no CSS/JS | Static mapping missing | Guide 4 section 3 |
| 403/404 on `/admin/...` | Not logged in as an admin, or lower staff level | Log in with an `ADMIN_EMAILS` address; Database page needs super admin |
| Registration: "could not send e-mail" | Mail not working from this plan | `flask check-services`; use an HTTPS provider (guide 2) |
| `flask push-selfcheck` shows a network failure | Plan's allow-list blocks the push service | Paid plan, or no push |
| Notification not delivered, Admin → Security shows `expired` and "endpoint not allowed" | The SSRF guard rejected the push host | Only if it is a genuine browser push host, add it to `PUSH_ENDPOINT_EXTRA_HOSTS` |
| Everyone suddenly signed out | `SECRET_KEY` changed | Restore the old `SECRET_KEY` |
| `database is locked` now and then | SQLite with several workers | Retry; avoid running long scripts while the site is busy |
| After upgrade `flask db current` shows two lines | Both old heads were stamped | Guide 3 section 5 (fallback) |
| `pip install` fails building a package | Package wheel missing for this Python | Copy the last 15 lines of the output; do not change Python versions blindly |

## 3. Quick self-diagnosis

```bash
cd ~/arnab_chat_web-main && workon goppo-venv && export FLASK_APP=run.py
python tools/db_inspect.py                       # database vs. what the code needs (read-only)
python -c "from wsgi import application; print('app starts OK')"
flask db heads && flask db current
tail -n 60 /var/log/YOUR_USERNAME.pythonanywhere.com.error.log
```

## 4. Roll back the CODE

```bash
cd ~
mv arnab_chat_web-main arnab_chat_web-main.broken
tar xzf arnab_chat_web-code-before-fix.tgz        # restores the old code (no .env / instance / backups / logs were in it)
cp -a arnab_chat_web-main.broken/.env arnab_chat_web-main/
cp -a arnab_chat_web-main.broken/instance arnab_chat_web-main/
cp -a arnab_chat_web-main.broken/backups arnab_chat_web-main/ 2>/dev/null
cp -a arnab_chat_web-main.broken/logs arnab_chat_web-main/ 2>/dev/null
```

Web tab → Reload. The old code runs fine on the upgraded database because the migrations only added columns/tables.
(The old code will, of course, still have the original outage if the database was never upgraded.)

## 5. Roll back the DATABASE (only if data was damaged)

```bash
cd ~/arnab_chat_web-main && workon goppo-venv
ls backups/
python tools/db_restore.py backups/chat-YYYYmmdd-HHMMSS.db           # dry run
python tools/db_restore.py backups/chat-YYYYmmdd-HHMMSS.db --yes     # restore (current DB saved first)
```

Reload the web app. Compare `python tools/db_inspect.py` counts with `~/db_inspect_before.txt`.

## 6. Last resort

Do not delete anything. Send me: the last 60 lines of the error log, the output of `python tools/db_inspect.py`, and the
output of `flask db current` and `flask db heads`.
