"""Notice Box: opening it reads everything; Clear All deletes only the signed-in person's own notices."""
from app.extensions import db
from app.models import Notification, PushDelivery, PushSubscription
from tests.conftest import login_as, verified


def add(user, key, read=False, type_="welcome"):
    n = Notification(user_id=user.id, type=type_, payload={}, dedupe_key=key, is_read=read)
    db.session.add(n)
    db.session.commit()
    return n


def pair(app):
    alice, bob = verified("alice"), verified("bob")
    return alice, bob, login_as(app, alice), login_as(app, bob)


# ------------------------------------------------------------------ auto read
def test_read_all_marks_every_unread_notice_of_this_user_only(app):
    alice, bob, a, b = pair(app)
    for i in range(5):
        add(alice, f"a{i}")
    add(bob, "b0")
    assert a.get("/api/summary").get_json()["notices"] == 5
    res = a.post("/api/notices/read-all", json={})
    assert res.get_json() == {"ok": True, "marked": 5, "notices": 0}
    assert a.get("/api/summary").get_json()["notices"] == 0
    assert Notification.query.filter_by(user_id=alice.id).count() == 5          # nothing deleted
    assert Notification.query.filter_by(user_id=bob.id, is_read=False).count() == 1


def test_notice_page_wires_up_read_all_and_clear_all(app):
    alice, bob, a, b = pair(app)
    add(alice, "a0")
    html = a.get("/notices").get_data(as_text=True)
    assert "/api/notices/read-all" in html and "/api/notices/clear-all" in html and 'id="notice-clear"' in html
    assert 'id="notice-clear"' not in b.get("/notices").get_data(as_text=True)    # nothing to clear: no button


def test_read_all_needs_login(client):
    assert client.post("/api/notices/read-all", json={}).status_code == 401


# ------------------------------------------------------------------ clear all
def test_clear_all_permanently_deletes_only_my_rows(app):
    alice, bob, a, b = pair(app)
    for i in range(3):
        add(alice, f"a{i}", read=bool(i % 2))
    for i in range(2):
        add(bob, f"b{i}")
    res = a.post("/api/notices/clear-all", json={})
    assert res.get_json() == {"ok": True, "deleted": 3, "notices": 0}
    assert Notification.query.filter_by(user_id=alice.id).count() == 0            # rows gone, not hidden
    assert Notification.query.filter_by(user_id=bob.id).count() == 2              # untouched
    assert a.get("/api/summary").get_json()["notices"] == 0
    assert b.get("/api/summary").get_json()["notices"] == 2


def test_clear_all_cannot_be_aimed_at_someone_else(app):
    alice, bob, a, b = pair(app)
    add(bob, "b0")
    for body in ({"user_id": bob.id}, {"user": bob.id, "ids": [1]}, {"all": True, "scope": "global"}):
        a.post("/api/notices/clear-all", json=body)
        a.post("/api/notices/clear-all?user_id=%d" % bob.id, json=body)
    assert Notification.query.filter_by(user_id=bob.id).count() == 1


def test_clear_all_needs_login_and_post(client, app):
    alice, bob, a, b = pair(app)
    add(alice, "a0")
    assert client.post("/api/notices/clear-all", json={}).status_code == 401
    assert a.get("/api/notices/clear-all").status_code == 405
    assert Notification.query.filter_by(user_id=alice.id).count() == 1


def test_clear_all_requires_csrf_when_enabled(app):
    alice, bob, a, b = pair(app)
    add(alice, "a0")
    app.config["WTF_CSRF_ENABLED"] = True
    assert a.post("/api/notices/clear-all", json={}).status_code in (400, 403)
    assert Notification.query.filter_by(user_id=alice.id).count() == 1


def test_clear_all_removes_the_push_records_of_those_notices_only(app):
    alice, bob, a, b = pair(app)
    sub_b = PushSubscription(user_id=bob.id, endpoint="https://p.example/b", p256dh="p", auth="a")
    sub_a = PushSubscription(user_id=alice.id, endpoint="https://p.example/a", p256dh="p", auth="a")
    db.session.add_all([sub_a, sub_b]); db.session.commit()
    na, nb = add(alice, "a0"), add(bob, "b0")
    db.session.add_all([PushDelivery(notification_id=na.id, subscription_id=sub_a.id, user_id=alice.id, group_key="x"),
                        PushDelivery(notification_id=nb.id, subscription_id=sub_b.id, user_id=bob.id, group_key="y")])
    db.session.commit()
    a.post("/api/notices/clear-all", json={})
    assert [d.user_id for d in PushDelivery.query.all()] == [bob.id]
    assert PushSubscription.query.count() == 2                                     # devices stay registered


def test_clear_all_on_an_empty_box_is_fine(app):
    alice, bob, a, b = pair(app)
    assert a.post("/api/notices/clear-all", json={}).get_json()["deleted"] == 0


def test_a_new_message_after_clearing_creates_a_fresh_notice(app):
    alice, bob, a, b = pair(app)
    a.post("/u/bob/message")
    from app.models import Conversation
    cid = Conversation.query.one().id
    a.post(f"/api/conversations/{cid}/messages", json={"uuid": "uuid-0001-aaaa", "body": "hi"})
    assert b.post("/api/notices/clear-all", json={}).get_json()["deleted"] == 1
    a.post(f"/api/conversations/{cid}/messages", json={"uuid": "uuid-0002-aaaa", "body": "again"})
    n = Notification.query.filter_by(user_id=bob.id).one()
    assert n.payload["count"] == 1 and n.is_read is False
