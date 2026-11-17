#!/usr/bin/env python3
"""Make a consistent copy of the SQLite database with SQLite's own backup API (safe while the site is running).

    python tools/db_backup.py                      # database from .env / instance/chat.db -> backups/
    python tools/db_backup.py /path/to/chat.db     # explicit file

Writes  backups/chat-YYYYmmdd-HHMMSS.db  (permissions 600), then verifies the copy (integrity + row counts).
Never modifies the live database. Exit code 0 only when the copy verified.
"""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db_inspect import ROOT, find_db  # noqa: E402


def main(argv):
    src_path = find_db(argv)
    if not src_path.is_file():
        print(f"Database not found: {src_path}")
        return 2
    out_dir = ROOT / "backups"
    out_dir.mkdir(exist_ok=True)
    target = out_dir / f"chat-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
    target.chmod(0o600)
    check = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        ok = check.execute("pragma integrity_check").fetchone()[0]
        tables = [r[0] for r in check.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
        counts = {t: check.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
        live = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
        same = all(live.execute(f'select count(*) from "{t}"').fetchone()[0] == n for t, n in counts.items())
        live.close()
    finally:
        check.close()
        src.close()
    print(f"Backup file : {target}")
    print(f"Integrity   : {ok}")
    for t in ("users", "messages", "conversations", "notifications"):
        if t in counts:
            print(f"  {t:<14}: {counts[t]} rows")
    if ok != "ok" or not same:
        print("BACKUP DID NOT VERIFY. Do not continue. Try again, or copy the .db file by hand.")
        return 1
    print("Backup verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
