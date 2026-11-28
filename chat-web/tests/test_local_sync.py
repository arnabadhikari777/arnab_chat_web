"""Task 2 backend: the change feed the Android local database syncs from, tombstones, idempotency, JSON login.

Everything here runs against the real Flask app (in-memory SQLite). Nothing here proves behaviour on a phone.
"""
import io
import sqlite3

import pytest
from flask_migrate import upgrade

from app.extensions import db
from app.models import Conversation, Message, MessageDeletion, SyncCounter
from app.services import message_service
from tests.conftest import login_as, verified
from tests.test_messaging import MP4, PNG, duo, file_post, send  # noqa: F401  (duo is a fixture)


def changes(browser, rev=0, mid=0, limit=200):
    res = browser.get(f"/api/sync/changes?rev={rev}&id={mid}&limit={limit}")
    assert res.status_code == 200, res.data
    return res.get_json()


def walk(browser, limit=2):
    """Read the whole feed page by page, the way the app's history restore does."""
    seen, cur = [], {"rev": 0, "id": 0}
    for _ in range(200):
        d = changes(browser, cur["rev"], cur["id"], limit)
        seen += d["messages"]
        cur = d["next"]
        if not d["has_more"]:
            return seen, cur
    raise AssertionError("feed never ended")


# ------------------------------------------------------------------ feed basics
def test_feed_returns_new_messages_with_revisions_in_order(app, duo):
    for i in range(5):
        send(duo.a, duo, f"m{i}", f"uuid-{i:04d}-aaaa")
    d = changes(duo.b)
    assert [m["body"] for m in d["messages"]] == [f"m{i}" for i in range(5)]
    revs = [m["rev"] for m in d["messages"]]
    assert revs == sorted(revs) and len(set(revs)) == 5 and min(revs) > 0
    assert d["head"] == max(revs) and d["has_more"] is False
    assert d["messages"][0]["conv"] == duo.conv and d["messages"][0]["sender"] == duo.alice.id


def test_paginated_history_has_no_gaps_and_no_duplicates(app, duo):
    for i in range(23):
        send(duo.a, duo, f"m{i}", f"uuid-{i:04d}-aaaa")
    seen, cur = walk(duo.b, limit=5)
    ids = [m["id"] for m in seen]
    assert len(ids) == 23 and len(set(ids)) == 23
    assert sorted(ids) == ids                                           # chronological for pure inserts
    # resuming from the end returns nothing new: "only changes" after the cursor
    d = changes(duo.b, cur["rev"], cur["id"])
    assert d["messages"] == [] and d["has_more"] is False


def test_a_bulk_status_change_does_not_split_across_pages(app, duo):
    for i in range(7):
        send(duo.a, duo, f"m{i}", f"uuid-{i:04d}-aaaa")
    seen, cur = walk(duo.a, limit=3)
    assert len(seen) == 7
    duo.b.post(f"{duo.url}/seen", json={})                              # one revision for all 7 rows
    after = []
    while True:
        d = changes(duo.a, cur["rev"], cur["id"], 3)
        after += d["messages"]
        cur = d["next"]
        if not d["has_more"]:
            break
    assert sorted(m["id"] for m in after) == sorted(m["id"] for m in seen)
    assert {m["status"] for m in after} == {"seen"}
    assert all(m["seen_at"] for m in after)


def test_delivered_and_read_reach_the_sender_through_the_feed(app, duo):
    send(duo.a, duo, "hi")
    base = changes(duo.a)["next"]
    from tests.test_messaging import deliver_by_push
    assert deliver_by_push(duo.b, duo).status_code == 200
    d = changes(duo.a, base["rev"], base["id"])
    assert [m["status"] for m in d["messages"]] == ["delivered"]
    base = d["next"]
    duo.b.post(f"{duo.url}/seen", json={})
    d = changes(duo.a, base["rev"], base["id"])
    assert [m["status"] for m in d["messages"]] == ["seen"]


def test_nothing_changes_means_no_new_revision(app, duo):
    send(duo.a, duo, "hi")
    duo.b.post(f"{duo.url}/seen", json={})
    head = message_service.sync_head()
    duo.b.post(f"{duo.url}/seen", json={})                              # nothing left to mark
    assert message_service.sync_head() == head


# ------------------------------------------------------------------ security
def test_feed_needs_login_and_never_leaks_other_conversations(app, duo):
    send(duo.a, duo, "private")
    anon = app.test_client()
    assert anon.get("/api/sync/changes").status_code == 401
    assert anon.get("/api/sync/head").status_code == 401
    assert changes(duo.c)["messages"] == []                              # Cara is in no conversation
    # Cara starts her own chat with Alice: Bob must not see it
    verified("dave")
    c2 = duo.c.post("/u/alice/message")
    assert c2.status_code in (200, 302)
    other = Conversation.query.filter(Conversation.id != duo.conv).first()
    if other is not None:
        duo.c.post(f"/api/conversations/{other.id}/messages", json={"uuid": "cara-0001-aaaa", "body": "secret"})
        assert all(m["body"] != "secret" for m in changes(duo.b)["messages"])


