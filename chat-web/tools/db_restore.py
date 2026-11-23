#!/usr/bin/env python3
"""Restore the SQLite database from a backup made by tools/db_backup.py (or Admin > Database).

    python tools/db_restore.py backups/chat-20261116-204930.db            # dry run: checks only, changes nothing
    python tools/db_restore.py backups/chat-20261116-204930.db --yes      # really restore

Safety rules, all enforced here:
  * The backup must open, pass SQLite's integrity check and contain a users table. Otherwise nothing happens.
  * Before anything is replaced, the CURRENT database is saved as backups/pre-restore-<time>.db.
  * Without --yes this is only a dry run.
  * Stop the site first (PythonAnywhere Web tab: no requests should be running), then Reload afterwards.
Needs only Python's standard library. Exit code 0 = restored (or dry run passed).
"""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db_inspect import ROOT, find_db  # noqa: E402


def check(path: Path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = con.execute("pragma integrity_check").fetchone()[0]
        users = con.execute("select count(*) from users").fetchone()[0]
        return integrity, users
    finally:
        con.close()


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    really = "--yes" in argv
    if len(args) < 1:
        print(__doc__)
        return 2
    backup = Path(args[0])
    live = find_db(["", args[1]] if len(args) > 1 else [""])
    if not backup.is_file():
        print(f"Backup not found: {backup}")
        return 2
    try:
        integrity, users = check(backup)
    except sqlite3.DatabaseError as exc:
        print(f"The backup cannot be used (nothing was changed): {exc}")
        return 1
    print(f"Backup   : {backup}  integrity={integrity}  accounts={users}")
    print(f"Live DB  : {live}")
    if integrity != "ok":
        print("The backup failed its integrity check. Nothing was changed.")
        return 1
    if not really:
        print("Dry run only. Add --yes to restore.")
        return 0

    safe = ROOT / "backups" / f"pre-restore-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.db"
    safe.parent.mkdir(exist_ok=True)
    if live.is_file():
        src = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
        dst = sqlite3.connect(safe)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        safe.chmod(0o600)
        print(f"Saved the current database first: {safe}")

    source = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    target = sqlite3.connect(live)
    try:
        source.backup(target)                       # replaces the content of the live file page by page
    finally:
        target.close()
        source.close()
    integrity, users = check(live)
    print(f"Restored : integrity={integrity}  accounts={users}")
    if integrity != "ok":
        print(f"PROBLEM. Put the saved copy back with:  python tools/db_restore.py {safe} --yes")
        return 1
    print("Done. Reload the web app now (Web tab -> Reload).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
