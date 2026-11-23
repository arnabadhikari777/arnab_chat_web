import io

import pytest

from app.extensions import db
from app.models import Conversation, MessageRequest, Notification, User
from tests.conftest import login_as, verified

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200


def img(name="a.png"):
    return (io.BytesIO(PNG), name)


@pytest.fixture()
def world(app):
    """Alice (public), Bob (public), Cara (private), Dan (private) + two browsers."""
    users = {
        "alice": verified("alice", is_public=True, bio="Hello from Alice"),
        "bob": verified("bob", is_public=True),
        "cara": verified("cara", is_public=False, bio="Private <b>bio</b>"),
        "dan": verified("dan", is_public=False),
    }
    return users


def as_(app, user):
    return login_as(app, user)


# ======================================================== People page
def test_people_requires_login(client):
    assert "/login" in client.get("/people").headers["Location"]


def test_people_lists_everyone_public_and_private_except_me(app, world):
    html = as_(app, world["alice"]).get("/people").data.decode()
    for name in ("Bob", "Cara", "Dan"):
        assert name in html
    assert "@alice" not in html            # not myself
    assert "Public" in html and "Private" in html


def test_people_hides_unverified_and_blocked_accounts(app, world):
    verified("ghost", is_public=True)
    User.query.filter_by(username="ghost").one().is_email_verified = False
    verified("banned_guy").status = "banned"
    verified("sus_guy").status = "suspended"
    db.session.commit()
    html = as_(app, world["alice"]).get("/people").data.decode()
    assert "@ghost" not in html and "@banned_guy" not in html and "@sus_guy" not in html


def test_people_cards_show_no_bio_and_no_email(app, world):
    html = as_(app, world["alice"]).get("/people").data.decode()
    assert "Private &lt;b&gt;bio" not in html and "Private <b>bio" not in html
    assert "@example.com" not in html


def test_people_sorted_by_name(app, world):
    html = as_(app, world["dan"]).get("/people").data.decode()
    assert html.index("Alice") < html.index("Bob") < html.index("Cara")


def test_search_finds_partial_username_case_insensitive_and_at_sign(app, world):
    c = as_(app, world["alice"])
    for q in ("ca", "CARA", "@cara", " ara "):
        html = c.get("/people", query_string={"q": q}).data.decode()
        assert "@cara" in html and "@bob" not in html


def test_search_treats_underscore_and_percent_literally(app, world):
    verified("a_b")
    verified("axb")
    c = as_(app, world["alice"])
    html = c.get("/people", query_string={"q": "a_b"}).data.decode()
    assert "@a_b" in html and "@axb" not in html
    assert "@bob" not in c.get("/people", query_string={"q": "%"}).data.decode()


def test_search_no_results_message(app, world):
    res = as_(app, world["alice"]).get("/people", query_string={"q": "zzzz"})
    assert b"No matching username" in res.data


def test_people_empty_state(app):
    only = verified("solo")
    assert b"No one else is here yet" in as_(app, only).get("/people").data


def test_people_pagination(app, world):
    for i in range(25):
        verified(f"user{i:02d}")
    c = as_(app, world["alice"])
    first = c.get("/people").data.decode()
    assert first.count('class="list-item"') == 20 and "page=2" in first
    second = c.get("/people?page=2").data.decode()
    assert second.count('class="list-item"') == 8 and "page=3" not in second and "page=1" in second
    assert c.get("/people?page=abc").status_code == 200      # junk page number is safe
    assert c.get("/people?page=-5").status_code == 200


# ======================================================== Profile view
def test_profile_shows_public_details_but_never_email(app, world):
    html = as_(app, world["alice"]).get("/u/cara").data.decode()
    assert "Cara" in html and "@cara" in html and "Private" in html
    assert "Private &lt;b&gt;bio&lt;/b&gt;" in html            # bio is escaped
    assert "cara@example.com" not in html


def test_profile_shows_photo_and_cover(app, world):
    world["cara"].avatar_url = "https://res.cloudinary.com/x/av.png"
    world["cara"].cover_url = "https://res.cloudinary.com/x/cv.png"
    db.session.commit()
    html = as_(app, world["alice"]).get("/u/cara").data.decode()
    assert "av.png" in html and "cv.png" in html


@pytest.mark.parametrize("path", ["/u/nobody", "/u/ghost2"])
def test_profile_404_for_unknown_or_hidden(app, world, path):
    verified("ghost2").status = "suspended"
    db.session.commit()
    assert as_(app, world["alice"]).get(path).status_code == 404


