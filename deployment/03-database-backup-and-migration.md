# 03 – Database backup, inspection, migration and restore

**Golden rules**

1. **Back up before you change anything.** Always. Even for read-only looking steps, take the backup first.
2. **Never delete `instance/chat.db`.** No step in this guide asks you to. If a guide or a person tells you to delete it, stop.
3. The repaired migrations only **add** missing tables/columns. They never drop, rename or rewrite data.
4. Do the commands exactly in this order.

All commands run in a PythonAnywhere **Bash console**:

```bash
cd ~/arnab_chat_web-main        # your real project folder
workon goppo-venv               # your virtualenv
export FLASK_APP=run.py
```

## 1. Back up the database (do this first)

```bash
python tools/db_backup.py
```

It uses SQLite's own backup function (safe while the site is running), writes `backups/chat-YYYYmmdd-HHMMSS.db`
with permissions 600 and then **verifies** the copy. You must see:

```
Integrity   : ok
Backup verified.
```

If it says `BACKUP DID NOT VERIFY`, stop and fix that first. Also keep an off-server copy: in the PythonAnywhere
**Files** tab open `backups/`, click the file and download it to your computer.

If your database is not in the default place, pass the path:

```bash
python tools/db_backup.py /home/YOUR_USERNAME/arnab_chat_web-main/instance/chat.db
```

Also back up the old code (so you can go back):

```bash
cd ~ && tar czf arnab_chat_web-code-before-fix.tgz --exclude='*/instance' --exclude='*/backups' --exclude='*/logs' arnab_chat_web-main
```

## 2. Find out which revision the database says it is at

```bash
flask db current
```

Typical answers and what they mean:

| Output | Meaning |
|---|---|
| `e5b3c8a1d7f4` | Database followed the *second* old branch. **This state makes `_load_user()` crash** (no `suspended_until`, `session_epoch`, `admin_role`). |
| `f1a2b3c4d5e6` | Followed the *first* old branch; columns are present, only a table step may be pending. |
| two lines (`e5b3c8a1d7f4` and `f1a2b3c4d5e6`) | Both old heads were stamped. See section 5. |
| `a4f8c2e1b9d0` or older | Stopped early. Many columns missing. |
| `a9f0c3d7b1e2` | Already at the repaired head. |
| nothing | Never stamped. Run the inspector (section 3) before anything else. |
| `Can't locate revision identified by 'xxxx'` | The old migration files are not all present. See guide 7. |

## 3. Look at the real tables and columns (read-only)

```bash
python tools/db_inspect.py
```

It never writes. It lists every table/column the application needs that your database lacks, the Alembic revision
recorded in the file, and row counts. Example of the crash cause:

```
Alembic rows  : ['e5b3c8a1d7f4']
MISSING COLUMN users.admin_role
MISSING COLUMN users.suspended_until
MISSING COLUMN users.session_epoch
RESULT: 3 thing(s) missing. This is what makes the site show 'Something went wrong'.
```

Write down the `users`, `messages` and `conversations` row counts. You will compare them after the migration.

You can also look manually (read-only):

```bash
python - <<'PY'
import sqlite3
c = sqlite3.connect("file:instance/chat.db?mode=ro", uri=True)
print([r[0] for r in c.execute("select name from sqlite_master where type='table' order by 1")])
print([r[1] for r in c.execute("pragma table_info(users)")])
PY
```

## 4. Check the migration files are correct (conflict detection)

```bash
ls migrations/versions/*.py | wc -l      # must print 12
flask db heads                           # must print exactly ONE line: a9f0c3d7b1e2 (head)
flask db branches                        # must print nothing
flask db history | head -14              # a straight line, newest first
```

If `flask db heads` prints **two** lines, the old files from the previous deployment are mixed with the new ones.
Re-extract the ZIP over the project (guide 5, step 4) so all 12 files in `migrations/versions/` come from the ZIP.
**Do not run `flask db upgrade` while two heads are shown.**

## 5. Apply the repaired migration

```bash
flask db upgrade
```

What it does, step by step (each step first checks whether its table/column already exists and skips it if so):

* `b7e3…`, `c7e1…`: make sure `push_deliveries.acked_at` exists.
* `c9a1…`: `site_settings`, `moderation_notes`, `users.suspended_until`, `users.session_epoch`.
* `d2a9…`: guard for `site_settings` (was a duplicate).
* `d3b8…`: `users.admin_role`.
* `e5b3…`, `f1a2…`: `security_events` (was created twice in the old history).
* `a9f0…`: final reconcile: adds anything the models still need. Adds only.

Columns that exist only in the abandoned branch (`users.status_until`, `push_deliveries.ack_token_hash`,
`ack_expires_at`, `message_upto_id`, `acked_upto_id`, `applied_upto_id`) are **left in place**, unused and harmless.

**If `flask db current` showed two revisions** (both old heads), try `flask db upgrade` first. If Alembic complains,
the safe fallback is below. It changes only the version *label*, never your data, and is safe because the final step
(`a9f0…`) adds every missing column anyway:

```bash
python tools/db_backup.py          # again, to be sure
python - <<'PY'
import sqlite3
c = sqlite3.connect("instance/chat.db")
c.execute("delete from alembic_version")
c.execute("insert into alembic_version values ('f1a2b3c4d5e6')")
c.commit()
PY
flask db upgrade
```

## 6. Verify (all four must be true)

```bash
flask db current            # a9f0c3d7b1e2 (head)
python tools/db_inspect.py  # RESULT: schema is complete and stamped at the current head.
```

* The `users`, `messages`, `conversations`, `notifications` counts equal the numbers you wrote down in section 3.
* Open the site and log in with an existing account (guide 6).

## 7. If something goes wrong: restore

Restoring replaces the live database with a backup. It **first saves the current file** as
`backups/pre-restore-<time>.db`, and it refuses a backup that fails SQLite's integrity check.

```bash
ls backups/                                                   # pick the file you made in section 1
python tools/db_restore.py backups/chat-20261116-204930.db           # dry run: checks only
python tools/db_restore.py backups/chat-20261116-204930.db --yes     # really restore
```

Then **Reload** the web app (Web tab). To undo a restore, restore the `pre-restore-*.db` file the same way.

Because the migrations only add things, the **old code keeps working on the upgraded database** (extra columns are
ignored). So after a failed *code* deployment you normally only need to put the old code back (guide 7), not the database.

## 8. New databases (fresh install)

```bash
flask db upgrade        # creates instance/chat.db with the complete schema
```

Never use `db.create_all()` on production, and never `flask db stamp head` on a database that has not been
inspected: stamping tells Alembic "everything is already here" and can hide missing columns. (The only stamp in this
guide is the version-label fallback in section 5, which is covered by the final reconcile step.)

## 9. Admin → Database page

Super admins can also make, verify, download and delete backups, and remove old read notices / old security-log lines
under **Admin → Database**. Restoring is deliberately **not** possible from the web page (replacing a live database
from a web request is unsafe): use `tools/db_restore.py`. A backup file contains every password hash: download it only to
a safe place and delete old copies.