def test_bad_cursor_values_are_harmless(app, duo):
    send(duo.a, duo, "hi")
    res = duo.b.get("/api/sync/changes?rev=abc&id=-5&limit=99999")
    assert res.status_code == 200
    assert len(res.get_json()["messages"]) == 1


# ------------------------------------------------------------------ idempotent send
def test_sending_the_same_uuid_twice_creates_one_message_and_one_revision(app, duo):
    first = send(duo.a, duo, "once", "uuid-same-0001")
    head = message_service.sync_head()
    second = send(duo.a, duo, "once", "uuid-same-0001")
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.get_json()["created"] is False
    assert Message.query.count() == 1 and message_service.sync_head() == head
    assert first.get_json()["message"]["id"] == second.get_json()["message"]["id"]


# ------------------------------------------------------------------ delete for everyone
def test_delete_for_everyone_is_a_tombstone_in_the_feed_and_cannot_come_back(app, duo):
    m = send(duo.a, duo, "oops", "uuid-del-0001").get_json()["message"]
    base = changes(duo.b)["next"]
    assert duo.a.post(f"/api/messages/{m['id']}/delete", json={"scope": "everyone"}).status_code == 200
    d = changes(duo.b, base["rev"], base["id"])
    assert len(d["messages"]) == 1
    t = d["messages"][0]
    assert t["deleted"] is True and t["body"] == "" and t["att"] is None and t["deleted_at"]
    # a brand-new install restoring history gets the tombstone, never the text
    seen, _ = walk(duo.b)
    assert [x["body"] for x in seen] == [""] and seen[0]["deleted"] is True
    # retrying the same request is idempotent and creates no new revision
    head = message_service.sync_head()
    assert duo.a.post(f"/api/messages/{m['id']}/delete", json={"scope": "everyone"}).status_code == 200
    assert message_service.sync_head() == head


def test_only_the_sender_may_delete_for_everyone_and_outsiders_get_404(app, duo):
    m = send(duo.a, duo, "mine", "uuid-del-0002").get_json()["message"]
    assert duo.b.post(f"/api/messages/{m['id']}/delete", json={"scope": "everyone"}).status_code == 403
    assert duo.c.post(f"/api/messages/{m['id']}/delete", json={"scope": "everyone"}).status_code == 404
    assert Message.query.one().deleted_for_all is False
    assert Message.query.count() == 1                                     # nothing unrelated was removed


def test_delete_for_everyone_sends_a_wake_up_hint_to_the_other_person(app, duo, monkeypatch):
    calls = []
    from app.services import fcm_service
    monkeypatch.setattr(fcm_service, "deliver_sync_hint", lambda uid, reason, conv=None: calls.append((uid, reason, conv)) or 1)
    m = send(duo.a, duo, "bye", "uuid-del-0003").get_json()["message"]
    duo.a.post(f"/api/messages/{m['id']}/delete", json={"scope": "everyone"})
    assert calls == [(duo.bob.id, "deleted", duo.conv)]


def test_a_failing_hint_never_fails_the_delete(app, duo, monkeypatch):
    from app.services import fcm_service

    def boom(*a, **k):
        raise RuntimeError("fcm down")
    monkeypatch.setattr(fcm_service, "deliver_sync_hint", boom)
    m = send(duo.a, duo, "bye", "uuid-del-0004").get_json()["message"]
    assert duo.a.post(f"/api/messages/{m['id']}/delete", json={"scope": "everyone"}).status_code == 200
    assert Message.query.one().deleted_for_all is True


def test_the_exact_english_text(app):
    from app.i18n import t
    assert t("chat.deleted", lang="en") == "This message was deleted for everyone."


def test_delete_for_me_is_flagged_hidden_for_that_person_only(app, duo):
    m = send(duo.a, duo, "keep for alice", "uuid-del-0005").get_json()["message"]
    base_b = changes(duo.b)["next"]
    assert duo.b.post(f"/api/messages/{m['id']}/delete", json={"scope": "me"}).status_code == 200
    d = changes(duo.b, base_b["rev"], base_b["id"])
    assert d["messages"][0]["hidden"] is True and d["messages"][0]["body"] == ""
    seen_a, _ = walk(duo.a)
    assert seen_a[0]["body"] == "keep for alice" and seen_a[0]["hidden"] is False
    assert MessageDeletion.query.count() == 1