def test_profile_requires_login(client, world):
    assert "/login" in client.get("/u/alice").headers["Location"]


def test_profile_buttons_by_account_type(app, world):
    c = as_(app, world["alice"])
    assert b"/u/bob/message" in c.get("/u/bob").data           # public -> Message
    private = c.get("/u/cara").data
    assert b"/u/cara/request" in private and b"/u/cara/message" not in private
    assert b"Edit profile" in c.get("/u/alice").data            # my own profile


# ======================================================== Messaging a public account
def test_message_public_creates_one_conversation(app, world):
    c = as_(app, world["alice"])
    res = c.post("/u/bob/message")
    assert res.status_code == 302 and "/chat/" in res.headers["Location"]
    c.post("/u/bob/message")
    as_(app, world["bob"]).post("/u/alice/message")             # from the other side too
    conv = Conversation.query.one()
    assert conv.user_low_id < conv.user_high_id


def test_cannot_message_private_without_request(app, world):
    res = as_(app, world["alice"]).post("/u/cara/message", follow_redirects=True)
    assert b"Send a message request first" in res.data
    assert Conversation.query.count() == 0


def test_cannot_message_yourself(app, world):
    res = as_(app, world["alice"]).post("/u/alice/message", follow_redirects=True)
    assert b"own account" in res.data and Conversation.query.count() == 0


# ======================================================== Message requests
def test_request_to_private_creates_pending_row_and_notice(app, world):
    c = as_(app, world["alice"])
    res = c.post("/u/cara/request", follow_redirects=True)
    assert b"Request sent" in res.data
    req = MessageRequest.query.one()
    assert (req.sender_id, req.receiver_id, req.status) == (world["alice"].id, world["cara"].id, "pending")
    note = Notification.query.one()
    assert note.user_id == world["cara"].id and note.type == "message_request_received"
    assert note.payload["username"] == "alice" and note.link == "/u/alice"
    assert Conversation.query.count() == 0                      # no chat until accepted


def test_request_notice_has_no_message_content(app, world):
    as_(app, world["alice"]).post("/u/cara/request")
    note = Notification.query.one()
    assert set(note.payload) == {"user_id", "username", "full_name", "request_id"}


def test_receiver_sees_no_chat_before_accepting(app, world):
    as_(app, world["alice"]).post("/u/cara/request")
    assert Conversation.query.count() == 0
    assert as_(app, world["cara"]).get("/chat/1").status_code == 404


def test_duplicate_request_is_blocked(app, world):
    c = as_(app, world["alice"])
    c.post("/u/cara/request")
    res = c.post("/u/cara/request", follow_redirects=True)
    assert b"already sent a request" in res.data
    assert MessageRequest.query.count() == 1 and Notification.query.count() == 1
    assert b"Request sent" in c.get("/u/cara").data and b"/u/cara/request" not in c.get("/u/cara").data


def test_cannot_request_a_public_account_or_yourself(app, world):
    c = as_(app, world["alice"])
    assert b"public" in c.post("/u/bob/request", follow_redirects=True).data
    assert b"own account" in c.post("/u/alice/request", follow_redirects=True).data
    assert MessageRequest.query.count() == 0


def test_receiver_accepts_and_chat_opens(app, world):
    alice, cara = as_(app, world["alice"]), as_(app, world["cara"])
    alice.post("/u/cara/request")
    page = cara.get("/u/alice").data
    assert b"/accept" in page and b"/reject" in page
    req = MessageRequest.query.one()
    res = cara.post(f"/requests/{req.id}/accept")
    assert res.status_code == 302 and "/chat/" in res.headers["Location"]
    assert MessageRequest.query.one().status == "accepted"
    conv = Conversation.query.one()
    assert conv.has_member(world["alice"].id) and conv.has_member(world["cara"].id)
    note = Notification.query.filter_by(type="message_request_accepted").one()
    assert note.user_id == world["alice"].id and note.link == f"/chat/{conv.id}"
    # the sender can now open the chat, and the profile offers "Open chat"
    assert alice.get(f"/chat/{conv.id}").status_code == 200
    assert b"Open chat" in alice.get("/u/cara").data
    assert alice.post("/u/cara/message").status_code == 302


def test_only_the_receiver_can_answer(app, world):
    as_(app, world["alice"]).post("/u/cara/request")
    req = MessageRequest.query.one()
    assert as_(app, world["alice"]).post(f"/requests/{req.id}/accept").status_code == 404   # sender
    assert as_(app, world["dan"]).post(f"/requests/{req.id}/reject").status_code == 404     # stranger
    assert as_(app, world["cara"]).post("/requests/9999/accept").status_code == 404
    assert MessageRequest.query.one().status == "pending" and Conversation.query.count() == 0


