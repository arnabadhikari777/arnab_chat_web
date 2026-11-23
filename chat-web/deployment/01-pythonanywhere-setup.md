# 01 – PythonAnywhere setup

Replace `YOUR_USERNAME` with your PythonAnywhere username everywhere. Your site address is
`https://YOUR_USERNAME.pythonanywhere.com`.

**If the website already exists on PythonAnywhere, you do NOT create a new web app.** Skip to section 5
(folders) and only check that the values in sections 2–4 match what you already have.

## 1. Account

* A free ("Beginner") account works for this project: it uses polling (no WebSockets), SQLite (no database server)
  and no background workers.
* Free accounts can only reach the internet through an **allow-list**. Gmail, Cloudinary and the browser push
  services must be reachable. You test that in guide 2 with `flask check-services` and `flask push-selfcheck`.
  If a service is blocked on the free plan, the fix is a paid plan or another provider; no code change helps.

## 2. Python version

The project was running on **Python 3.13** on your server (from the error log). Keep using the same version.

* Web tab → **Python version** → choose `3.13` (the same one shown in your error log).
* Honest limit: the dependencies were pinned to ranges (`Flask>=3.0,<4`, `Flask-SQLAlchemy>=3.1,<4`,
  `Flask-Migrate>=4.0,<5`, `pywebpush>=1.14,<3`, ...). I could not install them for Python 3.13 here, so the first
  `pip install -r requirements.txt` on your account is the real compatibility test. If it fails, read the last 15
  lines of its output and see guide 7, section "pip install".

## 3. Web app (only if you do not already have one)

1. **Web** tab → **Add a new web app** → your domain → **Manual configuration** (NOT "Flask") → Python 3.13.
2. You will fill in the source directory, virtualenv, WSGI file and static files in guide 4.

## 4. Virtual environment

Open **Consoles → Bash** and run:

```bash
mkvirtualenv --python=/usr/bin/python3.13 goppo-venv
```

The environment lives at `/home/YOUR_USERNAME/.virtualenvs/goppo-venv`. In any later console, switch to it with:

```bash
workon goppo-venv
```

If you already have a working virtualenv for this site, keep using it (look at the **Virtualenv** box on the Web tab).

## 5. Folders (project upload)

The ZIP unpacks to a folder named `arnab_chat_web-main/`. Typical result:

```
/home/YOUR_USERNAME/arnab_chat_web-main/
├── app/                 code, templates, static files, translations
├── migrations/          database migrations (12 files in versions/)
├── tools/               db_inspect.py, db_backup.py, db_restore.py
├── deployment/          this guide
├── tests/
├── wsgi.py   run.py   requirements.txt   requirements-dev.txt   .env.example
├── instance/            created on first start: contains chat.db  (YOUR DATA. Never in the ZIP.)
├── backups/             created by the backup tool                 (never in the ZIP)
└── logs/                app.log                                    (never in the ZIP)
```

Use **your existing folder** if the site already runs from somewhere else. Find it on the Web tab: the
**Source code** box. Everything in these guides that says `~/arnab_chat_web-main` means that folder.

Upload and unpack: see guide 5, step 4.

## 6. Permissions

Nothing special is needed. After the first start you can tighten the secrets file:

```bash
chmod 600 ~/arnab_chat_web-main/.env
chmod 700 ~/arnab_chat_web-main/instance ~/arnab_chat_web-main/backups
```