# ------------------------------------------------------------------ media metadata
def test_media_messages_carry_a_stable_id_a_url_and_a_thumbnail(app, duo):
    res = file_post(duo.a, duo, PNG, "pic.png", uuid="img-0001-aaaa")
    assert res.status_code == 201, res.data
    d = changes(duo.b)
    att = d["messages"][0]["att"]
    assert att["id"] and att["url"].startswith("https://res.cloudinary.com/")
    assert d["messages"][0]["type"] == "image"
    again = changes(duo.b)["messages"][0]["att"]["id"]
    assert again == att["id"]                                              # the id never changes: no duplicate downloads


@pytest.mark.parametrize("url,rtype,expect", [
    ("https://res.cloudinary.com/x/image/upload/v1/a/b.png", "image",
     "https://res.cloudinary.com/x/image/upload/c_limit,w_480,q_auto,f_auto/v1/a/b.png"),
    ("https://res.cloudinary.com/x/video/upload/v1/a/clip.mp4", "video",
     "https://res.cloudinary.com/x/video/upload/so_0,c_limit,w_480,q_auto/v1/a/clip.jpg"),
    ("https://res.cloudinary.com/x/raw/upload/v1/a/doc.pdf", "raw", None),
    ("https://example.com/pic.png", "image", None),
    (None, "image", None),
])
def test_thumbnail_url(url, rtype, expect):
    assert message_service.thumbnail_url(url, rtype) == expect


# ------------------------------------------------------------------ JSON login for the bundled app
def test_json_login_flow_csrf_me_logout(app):
    app.config["WTF_CSRF_ENABLED"] = True
    verified("nina")
    b = app.test_client()
    assert b.get("/api/auth/me").status_code == 401
    token = b.get("/api/auth/csrf").get_json()["token"]
    assert token
    bad = b.post("/api/auth/login", json={"identifier": "nina", "password": "nope-nope"}, headers={"X-CSRFToken": token})
    assert bad.status_code == 401 and bad.get_json()["ok"] is False
    no_csrf = b.post("/api/auth/login", json={"identifier": "nina", "password": "secret123"})
    assert no_csrf.status_code == 400                                       # CSRF protection is NOT exempt
    ok = b.post("/api/auth/login", json={"identifier": "nina", "password": "secret123"}, headers={"X-CSRFToken": token})
    assert ok.status_code == 200 and ok.get_json()["user"]["username"] == "nina"
    assert "password" not in ok.get_data(as_text=True)
    me = b.get("/api/auth/me")
    assert me.status_code == 200 and me.get_json()["user"]["username"] == "nina"


def test_json_login_for_unverified_account_asks_for_verification(app):
    from app.models import User
    u = User(full_name="Unv", email="unv@example.com", username="unv", is_email_verified=False)
    u.set_password("secret123")
    db.session.add(u)
    db.session.commit()
    res = app.test_client().post("/api/auth/login", json={"identifier": "unv", "password": "secret123"})
    assert res.status_code == 403
    assert res.get_json()["needs_verification"] is True


# ------------------------------------------------------------------ migration
def test_migration_backfills_revisions_and_keeps_every_row(tmp_path, monkeypatch):
    from app import config, create_app
    path = tmp_path / "chat.db"
    monkeypatch.setattr(config.TestingConfig, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{path}")
    app = create_app("testing")
    with app.app_context():
        upgrade(directory="migrations", revision="d4f6a8c0e2b3")
        con = sqlite3.connect(path)
        con.executescript("""
            insert into users (id, full_name, email, username, password_hash, bio, is_public, is_email_verified, status, role, language, created_at, updated_at)
              values (1,'A','a@x.io','a','h','',1,1,'active','user','en','2026-01-01','2026-01-01'),
                     (2,'B','b@x.io','b','h','',1,1,'active','user','en','2026-01-01','2026-01-01');
            insert into conversations (id, user_low_id, user_high_id, last_message_at, created_at) values (1,1,2,'2026-01-01','2026-01-01');
            insert into messages (id, conversation_id, sender_id, type, body, client_uuid, status, deleted_for_all, created_at)
              values (10,1,1,'text','one','uuid-aaaa-0001','sent',0,'2026-01-01'),
                     (11,1,2,'text','two','uuid-aaaa-0002','sent',0,'2026-01-02');
        """)
        con.commit(); con.close()
        db.session.remove(); db.engine.dispose()
        upgrade(directory="migrations")
        con = sqlite3.connect(path)
        rows = con.execute("select id, body, sync_rev from messages order by id").fetchall()
        counter = con.execute("select value from sync_counter where id = 1").fetchone()[0]
        con.close()
        assert rows == [(10, "one", 10), (11, "two", 11)]
        assert counter == 11
        db.session.remove(); db.engine.dispose()
