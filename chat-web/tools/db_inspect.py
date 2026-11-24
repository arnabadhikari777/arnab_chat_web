#!/usr/bin/env python3
"""READ-ONLY database inspector. Needs only Python's standard library (works even when the app cannot start).

    python tools/db_inspect.py                       # uses DATABASE_URL from .env, else instance/chat.db
    python tools/db_inspect.py /path/to/chat.db

It never writes to the database. It prints:
  * the Alembic revision(s) recorded in the database,
  * every table / column the application models need but the database does not have,
  * the exact next step.
Exit code: 0 = schema complete, 1 = something missing, 2 = file could not be opened.

EXPECTED below is generated from app/models/*.py (tests/test_schema_consistency.py keeps it honest).
"""
import os
import sqlite3
import sys
from pathlib import Path

HEAD = "d4f6a8c0e2b3"
ROOT = Path(__file__).resolve().parent.parent

EXPECTED = {
    "otp_verifications": [
        "id",
        "email",
        "purpose",
        "code_hash",
        "expires_at",
        "attempts",
        "used_at",
        "last_sent_at",
        "created_at"
    ],
    "users": [
        "id",
        "full_name",
        "email",
        "username",
        "password_hash",
        "bio",
        "avatar_url",
        "avatar_public_id",
        "cover_url",
        "cover_public_id",
        "is_public",
        "is_email_verified",
        "status",
        "role",
        "admin_role",
        "language",
        "notify_messages",
        "notify_announcements",
        "last_seen_at",
        "suspended_until",
        "session_epoch",
        "created_at",
        "updated_at"
    ],
    "admin_audit_logs": [
        "id",
        "admin_id",
        "action",
        "target_type",
        "target_id",
        "details",
        "created_at"
    ],
    "announcements": [
        "id",
        "title",
        "body",
        "title_bn",
        "body_bn",
        "is_active",
        "created_by",
        "created_at"
    ],
    "conversations": [
        "id",
        "user_low_id",
        "user_high_id",
        "last_message_at",
        "created_at"
    ],
    "message_requests": [
        "id",
        "sender_id",
        "receiver_id",
        "status",
        "created_at",
        "responded_at"
    ],
    "moderation_notes": [
        "id",
        "user_id",
        "admin_id",
        "note",
        "created_at"
    ],
    "notifications": [
        "id",
        "user_id",
        "type",
        "payload",
        "link",
        "is_read",
        "dedupe_key",
        "created_at"
    ],
    "push_subscriptions": [
        "id",
        "user_id",
        "endpoint",
        "p256dh",
        "auth",
        "user_agent",
        "status",
        "failure_count",
        "last_success_at",
        "last_attempt_at",
        "created_at",
        "updated_at"
    ],
    "reports": [
        "id",
        "reporter_id",
        "reported_user_id",
        "reason",
        "details",
        "status",
        "handled_by",
        "handled_at",
        "created_at"
    ],
    "security_events": [
        "id",
        "kind",
        "user_id",
        "ip",
        "detail",
        "created_at"
    ],
    "site_settings": [
        "key",
        "value",
        "updated_by",
        "updated_at"
    ],
    "call_history": [
        "id",
        "call_id",
        "conversation_id",
        "caller_id",
        "receiver_id",
        "call_type",
        "status",
        "started_at",
        "answered_at",
        "ended_at",
        "duration_seconds",
        "ringing_at",
        "accepted_at",
        "ended_by_id",
        "end_reason",
        "created_at",
        "updated_at"
    ],
    "native_devices": [
        "id",
        "user_id",
        "platform",
        "device_id",
        "push_token",
        "token_hash",
        "app_version",
        "os_version",
        "device_model",
        "status",
        "failure_count",
        "last_seen_at",
        "last_success_at",
        "created_at",
        "updated_at"
    ],
    "messages": [
        "id",
        "conversation_id",
        "sender_id",
        "type",
        "body",
        "client_uuid",
        "status",
        "delivered_at",
        "seen_at",
        "deleted_for_all",
        "deleted_at",
        "created_at"
    ],
    "push_deliveries": [
        "id",
        "notification_id",
        "subscription_id",
        "user_id",
        "group_key",
        "status",
        "attempt_count",
        "last_error",
        "accepted_at",
        "acked_at",
        "created_at",
        "updated_at"
    ],
    "message_attachments": [
        "id",
        "message_id",
        "public_id",
        "url",
        "resource_type",
        "mime_type",
        "original_name",
        "size_bytes",
        "duration_seconds",
        "width",
        "height"
    ],
    "message_deletions": [
        "message_id",
        "user_id",
        "deleted_at"
    ]
}

# Columns that exist only in the abandoned second migration branch. Harmless if present; never required.
LEGACY_OK = {
    "users": {"status_until"},
    "push_deliveries": {"ack_token_hash", "ack_expires_at", "message_upto_id", "acked_upto_id", "applied_upto_id"},
}


def find_db(argv):
    if len(argv) > 1:
        return Path(argv[1])
    url = ""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip("\"'")
    url = url or os.environ.get("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        raw = url[len("sqlite:///"):].split("?")[0]
        p = Path(raw)
        return p if p.is_absolute() else ROOT / p
    return ROOT / "instance" / "chat.db"


def main(argv):
    path = find_db(argv)
    print(f"Database file : {path}")
    if not path.is_file():
        print("NOT FOUND. Check the path (see deployment/03-database-backup-and-migration.md).")
        return 2
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    except sqlite3.DatabaseError as exc:
        print(f"CANNOT READ: {exc}")
        return 2
    print(f"Size          : {path.stat().st_size / 1048576:.2f} MB")
    print(f"Integrity     : {con.execute('pragma integrity_check').fetchone()[0]}")

    revs = []
    if "alembic_version" in tables:
        revs = [r[0] for r in con.execute("select version_num from alembic_version")]
    print(f"Alembic rows  : {revs or 'NONE (database is not stamped)'}")
    if len(revs) > 1:
        print("  NOTE: more than one row. This is what the old two-branch history leaves behind.")
        print("  The repaired chain handles it with the steps in deployment/03-database-backup-and-migration.md (section 5).")

    problems = 0
    for table, cols in sorted(EXPECTED.items()):
        if table not in tables:
            problems += 1
            print(f"MISSING TABLE  {table}")
            continue
        have = {r[1] for r in con.execute(f'pragma table_info("{table}")')}
        for col in cols:
            if col not in have:
                problems += 1
                print(f"MISSING COLUMN {table}.{col}")
        extra = have - set(cols)
        legacy = extra & LEGACY_OK.get(table, set())
        if legacy:
            print(f"(legacy, harmless, left alone) {table}: {', '.join(sorted(legacy))}")
        other = extra - legacy
        if other:
            print(f"(extra, ignored) {table}: {', '.join(sorted(other))}")

    for table in ("users", "messages", "conversations", "notifications"):
        if table in tables:
            print(f"Rows in {table:<14}: {con.execute(f'select count(*) from {table}').fetchone()[0]}")

    print()
    if problems == 0 and revs == [HEAD]:
        print("RESULT: schema is complete and stamped at the current head. Nothing to do.")
        return 0
    if problems == 0:
        print("RESULT: no columns or tables are missing. Run the upgrade so the version stamp reaches the head:")
    else:
        print(f"RESULT: {problems} thing(s) missing. This is what makes the site show 'Something went wrong'.")
        print("The repaired migrations add exactly these and nothing else. After a BACKUP, run:")
    print("    flask db upgrade")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
