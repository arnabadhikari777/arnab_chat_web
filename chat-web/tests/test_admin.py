import pytest
from flask import url_for

from app.extensions import db
from app.models import AdminAuditLog, Announcement, Notification, Report, User
from tests.conftest import login_as, verified


@pytest.fixture()
def site(app):
    boss = verified("boss")                    # boss@example.com is in ADMIN_EMAILS (testing config)
    alice, bob = verified("alice"), verified("bob", is_public=False)
    return type("S", (), dict(boss=boss, alice=alice, bob=bob, admin=login_as(app, boss),
                               a=login_as(app, alice), b=login_as(app, bob)))


# ================================================================ authorization
def admin_rules(app):
    return [r for r in app.url_map.iter_rules() if r.rule.startswith("/admin")]


def fill(rule):
    return rule.rule.replace("<int:user_id>", "1").replace("<int:report_id>", "1").replace("<int:announcement_id>", "1")


def test_admin_routes_exist(app):
    assert len(admin_rules(app)) >= 10


def test_every_admin_route_refuses_visitors_and_normal_users(app, client, site):
    for rule in admin_rules(app):
        path = fill(rule)
        for method in sorted(rule.methods & {"GET", "POST"}):
            visitor = getattr(client, method.lower())(path)
            normal = getattr(site.a, method.lower())(path)
            assert visitor.status_code == 302 and "/login" in visitor.headers["Location"], (method, path)
            assert normal.status_code == 403, (method, path)


def test_normal_user_cannot_change_anything_through_admin_posts(app, site):
    site.a.post(f"/admin/users/{site.bob.id}/status", data={"status": "banned"})
    site.a.post("/admin/announcements", data={"title": "Hello", "body": "World wide"})
    assert User.query.filter_by(username="bob").one().status == "active"
    assert Announcement.query.count() == 0 and AdminAuditLog.query.count() == 0


def test_admin_role_comes_only_from_the_env_list(app, site):
    assert User.query.filter_by(username="boss").one().role == "admin"
    assert User.query.filter_by(username="alice").one().role == "user"
    assert b"/admin" not in site.a.get("/").data and b"/admin" in site.admin.get("/").data


def test_admin_panel_is_not_in_a_normal_users_pages(app, site):
    for path in ("/", "/settings", "/people", "/notices", "/messages"):
        assert b'href="/admin' not in site.a.get(path).data


# ================================================================ dashboard & users
def test_dashboard_numbers(app, site):
    site.a.post("/u/bob/request")                                    # a pending request
    data = site.admin.get("/admin").data.decode()
    assert 'stat__value">3<' in data                                 # three accounts
    for label in ("All accounts", "Public accounts", "Private accounts", "Messages today", "Open reports"):
        assert label in data


def test_dashboard_counts_suspended_and_reports(app, site):
    site.admin.post(f"/admin/users/{site.alice.id}/status", data={"status": "suspended"})
    from app.services import admin_service
    s = admin_service.stats()
    assert s["users_suspended"] == 1 and s["users_public"] == 2 and s["users_private"] == 1


def test_user_list_search_and_filter(app, site):
    html = site.admin.get("/admin/users").data.decode()
    assert "@alice" in html and "@bob" in html and "@boss" in html
    assert "@bob" not in site.admin.get("/admin/users?q=ali").data.decode()
    assert "@alice" in site.admin.get("/admin/users?q=alice@example").data.decode()       # by email too
    site.admin.post(f"/admin/users/{site.bob.id}/status", data={"status": "suspended"})
    only = site.admin.get("/admin/users?status=suspended").data.decode()
    assert "@bob" in only and "@alice" not in only


def test_user_detail_shows_account_info(app, site):
    html = site.admin.get(f"/admin/users/{site.alice.id}").data.decode()
    assert "alice@example.com" in html and "Messages sent" in html and "Private" not in html.split("facts")[0]
    assert site.admin.get("/admin/users/9999").status_code == 404


def test_admin_never_sees_message_text(app, site):
    carol = verified("carol")                                  # a chat the admin is NOT part of
    site.a.post("/u/carol/message")
    from app.models import Conversation
    conv = Conversation.query.one()
    site.a.post(f"/api/conversations/{conv.id}/messages", json={"uuid": "uuid-0001-aaaa", "body": "very private words"})
    for path in ("/admin", f"/admin/users/{site.alice.id}", f"/admin/users/{carol.id}", "/admin/users", "/admin/audit"):
        assert b"very private words" not in site.admin.get(path).data
    assert site.admin.get(f"/api/conversations/{conv.id}/messages").status_code == 404   # not even an admin
    assert site.admin.get(f"/chat/{conv.id}").status_code == 404


