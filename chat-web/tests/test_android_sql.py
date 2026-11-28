"""Task 2: the Android SQLite schema and every SQL statement of LocalStore.kt are valid against that schema.

The Kotlin code cannot run here (no Android runtime). This test extracts the migrations from LocalDb.kt and every SQL string from
LocalStore.kt / SyncEngine and runs `EXPLAIN` on them with Python's sqlite3, so a typo in a table or column name, or invalid SQL,
fails here instead of on the phone. It also exercises the schema's key guarantees (duplicate prevention). It does not prove the
Kotlin logic around the SQL."""
import re
import sqlite3
from pathlib import Path

import pytest

KT = Path(__file__).resolve().parents[2] / "android-app/android/app/src/main/kotlin/com/goppo/chat/local"


def migrations():
    src = (KT / "LocalDb.kt").read_text(encoding="utf-8")
    block = src[src.index("val MIGRATIONS"):src.index("private val open")]
    versions = re.split(r"\n\s*(?://[^\n]*\n\s*)?listOf\(", block)[1:]
    out = []
    for v in versions:
        stmts = re.findall(r'"""(.*?)"""|"([^"\n]+)"', v, re.S)
        out.append([a or b for a, b in stmts])
    return out


@pytest.fixture()
def db():
    con = sqlite3.connect(":memory:")
    for stmts in migrations():
        for s in stmts:
            con.execute(s)
    return con


def test_two_schema_versions_and_all_tables(db):
    assert len(migrations()) == 2
    names = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    assert {"messages", "attachments", "conversations", "outbox", "sync_state", "seen_queue"} <= names


def test_upgrade_path_from_version_1_keeps_data():
    con = sqlite3.connect(":memory:")
    for s in migrations()[0]:
        con.execute(s)
    con.execute("insert into messages(uuid, conv_id, type, status, local_created_at, body) values ('u1', 1, 'text', 'sent', 1, 'keep me')")
    for s in migrations()[1]:                                   # what onUpgrade(1 -> 2) runs
        con.execute(s)
    assert con.execute("select body from messages").fetchone()[0] == "keep me"
    con.execute("insert into seen_queue values (1, 5)")


def test_duplicate_message_ids_are_impossible(db):
    db.execute("insert into messages(server_id, uuid, conv_id, type, status, local_created_at) values (10, 'u-a', 1, 'text', 'sent', 1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into messages(server_id, uuid, conv_id, type, status, local_created_at) values (10, 'u-b', 1, 'text', 'sent', 1)")   # same server id
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into messages(server_id, uuid, conv_id, type, status, local_created_at) values (11, 'u-a', 1, 'text', 'sent', 1)")   # same client uuid
    # many unsent messages (server_id NULL) are fine
    for i in range(3):
        db.execute("insert into messages(uuid, conv_id, type, status, local_created_at) values (?, 1, 'text', 'waiting', 1)", (f"p{i}",))
    db.execute("insert into attachments(message_uuid) values ('x')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into attachments(message_uuid) values ('x')")                       # one attachment per message
    db.execute("insert into outbox(kind, message_uuid, created_at) values ('send_text','p0',1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into outbox(kind, message_uuid, created_at) values ('send_text','p0',1)")   # one queue entry per message and kind


def sql_literals(name):
    src = (KT / name).read_text(encoding="utf-8")
    lits = re.findall(r'"((?:SELECT|UPDATE|INSERT|DELETE)[^"\n]*)"', src)
    lits += [m.strip() for m in re.findall(r'"""\s*((?:SELECT|UPDATE|INSERT|DELETE).*?)"""', src, re.S)]
    return lits


@pytest.mark.parametrize("file", ["LocalStore.kt", "SyncEngine.kt", "MediaManager.kt"])
def test_every_sql_statement_is_valid_against_the_schema(db, file):
    lits = sql_literals(file)
    if file == "LocalStore.kt":
        assert len(lits) > 40
    for sql in lits:
        sql = re.sub(r"\$\{[^}]*\}", "0", sql)                  # Kotlin string templates
        sql = sql.replace('" + (if (beforeServerId != null) " AND server_id < ?" else "") +\n            "', "")
        try:
            db.execute("EXPLAIN " + sql, (None,) * sql.count("?"))
        except sqlite3.Error as e:
            pytest.fail(f"{file}: {sql!r}: {e}")


def test_seen_queue_keeps_the_highest_value(db):
    sql = next(s for s in sql_literals("LocalStore.kt") if "seen_queue" in s and "INSERT" in s)
    db.execute(sql, (3, 10, 3)); db.execute(sql, (3, 4, 3)); db.execute(sql, (3, 12, 3))
    assert db.execute("select upto from seen_queue where conv_id = 3").fetchone()[0] == 12


def test_outbox_and_paging_queries_behave(db):
    for i, sid in enumerate([None, 5, 3, 9]):
        db.execute("insert into messages(server_id, uuid, conv_id, type, status, local_created_at) values (?, ?, 1, 'text', 'sent', ?)", (sid, f"u{i}", i))
    rows = db.execute("SELECT uuid FROM messages WHERE conv_id = ? AND hidden = 0 ORDER BY COALESCE(server_id, 9223372036854775807) DESC, local_created_at DESC LIMIT ?", (1, 3)).fetchall()
    assert [r[0] for r in rows] == ["u0", "u3", "u1"]           # unsent first (newest), then by server id
