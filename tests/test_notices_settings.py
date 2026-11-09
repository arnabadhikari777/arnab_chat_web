import pytest

from app.extensions import db
from app.models import Notification, OtpVerification, Report, User
from app.utils.time import utcnow
from tests.conftest import login_as, verified


@pytest.fixture()
def pair(app):
    alice, bob = verified("alice", is_public=False), verified("bob")
    return type("P", (), dict(alice=alice, bob=bob, a=login_as(app, alice), b=login_as(app, bob)))


def note(user, type_="message_request_received", key=None, **kw):
    n = Notification(user_id=user.id, type=type_, payload={"full_name": "Zed"}, link="/people",
                     dedupe_key=key or f"k{Notification.query.count()}", **kw)
    db.session.add(n)
    db.session.commit()
    return n


# ================================================================ notice creation
def test_message_request_creates_notice_for_receiver_only(app, pair):
    pair.b.post("/u/alice/request")
    assert Notification.query.filter_by(user_id=pair.alice.id, type="message_request_received").count() == 1
    assert Notification.query.filter_by(user_id=pair.bob.id, type="message_request_received").count() == 0


def test_accept_and_reject_notify_the_sender(app, pair):
    pair.b.post("/u/alice/request")
    rid = pair.alice.id and __import__("app.models", fromlist=["MessageRequest"]).MessageRequest.query.one().id
    pair.a.post(f"/requests/{rid}/accept")
    assert Notification.query.filter_by(user_id=pair.bob.id, type="message_request_accepted").count() == 1


def test_welcome_notice_after_verification(client, fake_services):
    import io
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    client.post("/register", data={"full_name": "New Person", "email": "new@example.com", "username": "newbie",
                                   "password": "secret123", "confirm_password": "secret123", "account_type": "public",
                                   "photo": (io.BytesIO(png), "p.png")}, content_type="multipart/form-data")
    code = fake_services.sent[-1]["body"].split("is ")[1][:6]
    client.post("/verify-email", data={"code": code})
    assert Notification.query.filter_by(type="welcome").count() == 1


# ================================================================ Notice Box page
def test_notice_box_lists_notices_in_the_reader_language(app, pair):
    pair.b.post("/u/alice/request")
    html = pair.a.get("/notices").data.decode()
    assert "Bob sent you a message request." in html and "notice--unread" in html
    pair.a.get("/lang/bn")
    assert "মেসেজ রিকোয়েস্ট পাঠিয়েছেন" in pair.a.get("/notices").data.decode()


def test_notice_box_needs_login_and_shows_only_my_notices(client, app, pair):
    assert "/login" in client.get("/notices").headers["Location"]
    note(pair.alice, type_="welcome")
    assert b"notice--unread" not in pair.b.get("/notices").data


def test_notice_has_timestamp(app, pair):
    note(pair.alice, type_="welcome")
    assert b'data-ts="' in pair.a.get("/notices").data


def test_mark_all_as_read_in_one_click(app, pair):
    for i in range(5):
        note(pair.alice, type_="welcome", key=f"w{i}")
    note(pair.bob, type_="welcome", key="bobs")
    assert pair.a.get("/api/summary").get_json()["notices"] == 5
    res = pair.a.post("/notices/read-all")
    assert res.status_code == 302
    assert pair.a.get("/api/summary").get_json()["notices"] == 0
    assert Notification.query.filter_by(user_id=pair.alice.id).count() == 5      # history is kept
    assert Notification.query.filter_by(user_id=pair.bob.id, is_read=False).count() == 1   # not touching others
    assert b"notice--unread" not in pair.a.get("/notices").data


def test_mark_all_needs_post_and_login(client, app, pair):
    assert pair.a.get("/notices/read-all").status_code == 405
    assert "/login" in client.post("/notices/read-all").headers["Location"]


def test_clicking_a_notice_opens_its_page_and_marks_it_read(app, pair):
    pair.b.post("/u/alice/request")
    n = Notification.query.filter_by(user_id=pair.alice.id).one()
    res = pair.a.get(f"/notices/{n.id}/go")
    assert res.status_code == 302 and res.headers["Location"].endswith("/u/bob")
    db.session.refresh(n)
    assert n.is_read is True


def test_cannot_open_someone_elses_notice(app, pair):
    n = note(pair.alice, type_="welcome")
    assert pair.b.get(f"/notices/{n.id}/go").status_code == 404
    db.session.refresh(n)
    assert n.is_read is False


def test_notice_links_cannot_redirect_off_site(app, pair):
    n = note(pair.alice, type_="welcome")
    n.link = "https://evil.example/x"
    db.session.commit()
    assert "evil.example" not in pair.a.get(f"/notices/{n.id}/go").headers["Location"]


def test_notice_pagination_and_history(app, pair):
    for i in range(35):
        note(pair.alice, type_="welcome", key=f"w{i}")
    first = pair.a.get("/notices").data.decode()
    assert first.count("notice__icon") == 30 and "page=2" in first
    assert pair.a.get("/notices?page=2").data.decode().count("notice__icon") == 5
    assert pair.a.get("/notices?page=zzz").status_code == 200


def test_empty_notice_box(app, pair):
    assert b"No notices" in pair.a.get("/notices").data


def test_no_duplicate_notices_for_one_event(app, pair):
    pair.b.post("/u/alice/request")
    pair.b.post("/u/alice/request")
    assert Notification.query.filter_by(type="message_request_received").count() == 1


def test_summary_counts_unread_notices(app, pair):
    note(pair.alice, type_="welcome")
    assert pair.a.get("/api/summary").get_json() == {"messages": 0, "notices": 1}


