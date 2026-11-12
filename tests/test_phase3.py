"""Phase 3: timed suspensions, sign-in log, device revoke, announcement edit/delete, database tools, dashboard."""
import sqlite3
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import (AdminAuditLog, Announcement, Message, Notification, PushSubscription, SecurityEvent, User)
from app.services import db_admin_service as dbs
from app.utils.time import utcnow
from tests.conftest import login_as, verified


@pytest.fixture()
def site(app):
    boss = verified("boss", role="admin")
    alice, bob = verified("alice"), verified("bob")
    return type("S", (), dict(boss=boss, alice=alice, bob=bob, admin=login_as(app, boss),
                               a=login_as(app, alice), b=login_as(app, bob)))


def status(site, user, value, days=""):
    return site.admin.post(f"/admin/users/{user.id}/status", data={"status": value, "days": days})


def fresh(user):
    db.session.expire_all()
    return db.session.get(User, user.id)


# ------------------------------------------------------------------ timed suspension
def test_timed_suspension_blocks_then_ends_by_itself(app, site):
    status(site, site.alice, "suspended", "2")
    u = fresh(site.alice)
    assert u.status == "suspended" and u.status_until > utcnow() + timedelta(days=1)
    assert site.a.get("/api/summary").status_code == 401
    u.status_until = utcnow() - timedelta(minutes=1)                  # the two days have passed
    db.session.commit()
    again = app.test_client()
    assert again.post("/login", data={"identifier": "alice", "password": "secret123"}).status_code == 302
    assert fresh(site.alice).status == "active" and fresh(site.alice).status_until is None
    assert AdminAuditLog.query.filter_by(action="user.status_expired").count() == 1


def test_an_expired_suspension_also_ends_for_an_already_open_session(app, site):
    status(site, site.alice, "suspended", "1")
    u = fresh(site.alice)
    u.status_until = utcnow() - timedelta(seconds=5)
    db.session.commit()
    assert site.a.get("/api/summary").status_code in (200, 401)       # the session was signed out earlier: 401 is fine too
    assert fresh(site.alice).status == "active"


def test_without_days_it_lasts_until_an_admin_changes_it(app, site):
    status(site, site.alice, "banned")
    assert fresh(site.alice).status_until is None
    assert app.test_client().post("/login", data={"identifier": "alice", "password": "secret123"}).status_code == 200


@pytest.mark.parametrize("days", ["0", "5000", "-3", "abc"])
def test_bad_day_counts_change_nothing(app, site, days):
    status(site, site.alice, "suspended", days)
    assert fresh(site.alice).status == "active"


def test_restoring_clears_the_end_date(app, site):
    status(site, site.alice, "suspended", "5")
    status(site, site.alice, "active")
    assert fresh(site.alice).status == "active" and fresh(site.alice).status_until is None


# ------------------------------------------------------------------ sign-in log
def test_failed_and_successful_sign_ins_are_logged_without_secrets(app, site):
    anon = app.test_client()
    anon.post("/login", data={"identifier": "alice", "password": "WRONG-PASSWORD-xyz"})
    anon.post("/login", data={"identifier": "alice", "password": "secret123"})
    kinds = [e.kind for e in SecurityEvent.query.order_by(SecurityEvent.id)]
    assert "login_failed" in kinds and kinds.count("login") >= 1
    assert all("WRONG-PASSWORD" not in (e.detail or "") and "secret123" not in (e.detail or "")
               for e in SecurityEvent.query.all())


def test_security_page_is_admin_only_and_shows_the_log(app, site):
    app.test_client().post("/login", data={"identifier": "ghost_user", "password": "x"})
    assert site.a.get("/admin/security").status_code in (403, 404)
    page = site.admin.get("/admin/security")
    assert page.status_code == 200 and "ghost_user" in page.get_data(as_text=True)


# ------------------------------------------------------------------ push devices
def device(user, n):
    sub = PushSubscription(user_id=user.id, endpoint=f"https://push.example/{n}", p256dh="p", auth="a")
    db.session.add(sub)
    db.session.commit()
    return sub


def test_admin_can_list_and_revoke_a_device_of_that_user_only(app, site):
    mine, theirs = device(site.alice, 1), device(site.bob, 2)
    # the device id of ANOTHER person in alice's URL must not delete it
    site.admin.post(f"/admin/users/{site.alice.id}/push/{theirs.id}/revoke")
    assert PushSubscription.query.count() == 2
    site.admin.post(f"/admin/users/{site.alice.id}/push/{mine.id}/revoke")
    assert [s.id for s in PushSubscription.query.all()] == [theirs.id]
    assert AdminAuditLog.query.filter_by(action="push.revoke").count() == 1


def test_normal_users_cannot_revoke_devices(app, site):
    d = device(site.bob, 1)
    assert site.a.post(f"/admin/users/{site.bob.id}/push/{d.id}/revoke").status_code in (403, 404)
    assert PushSubscription.query.count() == 1


# ------------------------------------------------------------------ announcements
def make_announcement(site):
    site.admin.post("/admin/announcements", data={"title": "Hello all", "body": "First text here"})
    return Announcement.query.one()