# ================================================================ account status
def set_status(site, user, status):
    return site.admin.post(f"/admin/users/{user.id}/status", data={"status": status})


def test_suspend_logs_the_user_out_and_blocks_login(app, site):
    assert site.a.get("/").status_code == 200
    set_status(site, site.alice, "suspended")
    assert site.a.get("/").status_code == 302                                # signed out at once
    res = app.test_client().post("/login", data={"identifier": "alice", "password": "secret123"})
    assert res.status_code == 200 and b"suspended" in res.data


def test_ban_and_restore(app, site):
    set_status(site, site.alice, "banned")
    assert b"not available" in app.test_client().post("/login", data={"identifier": "alice", "password": "secret123"}).data
    set_status(site, site.alice, "active")
    assert login_as(app, site.alice).get("/").status_code == 200
    assert Notification.query.filter_by(user_id=site.alice.id, type="account_restored").count() == 1


def test_suspended_user_disappears_from_people(app, site):
    set_status(site, site.alice, "suspended")
    assert b"@alice" not in site.b.get("/people").data
    assert site.b.get("/u/alice").status_code == 404


def test_cannot_change_own_or_other_admin_status(app, site):
    res = set_status(site, site.boss, "banned")
    assert User.query.filter_by(username="boss").one().status == "active"
    assert b"cannot change your own" in site.admin.get(res.headers["Location"]).data


def test_bad_status_value(app, site):
    set_status(site, site.alice, "deleted")
    assert User.query.filter_by(username="alice").one().status == "active"


def test_status_change_is_logged(app, site):
    set_status(site, site.alice, "suspended")
    log = AdminAuditLog.query.one()
    assert log.action == "user.status" and log.target_id == site.alice.id and log.details == {"from": "active", "to": "suspended"}
    assert b"user.status" in site.admin.get("/admin/audit").data


def test_there_is_no_account_deletion_anywhere(app, site):
    for rule in app.url_map.iter_rules():
        assert "delete-account" not in rule.rule and "delete_account" not in (rule.endpoint or "")
    assert b"delete account" not in site.a.get("/settings").data.lower()


# ================================================================ announcements
def announce(site, **over):
    data = {"title": "Maintenance tonight", "body": "We update at 10 PM.", "title_bn": "", "body_bn": ""}
    data.update(over)
    return site.admin.post("/admin/announcements", data=data)


def test_announcement_reaches_the_right_people(app, site):
    site.bob.notify_announcements = False
    db.session.commit()
    res = announce(site)
    assert res.status_code == 302
    ann = Announcement.query.one()
    got = {n.user_id for n in Notification.query.filter_by(type="announcement")}
    assert got == {site.boss.id, site.alice.id}                               # bob switched them off
    assert all(n.payload == {"announcement_id": ann.id} for n in Notification.query.filter_by(type="announcement"))


def test_announcement_skips_suspended_and_unverified(app, site):
    site.alice.status = "suspended"
    verified("pending").is_email_verified = False
    db.session.commit()
    announce(site)
    got = {n.user_id for n in Notification.query.filter_by(type="announcement")}
    assert site.alice.id not in got and len(got) == 2


def test_announcement_shows_in_notice_box_and_opens(app, site):
    announce(site, title_bn="আজ রাতে রক্ষণাবেক্ষণ", body_bn="রাত ১০টায় আপডেট।")
    assert b"Maintenance tonight" in site.a.get("/notices").data
    note = Notification.query.filter_by(user_id=site.alice.id, type="announcement").one()
    res = site.a.get(f"/notices/{note.id}/go")
    page = site.a.get(res.headers["Location"]).data.decode()
    assert "We update at 10 PM." in page
    site.a.get("/lang/bn")
    assert "আজ রাতে রক্ষণাবেক্ষণ" in site.a.get("/notices").data.decode()
    assert "রাত ১০টায় আপডেট।" in site.a.get(res.headers["Location"]).data.decode()


def test_announcement_without_bengali_falls_back_to_english(app, site):
    announce(site)
    site.a.get("/lang/bn")
    assert "Maintenance tonight" in site.a.get("/notices").data.decode()


def test_announcement_validation(app, site):
    assert b"Title must be" in announce(site, title="x").data
    assert b"Message must be" in announce(site, body="y").data
    assert b"Title must be" in announce(site, title_bn="z" * 151).data
    assert Announcement.query.count() == 0 and Notification.query.filter_by(type="announcement").count() == 0


