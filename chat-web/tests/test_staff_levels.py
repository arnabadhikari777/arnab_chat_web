"""Super admin / admin / moderator permissions."""
import pytest

from app.extensions import db
from app.models import AdminAuditLog, User
from app.services import permissions
from tests.conftest import login_as, verified


@pytest.fixture()
def team(app):
    boss, mod, adm, alice = verified("boss"), verified("mod"), verified("adm"), verified("alice")
    t = type("T", (), dict(boss=boss, mod=mod, adm=adm, alice=alice, root=login_as(app, boss),
                           a=login_as(app, alice)))
    t.root.post("/admin/staff", data={"username": "mod", "level": "moderator"})
    t.root.post("/admin/staff", data={"username": "adm", "level": "admin"})
    t.m, t.d = login_as(app, mod), login_as(app, adm)
    return t


def level(name):
    return User.query.filter_by(username=name).one().staff_level


def test_env_admin_is_super_admin_and_others_are_nobody(app, team):
    assert (level("boss"), level("mod"), level("adm"), level("alice")) == ("super_admin", "moderator", "admin", None)


def test_only_a_super_admin_can_manage_staff(app, team):
    for browser in (team.m, team.d, team.a):
        assert browser.get("/admin/staff").status_code == 403
        assert browser.post("/admin/staff", data={"username": "alice", "level": "admin"}).status_code == 403
    assert level("alice") is None


def test_nobody_can_promote_themselves(app, team):
    team.m.post("/admin/staff", data={"username": "mod", "level": "admin"})
    team.root.post("/admin/staff", data={"username": "boss", "level": "none"})
    assert level("mod") == "moderator" and level("boss") == "super_admin"


def test_env_super_admins_cannot_be_changed_from_the_website(app, team):
    second = verified("second_root")
    app.config["ADMIN_EMAILS"] = frozenset({"boss@example.com", second.email})
    team.root.post("/admin/staff", data={"username": "second_root", "level": "moderator"})
    assert User.query.filter_by(username="second_root").one().role == "user"       # refused: not changed


def test_unverified_or_banned_accounts_cannot_become_staff(app, team):
    verified("bad", ).status = "banned"
    db.session.commit()
    team.root.post("/admin/staff", data={"username": "bad", "level": "admin"})
    assert level("bad") is None
    team.root.post("/admin/staff", data={"username": "nobody_here", "level": "admin"})


def test_demotion_removes_access_immediately(app, team):
    assert team.d.get("/admin/settings").status_code == 200
    team.root.post("/admin/staff", data={"username": "adm", "level": "none"})
    assert team.d.get("/admin").status_code == 403
    assert level("adm") is None


def test_staff_changes_are_audited(app, team):
    assert AdminAuditLog.query.filter_by(action="staff.level").count() == 2


# ---------------------------------------------------------------- what each level may do
def test_moderator_pages(app, team):
    assert team.m.get("/admin").status_code == 200
    assert team.m.get("/admin/users").status_code == 200
    assert team.m.get("/admin/reports").status_code == 200
    for path in ("/admin/settings", "/admin/announcements", "/admin/audit", "/admin/staff"):
        assert team.m.get(path).status_code == 403, path


def test_admin_pages(app, team):
    for path in ("/admin", "/admin/users", "/admin/reports", "/admin/settings", "/admin/announcements", "/admin/audit"):
        assert team.d.get(path).status_code == 200, path
    assert team.d.get("/admin/staff").status_code == 403


def test_moderator_can_suspend_and_note_but_not_ban(app, team):
    uid = User.query.filter_by(username="alice").one().id
    assert team.m.post(f"/admin/users/{uid}/notes", data={"note": "Checked"}).status_code == 302
    team.m.post(f"/admin/users/{uid}/status", data={"status": "banned"})
    assert User.query.filter_by(username="alice").one().status == "active"
    team.m.post(f"/admin/users/{uid}/status", data={"status": "suspended", "days": "2"})
    assert User.query.filter_by(username="alice").one().status == "suspended"
    team.d.post(f"/admin/users/{uid}/status", data={"status": "banned"})
    assert User.query.filter_by(username="alice").one().status == "banned"
    team.m.post(f"/admin/users/{uid}/status", data={"status": "active"})                    # moderators cannot unban either
    assert User.query.filter_by(username="alice").one().status == "banned"


def test_moderator_cannot_touch_staff_accounts(app, team):
    uid = User.query.filter_by(username="adm").one().id
    team.m.post(f"/admin/users/{uid}/status", data={"status": "suspended"})
    assert User.query.filter_by(username="adm").one().status == "active"


def test_navigation_only_shows_what_the_level_may_open(app, team):
    html = team.m.get("/admin").get_data(as_text=True)
    assert "/admin/users" in html and "/admin/settings" not in html and "/admin/staff" not in html
    assert "/admin/staff" in team.root.get("/admin").get_data(as_text=True)


def test_every_admin_endpoint_has_an_explicit_permission(app):
    names = {r.endpoint for r in app.url_map.iter_rules() if r.endpoint.startswith("admin.")}
    assert names <= set(permissions.ENDPOINT_PERMS)


def test_unknown_admin_endpoint_defaults_to_the_highest_permission(app):
    assert permissions.required_for("admin.something_new") == "staff.manage"
    assert permissions.required_for(None) == "staff.manage"


def test_role_from_a_super_admin_survives_the_next_login(app, team):
    login_as(app, User.query.filter_by(username="mod").one())
    assert level("mod") == "moderator"