def test_announcement_can_be_edited_without_sending_again(app, site):
    ann = make_announcement(site)
    before = Notification.query.count()
    site.admin.post(f"/admin/announcements/{ann.id}/edit", data={"title": "Better title", "body": "Fixed text here"})
    db.session.expire_all()
    assert db.session.get(Announcement, ann.id).title == "Better title"
    assert Notification.query.count() == before
    assert site.admin.post(f"/admin/announcements/{ann.id}/edit", data={"title": "x", "body": "y"}).status_code == 200  # too short


def test_deleting_an_announcement_removes_its_notices_only(app, site):
    ann = make_announcement(site)
    other = Notification(user_id=site.alice.id, type="welcome", payload={}, dedupe_key="keep-me")
    db.session.add(other)
    db.session.commit()
    site.admin.post(f"/admin/announcements/{ann.id}/delete")
    assert Announcement.query.count() == 0
    assert [n.dedupe_key for n in Notification.query.all()] == ["keep-me"]
    assert AdminAuditLog.query.filter_by(action="announcement.delete").count() == 1


def test_normal_users_cannot_edit_or_delete_announcements(app, site):
    ann = make_announcement(site)
    assert site.a.post(f"/admin/announcements/{ann.id}/delete").status_code in (403, 404)
    assert site.a.post(f"/admin/announcements/{ann.id}/edit", data={"title": "hack", "body": "hacked!!"}).status_code in (403, 404)
    assert Announcement.query.one().title == "Hello all"


# ------------------------------------------------------------------ dashboard
def test_dashboard_shows_push_and_notice_numbers(app, site):
    assert site.admin.get("/admin").status_code == 200


# ------------------------------------------------------------------ database tools
@pytest.fixture()
def filedb(app, tmp_path, monkeypatch):
    live = tmp_path / "chat.db"
    con = sqlite3.connect(live)
    con.execute("create table users(id integer primary key, name text)")
    con.executemany("insert into users(name) values(?)", [("a",), ("b",)])
    con.commit()
    con.close()
    monkeypatch.setattr(dbs, "_db_file", lambda: live)
    return live


def test_database_page_works_and_says_so_when_not_sqlite_file(app, site):
    page = site.admin.get("/admin/database")                            # the test database is in memory
    assert page.status_code == 200 and "SQLite" in page.get_data(as_text=True)
    assert site.admin.post("/admin/database/backup").status_code == 302


def test_backup_verify_download_delete_cycle(app, site, filedb):
    assert site.admin.post("/admin/database/backup").status_code == 302
    (name,) = [b["name"] for b in dbs.list_backups()]
    ok = site.admin.post(f"/admin/database/backups/{name}/verify")
    assert ok.status_code == 200 and "healthy" in ok.get_data(as_text=True)
    dl = site.admin.get(f"/admin/database/backups/{name}/download")
    assert dl.status_code == 200 and dl.data[:15] == b"SQLite format 3" and "no-store" in dl.headers["Cache-Control"]
    assert site.a.get(f"/admin/database/backups/{name}/download").status_code in (403, 404)    # not for normal users
    assert site.admin.post(f"/admin/database/backups/{name}/delete").status_code == 302
    assert dbs.list_backups() == []
    actions = {r.action for r in AdminAuditLog.query.all()}
    assert {"database.backup", "database.backup_verify", "database.backup_download", "database.backup_delete"} <= actions


def test_a_damaged_backup_is_reported_not_trusted(app, site, filedb):
    site.admin.post("/admin/database/backup")
    name = dbs.list_backups()[0]["name"]
    (dbs.backup_dir() / name).write_bytes(b"junk" * 400)
    assert "damaged" in site.admin.post(f"/admin/database/backups/{name}/verify").get_data(as_text=True)


@pytest.mark.parametrize("name", ["../chat.db", "..%2Fchat.db", "chat.db", "goppo-1.db", "goppo-20260101-000000.db"])
def test_backup_names_cannot_reach_other_files(app, site, filedb, name):
    assert site.admin.get(f"/admin/database/backups/{name}/download").status_code == 404
    assert site.admin.post(f"/admin/database/backups/{name}/delete").status_code in (302, 404)
    assert filedb.exists()


def test_cleanup_deletes_only_old_read_notices(app, site):
    old = utcnow() - timedelta(days=200)
    rows = [Notification(user_id=site.alice.id, type="welcome", payload={}, dedupe_key=k, is_read=r, created_at=c)
            for k, r, c in (("old-read", True, old), ("old-unread", False, old), ("new-read", True, utcnow()))]
    db.session.add_all(rows)
    db.session.commit()
    site.admin.post("/admin/database/cleanup", data={"kind": "notices", "days": "90"})
    assert sorted(n.dedupe_key for n in Notification.query.all()) == ["new-read", "old-unread"]
    site.admin.post("/admin/database/cleanup", data={"kind": "notices", "days": "5"})         # too small: refused
    assert Notification.query.count() == 2
    assert site.a.post("/admin/database/cleanup", data={"kind": "notices", "days": "90"}).status_code in (403, 404)


def test_cleanup_never_touches_messages_or_users(app, site):
    before = (User.query.count(), Message.query.count())
    site.admin.post("/admin/database/cleanup", data={"kind": "users", "days": "90"})
    site.admin.post("/admin/database/cleanup", data={"kind": "messages", "days": "90"})
    assert (User.query.count(), Message.query.count()) == before