def test_request_cannot_be_answered_twice(app, world):
    as_(app, world["alice"]).post("/u/cara/request")
    req = MessageRequest.query.one()
    cara = as_(app, world["cara"])
    cara.post(f"/requests/{req.id}/accept")
    res = cara.post(f"/requests/{req.id}/reject", follow_redirects=True)
    assert b"already answered" in res.data
    assert MessageRequest.query.one().status == "accepted"
    assert Notification.query.filter_by(type="message_request_accepted").count() == 1
    assert Notification.query.filter_by(type="message_request_rejected").count() == 0


def test_reject_notifies_sender_and_allows_new_request(app, world):
    alice, cara = as_(app, world["alice"]), as_(app, world["cara"])
    alice.post("/u/cara/request")
    first = MessageRequest.query.one()
    cara.post(f"/requests/{first.id}/reject")
    assert MessageRequest.query.one().status == "rejected" and Conversation.query.count() == 0
    note = Notification.query.filter_by(type="message_request_rejected").one()
    assert note.user_id == world["alice"].id
    # sender may try again
    assert b"/u/cara/request" in alice.get("/u/cara").data
    alice.post("/u/cara/request")
    statuses = sorted(r.status for r in MessageRequest.query.all())
    assert statuses == ["pending", "rejected"]
    assert Notification.query.filter_by(type="message_request_received").count() == 2


def test_if_they_already_asked_me_i_must_answer_instead(app, world):
    as_(app, world["cara"]).post("/u/dan/request")
    res = as_(app, world["dan"]).post("/u/cara/request", follow_redirects=True)
    assert b"already sent you a request" in res.data
    assert MessageRequest.query.count() == 1


def test_existing_chat_survives_a_switch_to_private(app, world):
    alice = as_(app, world["alice"])
    alice.post("/u/bob/message")
    bob_browser = as_(app, world["bob"])
    bob_browser.post("/settings/privacy", data={"account_type": "private"})
    assert User.query.filter_by(username="bob").one().is_public is False
    assert alice.post("/u/bob/message").status_code == 302       # still allowed
    assert b"Open chat" in alice.get("/u/bob").data
    assert Conversation.query.count() == 1


def test_new_people_cannot_message_after_switch_to_private(app, world):
    as_(app, world["bob"]).post("/settings/privacy", data={"account_type": "private"})
    res = as_(app, world["dan"]).post("/u/bob/message", follow_redirects=True)
    assert b"Send a message request first" in res.data


# ======================================================== Conversation access
def test_only_members_can_open_a_conversation(app, world):
    as_(app, world["alice"]).post("/u/bob/message")
    conv = Conversation.query.one()
    assert as_(app, world["alice"]).get(f"/chat/{conv.id}").status_code == 200
    assert as_(app, world["bob"]).get(f"/chat/{conv.id}").status_code == 200
    assert as_(app, world["cara"]).get(f"/chat/{conv.id}").status_code == 404
    assert as_(app, world["alice"]).get("/chat/9999").status_code == 404


def test_chat_requires_login(client, world):
    assert "/login" in client.get("/chat/1").headers["Location"]


def test_chat_page_shows_the_other_person_not_email(app, world):
    as_(app, world["alice"]).post("/u/bob/message")
    html = as_(app, world["alice"]).get("/chat/1").data.decode()
    assert "Bob" in html and "bob@example.com" not in html


# ======================================================== Edit profile
def edit(browser, **over):
    data = {"full_name": "Alice New", "bio": "New bio"}
    data.update(over)
    return browser.post("/settings/profile", data={k: v for k, v in data.items() if v is not None},
                        content_type="multipart/form-data", follow_redirects=False)


def test_edit_name_and_bio(app, world):
    c = as_(app, world["alice"])
    assert edit(c).status_code == 302
    user = User.query.filter_by(username="alice").one()
    assert (user.full_name, user.bio) == ("Alice New", "New bio")


def test_username_cannot_be_changed_through_the_form(app, world):
    c = as_(app, world["alice"])
    edit(c, username="hacker")
    assert User.query.filter_by(username="alice").count() == 1 and User.query.filter_by(username="hacker").count() == 0
    assert b'name="username"' not in c.get("/settings/profile").data      # no editable field


def test_edit_validation(app, world):
    c = as_(app, world["alice"])
    assert b"Enter your name" in edit(c, full_name="A").data
    assert b"up to 300" in edit(c, bio="x" * 301).data
    assert User.query.filter_by(username="alice").one().full_name == "Alice"