def test_announcement_html_is_escaped(app, site):
    announce(site, title="<b>Hi</b> there", body="<script>x</script>")
    note = Notification.query.filter_by(user_id=site.alice.id, type="announcement").one()
    html = site.a.get(f"/announcements/{note.payload['announcement_id']}").data
    assert b"<script>x</script>" not in html and b"&lt;script&gt;" in html


def test_toggle_announcement_and_audit(app, site):
    announce(site)
    ann = Announcement.query.one()
    site.admin.post(f"/admin/announcements/{ann.id}/toggle")
    db.session.refresh(ann)
    assert ann.is_active is False
    site.admin.post(f"/admin/announcements/{ann.id}/toggle")
    db.session.refresh(ann)
    assert ann.is_active is True
    assert [l.action for l in AdminAuditLog.query.order_by(AdminAuditLog.id)] == [
        "announcement.create", "announcement.toggle", "announcement.toggle"]
    assert Notification.query.filter_by(type="announcement", user_id=site.alice.id).count() == 1   # no re-send
    assert site.admin.post("/admin/announcements/9999/toggle").status_code == 404


def test_announcement_page_needs_login(client, site):
    announce(site)
    assert "/login" in client.get("/announcements/1").headers["Location"]
    assert site.a.get("/announcements/9999").status_code == 404


# ================================================================ reports
def make_report(site, reporter=None, reported=None, reason="spam"):
    r = Report(reporter_id=(reporter or site.alice).id, reported_user_id=(reported or site.bob).id,
               reason=reason, details="bad things")
    db.session.add(r)
    db.session.commit()
    return r


def test_reports_list_filter_and_detail(app, site):
    r1, r2 = make_report(site), make_report(site, reporter=site.bob, reported=site.alice, reason="fake")
    html = site.admin.get("/admin/reports").data.decode()
    assert "@bob" in html and "@alice" in html
    site.admin.post(f"/admin/reports/{r1.id}/status", data={"status": "dismissed"})
    open_only = site.admin.get("/admin/reports?status=open").data.decode()
    assert f"/admin/reports/{r2.id}" in open_only and f"/admin/reports/{r1.id}" not in open_only
    detail = site.admin.get(f"/admin/reports/{r2.id}").data.decode()
    assert "bad things" in detail and "Fake account" in detail
    assert site.admin.get("/admin/reports/9999").status_code == 404


def test_resolving_a_report_thanks_the_reporter_once(app, site):
    r = make_report(site)
    site.admin.post(f"/admin/reports/{r.id}/status", data={"status": "reviewing"})
    assert Notification.query.filter_by(type="report_update").count() == 0
    site.admin.post(f"/admin/reports/{r.id}/status", data={"status": "resolved"})
    site.admin.post(f"/admin/reports/{r.id}/status", data={"status": "resolved"})
    notes = Notification.query.filter_by(type="report_update").all()
    assert len(notes) == 1 and notes[0].user_id == site.alice.id
    db.session.refresh(r)
    assert r.status == "resolved" and r.handled_by == site.boss.id and r.handled_at
    assert "details" not in str(notes[0].payload)


def test_bad_report_status(app, site):
    r = make_report(site)
    site.admin.post(f"/admin/reports/{r.id}/status", data={"status": "nonsense"})
    db.session.refresh(r)
    assert r.status == "open"
    assert site.admin.post("/admin/reports/9999/status", data={"status": "resolved"}).status_code == 404


def test_report_flow_end_to_end(app, site):
    site.a.post("/u/bob/report", data={"reason": "harassment", "details": "rude"})
    assert b"Bullying or harassment" in site.admin.get("/admin/reports").data
    assert site.admin.get("/admin").data.decode().count('stat__value">1<') >= 1


# ================================================================ audit
def test_audit_log_page(app, site):
    assert b"No activity yet" in site.admin.get("/admin/audit").data
    set_status(site, site.alice, "suspended")
    assert b"user.status" in site.admin.get("/admin/audit").data
    assert site.admin.get("/admin/audit?page=zzz").status_code == 200


def test_admin_pages_work_in_bengali(app, site):
    site.admin.get("/lang/bn")
    for path in ("/admin", "/admin/users", f"/admin/users/{site.alice.id}", "/admin/reports",
                 "/admin/announcements", "/admin/audit"):
        html = site.admin.get(path).data.decode()
        assert "admin." not in html.replace("admin.js", "") or "/admin/" in html
        assert "অ্যাডমিন প্যানেল" in html
        for marker in ("admin.nav.", "admin.stat.", "admin.user.", "admin.ann.", "admin.report."):
            assert marker not in html, (path, marker)
