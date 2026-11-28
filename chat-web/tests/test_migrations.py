"""The migration history: one head, no branches, and a safe path from every state production could be in.

These tests use a real SQLite FILE (not the in-memory test database) and run the real Alembic migrations.
The 'second branch' test reproduces the production crash: loading an existing user failed because the
database lacked users.suspended_until / session_epoch / admin_role.
"""
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from flask_migrate import upgrade
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models import User

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = str(ROOT / "migrations")
HEAD = "e8a1c4b7d2f9"


def script_directory():
    cfg = Config(str(ROOT / "migrations" / "alembic.ini"))
    cfg.set_main_option("script_location", MIGRATIONS)
    return ScriptDirectory.from_config(cfg)


@pytest.fixture()
def file_db(tmp_path, monkeypatch):
    """An app whose database is a real file, plus the path of that file."""
    from app import config, create_app

    path = tmp_path / "chat.db"
    monkeypatch.setattr(config.TestingConfig, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{path}")
    app = create_app("testing")
    with app.app_context():
        yield path
        db.session.remove()
        db.engine.dispose()


def sql(path, script):
    con = sqlite3.connect(path)
    try:
        con.executescript(script)
        con.commit()
    finally:
        con.close()
    db.session.remove()
    db.engine.dispose()           # forget pooled connections that saw the old schema


def versions(path):
    con = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in con.execute("select version_num from alembic_version"))
    finally:
        con.close()


def sqlite_tables(path):
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    finally:
        con.close()


def columns(path, table):
    con = sqlite3.connect(path)
    try:
        return {r[1] for r in con.execute(f'pragma table_info("{table}")')}
    finally:
        con.close()


def tables(path):
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    finally:
        con.close()


def make_alice():
    user = User(full_name="Alice", email="alice@example.com", username="alice", password_hash="keep-this-hash",
                is_email_verified=True)
    db.session.add(user)
    db.session.commit()
    uid = user.id
    db.session.remove()
    return uid


# ------------------------------------------------------------------ the history itself
def test_there_is_exactly_one_head_and_no_branches():
    script = script_directory()
    assert script.get_heads() == [HEAD]
    for rev in script.walk_revisions():
        assert not isinstance(rev.down_revision, tuple), f"{rev.revision} is a merge"
    children = {}
    for rev in script.walk_revisions():
        if rev.down_revision:
            children.setdefault(rev.down_revision, []).append(rev.revision)
    assert all(len(kids) == 1 for kids in children.values()), "a revision has two children = a branch"


def test_the_historic_revision_ids_are_all_still_in_the_chain():
    ids = {r.revision for r in script_directory().walk_revisions()}
    assert {"b7e3d1a2c4f6", "c7e1a5d3f2b8", "c9a1e5f7b2d3", "d2a9b4e6f1c7", "d3b8f2a6c1e4",
            "e5b3c8a1d7f4", "f1a2b3c4d5e6"} <= ids          # a database stamped with any of them can still upgrade


# ------------------------------------------------------------------ fresh database
def test_fresh_database_reaches_head_and_matches_the_models_exactly(file_db):
    upgrade(directory=MIGRATIONS)
    assert versions(file_db) == [HEAD]
    for name, table in db.metadata.tables.items():
        assert name in tables(file_db), name
        assert {c.name for c in table.columns} == columns(file_db, name), name


def test_running_the_upgrade_again_changes_nothing(file_db):
    upgrade(directory=MIGRATIONS)
    uid = make_alice()
    upgrade(directory=MIGRATIONS)
    assert versions(file_db) == [HEAD]
    assert db.session.get(User, uid).password_hash == "keep-this-hash"


# ------------------------------------------------------------------ the production crash and its repair
SECOND_BRANCH_STATE = """
    alter table users drop column suspended_until;
    alter table users drop column session_epoch;
    alter table users drop column admin_role;
    alter table users add column status_until datetime;
    drop table moderation_notes;
    delete from alembic_version;
    insert into alembic_version values ('e5b3c8a1d7f4');
"""


def test_database_left_by_the_second_branch_crashes_the_user_loader_then_upgrades_cleanly(file_db):
    upgrade(directory=MIGRATIONS)
    uid = make_alice()
    sql(file_db, SECOND_BRANCH_STATE)

    with pytest.raises(OperationalError):                   # the exact production failure
        db.session.get(User, uid)
    db.session.rollback()

    upgrade(directory=MIGRATIONS)

    user = db.session.get(User, uid)                        # the user loader works again
    assert user.username == "alice" and user.password_hash == "keep-this-hash" and user.is_email_verified
    assert user.session_epoch == 0 and user.suspended_until is None and user.admin_role is None
    assert "moderation_notes" in tables(file_db)
    assert "status_until" in columns(file_db, "users")      # legacy column is left alone, never dropped
    assert versions(file_db) == [HEAD]


def test_database_stuck_at_the_push_step_upgrades_cleanly(file_db):
    upgrade(directory=MIGRATIONS)
    uid = make_alice()
    sql(file_db, """
        alter table users drop column suspended_until;
        alter table users drop column session_epoch;
        alter table users drop column admin_role;
        alter table push_deliveries drop column acked_at;
        drop table site_settings;
        drop table moderation_notes;
        drop table security_events;
        delete from alembic_version;
        insert into alembic_version values ('a4f8c2e1b9d0');
    """)
    upgrade(directory=MIGRATIONS)
    assert db.session.get(User, uid).username == "alice"
    for name, table in db.metadata.tables.items():
        assert {c.name for c in table.columns} <= columns(file_db, name), name
    assert versions(file_db) == [HEAD]


