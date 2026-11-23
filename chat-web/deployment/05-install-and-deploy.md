# 05 – Install and deploy (exact order)

Do not skip a step. `YOUR_USERNAME` = your PythonAnywhere username. Assume the project folder
`~/arnab_chat_web-main` (use your real one).

## Step 1 – Back up (guide 3, section 1)

```bash
cd ~/arnab_chat_web-main && workon goppo-venv && export FLASK_APP=run.py
python tools/db_backup.py                 # must end with: Backup verified.
cd ~ && tar czf arnab_chat_web-code-before-fix.tgz --exclude='*/instance' --exclude='*/backups' --exclude='*/logs' arnab_chat_web-main
```

Download the newest file from `~/arnab_chat_web-main/backups/` to your computer (Files tab).

## Step 2 – Look before changing (read-only)

```bash
cd ~/arnab_chat_web-main
python tools/db_inspect.py | tee ~/db_inspect_before.txt
```

## Step 3 – Upload the ZIP

Files tab → go to `/home/YOUR_USERNAME/` → **Upload a file** → `arnab_chat_web_FIXED.zip`.

## Step 4 – Unpack over the project (this keeps your data)

The ZIP contains **no** `.env`, `instance/`, `backups/` or `logs/`, so unpacking cannot overwrite your secrets or database.

```bash
cd ~
unzip -o arnab_chat_web_FIXED.zip          # creates/updates arnab_chat_web-main/
ls arnab_chat_web-main/migrations/versions/*.py | wc -l      # must print 12
ls arnab_chat_web-main/.env arnab_chat_web-main/instance/chat.db   # both must still exist
```

If you deploy into a differently named existing folder, unzip to a temp folder and copy the contents in
(this keeps `.env` and `instance/` because they are not in the ZIP):

```bash
cd ~ && mkdir -p fixed_tmp && unzip -o arnab_chat_web_FIXED.zip -d fixed_tmp
cp -a fixed_tmp/arnab_chat_web-main/. ~/YOUR_EXISTING_FOLDER/
rm -rf fixed_tmp
```

## Step 5 – Install dependencies

```bash
cd ~/arnab_chat_web-main && workon goppo-venv
pip install -r requirements.txt
```

(`pytest` is no longer in `requirements.txt`. For tests only: `pip install -r requirements-dev.txt`.)

## Step 6 – Environment

Compare your `.env` with guide 2. At minimum: `SECRET_KEY`, `APP_ENV=production`, `ADMIN_EMAILS`. Add nothing you do not need.

```bash
export FLASK_APP=run.py
flask check-services        # e-mail + Cloudinary
flask push-status           # only if you use notifications
```

## Step 7 – Migration check, then migrate

```bash
flask db heads              # exactly one line: c3d5e7f9a1b2 (head)
flask db current            # note it
flask db upgrade
flask db current            # c3d5e7f9a1b2 (head)
python tools/db_inspect.py  # RESULT: schema is complete and stamped at the current head.
```

Compare the row counts with `~/db_inspect_before.txt`: they must be identical.

## Step 8 – WSGI and static (guide 4), then Reload

Web tab → check Source code / Virtualenv / WSGI file / Static files → green **Reload**.

## Step 9 – Run the automated tests (first real run on your side)

```bash
pip install -r requirements-dev.txt
python -m pytest -q 2>&1 | tail -25
```

Tests use their own in-memory database and fake mail/push/Cloudinary: they never touch `instance/chat.db`.
Send me the last 25 lines if anything fails.

## Step 10 – Test the live site

Guide 6. If the homepage still shows an error, read `/var/log/YOUR_USERNAME.pythonanywhere.com.error.log`
(guide 7).

## Step 11 – Clean up

Keep `~/arnab_chat_web-code-before-fix.tgz` and the verified backup for at least a week.