def test_new_photos_are_uploaded_and_old_ones_deleted(app, world, fake_services):
    alice = world["alice"]
    alice.avatar_public_id, alice.cover_public_id = "old-av", "old-cv"
    db.session.commit()
    c = as_(app, alice)
    edit(c, avatar=img("p.png"), cover=img("c.png"))
    user = User.query.filter_by(username="alice").one()
    folders = [u["folder"] for u in fake_services.uploads]
    assert folders == ["goppo/avatars", "goppo/covers"]
    assert user.avatar_url.startswith("https://res.cloudinary.com") and user.cover_url.startswith("https://res.cloudinary.com")
    assert sorted(fake_services.deleted) == ["old-av", "old-cv"]


def test_bad_cover_rejects_whole_save_and_uploads_nothing(app, world, fake_services):
    c = as_(app, world["alice"])
    res = edit(c, avatar=img(), cover=(io.BytesIO(b"not an image"), "c.png"))
    assert res.status_code == 200 and b"not a real picture" in res.data
    assert fake_services.uploads == []
    assert User.query.filter_by(username="alice").one().full_name == "Alice"


def test_upload_failure_keeps_old_profile(app, world, fake_services, monkeypatch):
    from app.services import cloudinary_service

    calls = []

    def flaky(stream, **o):
        calls.append(o["folder"])
        if len(calls) == 2:
            raise RuntimeError("down")
        return {"public_id": "new-av", "secure_url": "https://res.cloudinary.com/x/new.png"}

    monkeypatch.setattr(cloudinary_service.cloudinary.uploader, "upload", flaky)
    c = as_(app, world["alice"])
    res = edit(c, avatar=img(), cover=img())
    assert res.status_code == 200 and b"could not upload" in res.data
    user = User.query.filter_by(username="alice").one()
    assert user.avatar_url is None and user.full_name == "Alice"
    assert fake_services.deleted == ["new-av"]                   # the half-done upload was cleaned


def test_bio_is_escaped_on_profile(app, world):
    c = as_(app, world["alice"])
    edit(c, bio="<script>alert(1)</script>")
    html = as_(app, world["bob"]).get("/u/alice").data
    assert b"<script>alert(1)</script>" not in html


def test_edit_profile_requires_login(client):
    assert "/login" in client.get("/settings/profile").headers["Location"]
    assert "/login" in client.post("/settings/profile", data={}).headers["Location"]


# ======================================================== Settings: privacy
def test_privacy_can_be_changed_both_ways(app, world):
    c = as_(app, world["alice"])
    assert c.post("/settings/privacy", data={"account_type": "private"}).status_code == 302
    assert User.query.filter_by(username="alice").one().is_public is False
    c.post("/settings/privacy", data={"account_type": "public"})
    assert User.query.filter_by(username="alice").one().is_public is True


def test_privacy_rejects_bad_value(app, world):
    res = as_(app, world["alice"]).post("/settings/privacy", data={"account_type": "secret"})
    assert res.status_code == 400 and b"Choose Public or Private" in res.data
    assert User.query.filter_by(username="alice").one().is_public is True


def test_settings_page_shows_current_choice_and_language(app, world):
    html = as_(app, world["cara"]).get("/settings").data.decode()
    assert 'value="private" checked' in html and "English" in html and "বাংলা" in html


def test_private_user_changed_to_public_is_directly_messageable(app, world):
    as_(app, world["cara"]).post("/settings/privacy", data={"account_type": "public"})
    assert as_(app, world["alice"]).post("/u/cara/message").status_code == 302


def test_post_actions_need_login(client, world):
    for path in ("/u/bob/message", "/u/cara/request", "/requests/1/accept", "/requests/1/reject", "/settings/privacy"):
        assert "/login" in client.post(path).headers["Location"]


def test_dashboard_has_edit_link_and_nav(app, world):
    html = as_(app, world["alice"]).get("/").data.decode()
    assert "/settings/profile" in html and "/people" in html and "Hello from Alice" in html


def test_bengali_pages_have_no_missing_keys(app, world):
    c = as_(app, world["alice"])
    c.set_cookie("lang", "bn")
    for path in ("/people", "/u/cara", "/settings", "/settings/profile", "/"):
        html = c.get(path).data.decode()
        for marker in ("people.", "profile.", "action.", "settings.", "hint.", "field.", "flash."):
            assert marker not in html.replace("people.index", ""), (path, marker)