def test_database_stamped_with_both_old_heads_upgrades_cleanly(file_db):
    upgrade(directory=MIGRATIONS)
    uid = make_alice()
    sql(file_db, "delete from alembic_version; insert into alembic_version values ('e5b3c8a1d7f4'); "
                 "insert into alembic_version values ('f1a2b3c4d5e6');")
    upgrade(directory=MIGRATIONS)
    assert versions(file_db) == [HEAD]
    assert db.session.get(User, uid).username == "alice"


def test_site_settings_rows_survive_the_upgrade(file_db):
    upgrade(directory=MIGRATIONS)
    sql(file_db, "insert into site_settings(key, value, updated_at) values ('messaging_enabled', '0', '2026-01-01');"
                 "delete from alembic_version; insert into alembic_version values ('c7e1a5d3f2b8');")
    upgrade(directory=MIGRATIONS)
    con = sqlite3.connect(file_db)
    try:
        assert con.execute("select value from site_settings where key='messaging_enabled'").fetchone() == ("0",)
    finally:
        con.close()


# ------------------------------------------------------------------ call history hardening (c3d5e7f9a1b2)
def test_call_columns_added_and_existing_call_records_survive(file_db):
    from flask_migrate import downgrade

    upgrade(directory=MIGRATIONS, revision="b2c4e6f8a1d3")           # a database as it was before this change
    uid = make_alice()
    bob = User(full_name="Bob", email="bob@example.com", username="bob", password_hash="x", is_email_verified=True)
    db.session.add(bob)
    db.session.commit()
    bob_id = bob.id
    db.session.remove()
    con = sqlite3.connect(file_db)
    con.execute("insert into call_history(call_id, caller_id, receiver_id, call_type, status, started_at, answered_at, ended_at, "
                "duration_seconds, created_at, updated_at) values ('old-call-1', ?, 2, 'voice', 'completed', "
                "'2026-01-01 10:00:00', '2026-01-01 10:00:05', '2026-01-01 10:01:05', 60, '2026-01-01', '2026-01-01')".replace("?, 2,", "?, %d," % bob_id), (uid,))
    con.commit()
    con.close()
    assert "end_reason" not in columns(file_db, "call_history")

    upgrade(directory=MIGRATIONS)
    assert versions(file_db) == [HEAD]
    cols = columns(file_db, "call_history")
    assert {"ringing_at", "accepted_at", "ended_by_id", "end_reason"} <= set(cols)
    con = sqlite3.connect(file_db)
    try:
        assert con.execute("select status, duration_seconds, end_reason from call_history where call_id='old-call-1'").fetchone() == ("completed", 60, None)
        assert con.execute("select count(*) from users").fetchone()[0] == 2        # users untouched
    finally:
        con.close()

    downgrade(directory=MIGRATIONS, revision="b2c4e6f8a1d3")        # reversible, and still keeps the record
    assert "end_reason" not in columns(file_db, "call_history")
    upgrade(directory=MIGRATIONS)
    assert versions(file_db) == [HEAD]


def test_migration_graph_is_one_straight_line_ending_at_head():
    script = script_directory()
    revs = list(script.walk_revisions())
    ids = [r.revision for r in revs]
    assert len(ids) == len(set(ids)), "duplicate revision ids"
    assert script.get_heads() == [HEAD]
    assert sum(1 for r in revs if r.down_revision is None) == 1, "exactly one base"
    assert all(not isinstance(r.down_revision, tuple) for r in revs), "no merge revisions"
    assert {r.down_revision for r in revs if r.down_revision} <= set(ids), "no missing revisions"


# ------------------------------------------------------------------ native_devices (d4f6a8c0e2b3)
def test_native_devices_table_is_added_without_touching_existing_data_and_is_reversible(file_db):
    from flask_migrate import downgrade

    upgrade(directory=MIGRATIONS, revision="c3d5e7f9a1b2")           # a production database as it is today
    uid = make_alice()
    db.session.remove()
    assert not columns(file_db, "native_devices")
    con = sqlite3.connect(file_db)
    users_before = con.execute("select id, username from users").fetchall()
    push_cols_before = columns(file_db, "push_subscriptions")
    con.close()

    upgrade(directory=MIGRATIONS)
    assert versions(file_db) == [HEAD]
    cols = columns(file_db, "native_devices")
    assert {"user_id", "platform", "device_id", "push_token", "token_hash", "status"} <= set(cols)
    assert "native_devices" in sqlite_tables(file_db)
    con = sqlite3.connect(file_db)
    try:
        assert con.execute("select id, username from users").fetchall() == users_before
        con.execute("insert into native_devices(user_id, platform, device_id, status, failure_count, created_at, updated_at) "
                    "values (?, 'android', 'dev-abcdefgh', 'active', 0, '2026-01-01', '2026-01-01')", (uid,))
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):                   # one install id = one row
            con.execute("insert into native_devices(user_id, platform, device_id, status, failure_count, created_at, updated_at) "
                        "values (?, 'android', 'dev-abcdefgh', 'active', 0, '2026-01-01', '2026-01-01')", (uid,))
    finally:
        con.close()
    assert columns(file_db, "push_subscriptions") == push_cols_before    # browser push table is untouched

    downgrade(directory=MIGRATIONS, revision="c3d5e7f9a1b2")
    assert "native_devices" not in sqlite_tables(file_db)
    con = sqlite3.connect(file_db)
    assert con.execute("select id, username from users").fetchall() == users_before
    con.close()
    upgrade(directory=MIGRATIONS)
    assert versions(file_db) == [HEAD]