# ================================================================ settings: notifications
def test_notification_settings(app, pair):
    pair.a.post("/settings/notifications", data={"notify_messages": "on"})
    user = User.query.filter_by(username="alice").one()
    assert (user.notify_messages, user.notify_announcements) == (True, False)
    pair.a.post("/settings/notifications", data={})
    db.session.refresh(user)
    assert (user.notify_messages, user.notify_announcements) == (False, False)
    html = pair.a.get("/settings").data.decode()
    assert "notify_messages" in html


def test_request_notices_cannot_be_switched_off(app, pair):
    pair.a.post("/settings/notifications", data={})
    pair.b.post("/u/alice/request")
    assert Notification.query.filter_by(user_id=pair.alice.id, type="message_request_received").count() == 1


# ================================================================ settings: change password
def start_change(browser, current="secret123"):
    return browser.post("/settings/password", data={"current_password": current})


def last_code(fake_services):
    return fake_services.sent[-1]["body"].split("is ")[1][:6]


def finish_change(browser, code, new="brandnew99", confirm=None):
    return browser.post("/settings/password/confirm",
                        data={"code": code, "password": new, "confirm_password": confirm or new})


def test_change_password_with_email_code(app, pair, fake_services):
    res = start_change(pair.a)
    assert res.status_code == 302 and "/password/confirm" in res.headers["Location"]
    assert fake_services.sent[-1]["to"] == "alice@example.com"
    res = finish_change(pair.a, last_code(fake_services))
    assert res.status_code == 302
    user = User.query.filter_by(username="alice").one()
    assert user.check_password("brandnew99") and not user.check_password("secret123")
    assert Notification.query.filter_by(user_id=user.id, type="password_changed").count() == 1
    login_as(app, user, "brandnew99")                       # the new password really works


def test_wrong_current_password_sends_no_code(app, pair, fake_services):
    res = start_change(pair.a, "wrong-one1")
    assert res.status_code == 200 and b"current password is wrong" in res.data
    assert fake_services.sent == [] and OtpVerification.query.count() == 0


def test_change_needs_the_right_code(app, pair, fake_services):
    start_change(pair.a)
    code = last_code(fake_services)
    wrong = "000000" if code != "000000" else "111111"
    assert b"tries left" in finish_change(pair.a, wrong).data
    assert User.query.filter_by(username="alice").one().check_password("secret123")


def test_change_password_validation(app, pair, fake_services):
    start_change(pair.a)
    code = last_code(fake_services)
    assert b"at least 8" in finish_change(pair.a, code, "short").data
    assert b"do not match" in finish_change(pair.a, code, "brandnew99", "other12345").data
    assert b"different from your current" in finish_change(pair.a, code, "secret123").data
    assert User.query.filter_by(username="alice").one().check_password("secret123")


def test_code_cannot_be_reused_for_change(app, pair, fake_services):
    start_change(pair.a)
    code = last_code(fake_services)
    finish_change(pair.a, code)
    start_change(pair.a, "brandnew99")               # a NEW code is issued; the old one is dead
    res = finish_change(pair.a, code, "another999")
    assert b"tries left" in res.data or b"wrong" in res.data
    assert User.query.filter_by(username="alice").one().check_password("brandnew99")


def test_confirm_page_needs_the_first_step(app, pair):
    assert "/settings/password" in pair.a.get("/settings/password/confirm").headers["Location"]
    assert pair.a.post("/settings/password/resend").status_code == 302


def test_change_password_resend_cooldown(app, pair, fake_services):
    start_change(pair.a)
    app.config["OTP_RESEND_COOLDOWN_SECONDS"] = 60
    res = pair.a.post("/settings/password/resend", follow_redirects=True)
    assert b"Please wait" in res.data and len(fake_services.sent) == 1


def test_register_code_cannot_change_a_password(app, pair, fake_services):
    from app.services import otp_service
    code, _ = otp_service.issue_otp("alice@example.com", "reset_password")
    start_change(pair.a)
    assert b"wrong" in finish_change(pair.a, code).data or b"tries left" in finish_change(pair.a, code).data
    assert User.query.filter_by(username="alice").one().check_password("secret123")


def test_change_password_pages_need_login(client):
    for path in ("/settings/password", "/settings/password/confirm"):
        assert "/login" in client.get(path).headers["Location"]
    assert "/login" in client.post("/settings/password", data={}).headers["Location"]


# ================================================================ reports
def test_report_a_person(app, pair):
    res = pair.a.post("/u/bob/report", data={"reason": "spam", "details": "sends junk"})
    assert res.status_code == 302
    r = Report.query.one()
    assert (r.reporter_id, r.reported_user_id, r.reason, r.status) == (pair.alice.id, pair.bob.id, "spam", "open")


def test_report_validation(app, pair):
    assert b"Choose a reason" in pair.a.post("/u/bob/report", data={"reason": "bad", "details": ""}).data
    assert b"up to 1000" in pair.a.post("/u/bob/report", data={"reason": "spam", "details": "x" * 1001}).data
    assert Report.query.count() == 0


def test_cannot_report_yourself_or_twice(app, pair):
    assert pair.a.get("/u/alice/report").status_code == 404
    pair.a.post("/u/bob/report", data={"reason": "spam"})
    res = pair.a.post("/u/bob/report", data={"reason": "fake"}, follow_redirects=True)
    assert b"already reported" in res.data and Report.query.count() == 1


def test_report_form_and_link(app, pair):
    assert b"/u/bob/report" in pair.a.get("/u/bob").data
    assert b"Bullying" in pair.a.get("/u/bob/report").data
    assert b"/u/alice/report" not in pair.a.get("/u/alice").data


def test_report_pages_need_login(client, pair):
    assert "/login" in client.get("/u/bob/report").headers["Location"]
