# 08 – Final release checklist

Tick every box before you call the release done.

## Before

- [ ] Verified backup made (`Backup verified.`) **and** downloaded to my computer.
- [ ] Old code archived (`arnab_chat_web-code-before-fix.tgz`).
- [ ] `python tools/db_inspect.py` output saved (`~/db_inspect_before.txt`).
- [ ] `.env` exists, `chmod 600`, contains `SECRET_KEY`, `APP_ENV=production`, `ADMIN_EMAILS`; nothing was pasted anywhere public.

## Deploy

- [ ] ZIP unpacked; `migrations/versions/` has exactly 12 files; `.env` and `instance/chat.db` still exist.
- [ ] `pip install -r requirements.txt` finished without errors in the correct virtualenv.
- [ ] `flask db heads` → one line `a9f0c3d7b1e2 (head)`; `flask db branches` → empty.
- [ ] `flask db upgrade` finished without error; `flask db current` → `a9f0c3d7b1e2 (head)`.
- [ ] `python tools/db_inspect.py` → "schema is complete and stamped at the current head"; row counts equal the "before" numbers.
- [ ] WSGI file, virtualenv, source directory, static mapping, Force HTTPS checked; web app reloaded.

## Verify

- [ ] `python -m pytest -q` ran in the virtualenv (`pip install -r requirements-dev.txt`) and I have its result (pass/fail counts).
- [ ] Guide 6 checklist completed (A–I).
- [ ] An existing user logged in with the old password; old messages are visible.
- [ ] Admin "New messages" off keeps old chats readable; "Chat open" off closes the whole chat; both turned back on.
- [ ] Admin → Database: a backup can be made and verified. Admin → Security opens.
- [ ] Error log has no new traceback.

## Security

- [ ] `https://` only; "Force HTTPS" enabled.
- [ ] No `.env`, no `*.db`, no backups in any ZIP, Git repo or shared folder.
- [ ] Old backup copies on the server are deleted after a week; the off-server copy is stored privately (it contains password hashes).
- [ ] Only people who need it are in `ADMIN_EMAILS`.

## After

- [ ] Keep the verified backup and the old-code archive for at least 7 days.
- [ ] Schedule a weekly `python tools/db_backup.py` (PythonAnywhere → Tasks, e.g. `cd ~/arnab_chat_web-main && ~/.virtualenvs/goppo-venv/bin/python tools/db_backup.py`) and download one copy a month.
