import io
import json
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import (Conversation, Message, MessageAttachment, MessageDeletion, Notification, User)
from app.realtime import get_backend
from app.utils.time import utcnow
from tests.conftest import login_as, verified

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 300
JPG = b"\xff\xd8\xff\xe0" + b"0" * 300
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 300
WEBM = b"\x1a\x45\xdf\xa3" + b"0" * 300
PDF = b"%PDF-1.4\n" + b"0" * 300
ZIP = b"PK\x03\x04" + b"0" * 300


@pytest.fixture()
def duo(app):
    """Alice and Bob (both public) already chatting, plus Cara who is outside the chat."""
    alice, bob, cara = verified("alice"), verified("bob"), verified("cara")
    a, b = login_as(app, alice), login_as(app, bob)
    a.post("/u/bob/message")
    conv = Conversation.query.one()
    return type("Duo", (), dict(a=a, b=b, c=login_as(app, cara), conv=conv.id, alice=alice, bob=bob,
                                 cara=cara, url=f"/api/conversations/{conv.id}"))


def send(browser, duo, text="hello", uuid="uuid-0001-aaaa"):
    return browser.post(f"{duo.url}/messages", json={"uuid": uuid, "body": text})


def file_post(browser, duo, data, name, uuid="file-0001-aaaa", **extra):
    form = {"uuid": uuid, "file": (io.BytesIO(data), name), **extra}
    return browser.post(f"{duo.url}/messages", data=form, content_type="multipart/form-data")


# ================================================================ text messages
def test_send_text_is_saved_and_returned(app, duo):
    res = send(duo.a, duo)
    assert res.status_code == 201
    m = res.get_json()["message"]
    assert (m["type"], m["body"], m["mine"], m["status"]) == ("text", "hello", True, "sent")
    row = Message.query.one()
    assert row.sender_id == duo.alice.id and row.conversation_id == duo.conv and row.status == "sent"


def test_history_survives_reopening_the_chat(app, duo):
    send(duo.a, duo, "one", "uuid-0001-aaaa")
    send(duo.b, duo, "two", "uuid-0002-bbbb")
    for browser in (duo.a, duo.b):
        data = browser.get(f"{duo.url}/messages").get_json()
        assert [m["body"] for m in data["messages"]] == ["one", "two"]
    mine = duo.a.get(f"{duo.url}/messages").get_json()["messages"]
    assert [m["mine"] for m in mine] == [True, False]


@pytest.mark.parametrize("body", ["", "   ", "\n\t"])
def test_empty_messages_are_refused(app, duo, body):
    res = send(duo.a, duo, body)
    assert res.status_code == 400 and Message.query.count() == 0


def test_too_long_message_refused(app, duo):
    app.config["MESSAGE_MAX_CHARS"] = 10
    assert send(duo.a, duo, "x" * 11).status_code == 400
    assert send(duo.a, duo, "x" * 10, "uuid-0002-bbbb").status_code == 201


def test_bad_uuid_refused(app, duo):
    for bad in ("", "a b", "x" * 80, "short"):
        assert send(duo.a, duo, "hi", bad).status_code == 400


def test_double_submit_creates_one_message(app, duo):
    first = send(duo.a, duo)
    second = send(duo.a, duo)                       # same uuid: a double tap or a retry
    assert first.status_code == 201 and second.status_code == 200
    assert second.get_json()["created"] is False
    assert second.get_json()["message"]["id"] == first.get_json()["message"]["id"]
    assert Message.query.count() == 1


def test_same_uuid_from_two_people_is_two_messages(app, duo):
    send(duo.a, duo, "a", "shared-uuid-1")
    send(duo.b, duo, "b", "shared-uuid-1")
    assert Message.query.count() == 2


def test_message_content_is_not_in_the_page_html(app, duo):
    send(duo.a, duo, "top secret words")
    html = duo.a.get(f"/chat/{duo.conv}").data.decode()
    assert "top secret words" not in html            # messages only come through the API


def test_pagination_of_history(app, duo):
    app.config["CHAT_PAGE_SIZE"] = 10
    for i in range(25):
        send(duo.a, duo, f"m{i}", f"uuid-{i:04d}-pppp")
    page1 = duo.a.get(f"{duo.url}/messages").get_json()
    assert len(page1["messages"]) == 10 and page1["has_more"] is True
    assert page1["messages"][-1]["body"] == "m24" and page1["messages"][0]["body"] == "m15"
    first_id = page1["messages"][0]["id"]
    page2 = duo.a.get(f"{duo.url}/messages?before={first_id}").get_json()
    assert page2["messages"][-1]["body"] == "m14" and page2["has_more"] is True
    page3 = duo.a.get(f"{duo.url}/messages?before={page2['messages'][0]['id']}").get_json()
    assert len(page3["messages"]) == 5 and page3["has_more"] is False


# ================================================================ who may use a conversation
def test_outsider_cannot_touch_the_conversation(app, duo):
    send(duo.a, duo)
    msg_id = Message.query.one().id
    for method, path, kw in [
        ("get", f"{duo.url}/messages", {}), ("get", f"{duo.url}/poll", {}),
        ("post", f"{duo.url}/messages", {"json": {"uuid": "uuid-9999-zzzz", "body": "hi"}}),
        ("post", f"{duo.url}/seen", {"json": {}}), ("post", f"{duo.url}/delivered", {"json": {"ids": [msg_id]}}),
        ("post", f"{duo.url}/typing", {"json": {"typing": True}}),
        ("post", f"/api/messages/{msg_id}/delete", {"json": {"scope": "me"}}),
        ("get", f"/chat/{duo.conv}", {}),
    ]:
        assert getattr(duo.c, method)(path, **kw).status_code == 404, path
    assert Message.query.count() == 1 and MessageDeletion.query.count() == 0


def test_api_needs_login_and_answers_in_json(client, duo):
    res = client.get(f"{duo.url}/messages")
    assert res.status_code == 401 and res.get_json()["ok"] is False
    assert client.get("/api/chats").status_code == 401 and client.get("/api/summary").status_code == 401


def test_unknown_conversation_is_404(app, duo):
    assert duo.a.get("/api/conversations/9999/messages").status_code == 404


# ================================================================ Sent / Delivered / Seen
def test_new_message_is_only_sent(app, duo):
    send(duo.a, duo)
    row = Message.query.one()
    assert (row.status, row.delivered_at, row.seen_at) == ("sent", None, None)


def test_sender_fetching_never_makes_it_delivered(app, duo):
    send(duo.a, duo)
    for _ in range(3):
        duo.a.get(f"{duo.url}/poll"); duo.a.get("/api/chats"); duo.a.get(f"{duo.url}/messages")
    assert Message.query.one().status == "sent"


def test_delivered_when_receivers_app_asks_for_its_chats(app, duo):
    send(duo.a, duo)
    duo.b.get("/api/chats")
    row = Message.query.one()
    assert row.status == "delivered" and row.delivered_at is not None


def test_delivered_by_explicit_acknowledgement(app, duo):
    send(duo.a, duo)
    mid = Message.query.one().id
    assert duo.b.post(f"{duo.url}/delivered", json={"ids": [mid]}).get_json()["updated"] == 1
    assert Message.query.one().status == "delivered"


def test_sender_cannot_mark_own_messages_delivered_or_seen(app, duo):
    send(duo.a, duo)
    mid = Message.query.one().id
    duo.a.post(f"{duo.url}/delivered", json={"ids": [mid]})
    duo.a.post(f"{duo.url}/seen", json={})
    assert Message.query.one().status == "sent"


def test_seen_when_receiver_views_the_conversation(app, duo):
    send(duo.a, duo)
    res = duo.b.post(f"{duo.url}/seen", json={})
    assert res.get_json()["updated"] == 1
    row = Message.query.one()
    assert row.status == "seen" and row.seen_at and row.delivered_at          # seen implies delivered


def test_status_never_goes_backwards(app, duo):
    send(duo.a, duo)
    mid = Message.query.one().id
    duo.b.post(f"{duo.url}/seen", json={})
    duo.b.get("/api/chats")
    duo.b.post(f"{duo.url}/delivered", json={"ids": [mid]})
    assert Message.query.one().status == "seen"


def test_seen_upto_only_covers_older_messages(app, duo):
    send(duo.a, duo, "1", "uuid-0001-aaaa"); send(duo.a, duo, "2", "uuid-0002-aaaa")
    first = Message.query.order_by(Message.id).first().id
    duo.b.post(f"{duo.url}/seen", json={"upto": first})
    assert [m.status for m in Message.query.order_by(Message.id)] == ["seen", "sent"]


def test_sender_sees_status_changes_through_polling(app, duo):
    send(duo.a, duo)
    mid = Message.query.one().id
    first = duo.a.get(f"{duo.url}/messages").get_json()
    since = first["server_time"]
    duo.b.post(f"{duo.url}/delivered", json={"ids": [mid]})
    poll = duo.a.get(f"{duo.url}/poll", query_string={"after": mid, "since": since}).get_json()
    assert {"id": mid, "status": "delivered", "deleted": False} in poll["updates"]
    since = poll["server_time"]
    duo.b.post(f"{duo.url}/seen", json={})
    poll = duo.a.get(f"{duo.url}/poll", query_string={"after": mid, "since": since}).get_json()
    assert poll["updates"][0]["status"] == "seen"
    quiet = duo.a.get(f"{duo.url}/poll", query_string={"after": mid, "since": poll["server_time"]}).get_json()
    assert quiet["updates"] == []


def test_poll_returns_only_new_messages(app, duo):
    send(duo.a, duo, "old", "uuid-0001-aaaa")
    last = Message.query.one().id
    send(duo.b, duo, "fresh", "uuid-0002-bbbb")
    data = duo.a.get(f"{duo.url}/poll", query_string={"after": last}).get_json()
    assert [m["body"] for m in data["messages"]] == ["fresh"]


# ================================================================ unread counts & chat list
def test_unread_counts_and_chat_list(app, duo):
    send(duo.a, duo, "1", "uuid-0001-aaaa"); send(duo.a, duo, "2", "uuid-0002-aaaa")
    chats = duo.b.get("/api/chats").get_json()
    assert chats["total_unread"] == 2 and chats["chats"][0]["unread"] == 2
    assert chats["chats"][0]["user"]["username"] == "alice" and chats["chats"][0]["last"]["body"] == "2"
    assert duo.a.get("/api/chats").get_json()["chats"][0]["unread"] == 0        # my own messages
    assert duo.b.get("/api/summary").get_json()["messages"] == 2
    duo.b.post(f"{duo.url}/seen", json={})
    assert duo.b.get("/api/summary").get_json()["messages"] == 0


def test_chat_list_is_sorted_by_recent_activity(app, duo):
    cara_browser = duo.c
    cara_browser.post("/u/alice/message")
    second = Conversation.query.filter(Conversation.id != duo.conv).one()
    send(duo.b, duo, "newer")                                   # activity in the first chat
    order = [c["id"] for c in duo.a.get("/api/chats").get_json()["chats"]]
    assert order == [duo.conv, second.id]
    cara_browser.post(f"/api/conversations/{second.id}/messages", json={"uuid": "uuid-0007-cccc", "body": "even newer"})
    order = [c["id"] for c in duo.a.get("/api/chats").get_json()["chats"]]
    assert order == [second.id, duo.conv]


def test_chat_with_no_messages_is_listed(app, duo):
    chats = duo.a.get("/api/chats").get_json()["chats"]
    assert len(chats) == 1 and chats[0]["last"] is None


def test_chat_list_hides_blocked_people(app, duo):
    duo.bob.status = "suspended"
    db.session.commit()
    assert duo.a.get("/api/chats").get_json()["chats"] == []
    assert duo.a.get(f"/chat/{duo.conv}").status_code == 404


def test_chat_list_shows_media_and_deleted_previews(app, duo):
    file_post(duo.a, duo, PNG, "p.png")
    last = duo.a.get("/api/chats").get_json()["chats"][0]["last"]
    assert last["type"] == "image" and last["mine"] is True
    mid = Message.query.one().id
    duo.a.post(f"/api/messages/{mid}/delete", json={"scope": "everyone"})
    last = duo.a.get("/api/chats").get_json()["chats"][0]["last"]
    assert last["deleted"] is True and last["body"] == ""


# ================================================================ deleting
def test_delete_for_me_hides_only_for_me(app, duo):
    send(duo.a, duo, "hello", "uuid-0001-aaaa"); send(duo.a, duo, "keep", "uuid-0002-aaaa")
    mid = Message.query.order_by(Message.id).first().id
    assert duo.b.post(f"/api/messages/{mid}/delete", json={"scope": "me"}).status_code == 200
    mine = [m["body"] for m in duo.b.get(f"{duo.url}/messages").get_json()["messages"]]
    theirs = [m["body"] for m in duo.a.get(f"{duo.url}/messages").get_json()["messages"]]
    assert mine == ["keep"] and theirs == ["hello", "keep"]
    row = db.session.get(Message, mid)
    assert row.deleted_for_all is False and row.body == "hello"


def test_delete_for_me_is_repeatable_and_sender_can_use_it_too(app, duo):
    send(duo.a, duo)
    mid = Message.query.one().id
    for _ in range(2):
        assert duo.a.post(f"/api/messages/{mid}/delete", json={"scope": "me"}).status_code == 200
    assert MessageDeletion.query.count() == 1
    assert duo.a.get(f"{duo.url}/messages").get_json()["messages"] == []
    assert len(duo.b.get(f"{duo.url}/messages").get_json()["messages"]) == 1


def test_deleted_for_me_message_is_not_unread(app, duo):
    send(duo.a, duo)
    mid = Message.query.one().id
    duo.b.post(f"/api/messages/{mid}/delete", json={"scope": "me"})
    assert duo.b.get("/api/summary").get_json()["messages"] == 0


def test_delete_for_everyone_by_sender(app, duo):
    send(duo.a, duo, "oops")
    mid = Message.query.one().id
    assert duo.a.post(f"/api/messages/{mid}/delete", json={"scope": "everyone"}).status_code == 200
    for browser in (duo.a, duo.b):
        m = browser.get(f"{duo.url}/messages").get_json()["messages"][0]
        assert m["deleted"] is True and m["body"] == "" and m["att"] is None
    assert db.session.get(Message, mid).body == ""          # gone from the database too


def test_only_sender_can_delete_for_everyone(app, duo):
    send(duo.a, duo, "mine")
    mid = Message.query.one().id
    res = duo.b.post(f"/api/messages/{mid}/delete", json={"scope": "everyone"})
    assert res.status_code == 403
    row = db.session.get(Message, mid)
    assert row.deleted_for_all is False and row.body == "mine"       # backend enforces it
    assert duo.c.post(f"/api/messages/{mid}/delete", json={"scope": "everyone"}).status_code == 404


def test_delete_everyone_reaches_the_other_person_by_polling(app, duo):
    send(duo.a, duo)
    mid = Message.query.one().id
    state = duo.b.get(f"{duo.url}/messages").get_json()
    duo.a.post(f"/api/messages/{mid}/delete", json={"scope": "everyone"})
    poll = duo.b.get(f"{duo.url}/poll", query_string={"after": mid, "since": state["server_time"]}).get_json()
    assert {"id": mid, "status": "sent", "deleted": True} in poll["updates"]


def test_deleted_message_is_never_counted_unread_or_marked_seen(app, duo):
    send(duo.a, duo)
    mid = Message.query.one().id
    duo.a.post(f"/api/messages/{mid}/delete", json={"scope": "everyone"})
    assert duo.b.get("/api/summary").get_json()["messages"] == 0
    assert duo.b.post(f"{duo.url}/seen", json={}).get_json()["updated"] == 0


def test_delete_everyone_removes_attachment_and_cloud_file(app, duo, fake_services):
    file_post(duo.a, duo, PNG, "p.png")
    mid = Message.query.one().id
    assert MessageAttachment.query.count() == 1
    duo.a.post(f"/api/messages/{mid}/delete", json={"scope": "everyone"})
    assert MessageAttachment.query.count() == 0 and fake_services.deleted == ["pid1"]


def test_bad_delete_scope(app, duo):
    send(duo.a, duo)
    mid = Message.query.one().id
    assert duo.a.post(f"/api/messages/{mid}/delete", json={"scope": "world"}).status_code == 400
    assert duo.a.post(f"/api/messages/{mid}/delete", json={}).status_code == 400
    assert duo.a.post("/api/messages/9999/delete", json={"scope": "me"}).status_code == 404


# ================================================================ media & voice
@pytest.mark.parametrize("data,name,kind,resource,folder", [
    (PNG, "photo.png", "image", "image", "goppo/chat/images"),
    (JPG, "photo.jpg", "image", "image", "goppo/chat/images"),
    (MP4, "clip.mp4", "video", "video", "goppo/chat/videos"),
    (PDF, "paper.pdf", "document", "raw", "goppo/chat/files"),
    (ZIP, "bundle.zip", "file", "raw", "goppo/chat/other"),
    (ZIP, "report.docx", "document", "raw", "goppo/chat/files"),
    (b"just some text", "notes.txt", "document", "raw", "goppo/chat/files"),
    (b"a,b\n1,2", "table.csv", "file", "raw", "goppo/chat/other"),
])
def test_media_types_go_to_the_right_place(app, duo, fake_services, data, name, kind, resource, folder):
    res = file_post(duo.a, duo, data, name)
    assert res.status_code == 201, res.get_json()
    m = res.get_json()["message"]
    assert m["type"] == kind and m["att"]["name"] == name and m["att"]["url"].startswith("https://res.cloudinary.com")
    opts = fake_services.uploads[0]
    assert (opts["resource_type"], opts["folder"]) == (resource, folder)
    att = MessageAttachment.query.one()
    assert att.size_bytes == len(data) and att.resource_type == resource and att.public_id == "pid1"


def test_voice_message_is_saved_with_duration(app, duo, fake_services):
    res = file_post(duo.a, duo, WEBM, "voice.webm", voice="1", duration="12.5")
    assert res.status_code == 201
    m = res.get_json()["message"]
    assert m["type"] == "voice" and m["att"]["dur"] == 12.5
    opts = fake_services.uploads[0]
    assert opts["resource_type"] == "video" and opts["folder"] == "goppo/chat/voice"   # audio lives under video


def test_voice_duration_is_clamped(app, duo):
    app.config["MAX_VOICE_SECONDS"] = 60
    m = file_post(duo.a, duo, WEBM, "voice.webm", voice="1", duration="99999").get_json()["message"]
    assert m["att"]["dur"] == 60
    m = file_post(duo.a, duo, WEBM, "voice.webm", uuid="file-0002-aaaa", voice="1", duration="nonsense").get_json()["message"]
    assert m["att"]["dur"] is None


@pytest.mark.parametrize("fmt,data,name", [("m4a", MP4, "voice.m4a"), ("ogg", b"OggS" + b"0" * 100, "voice.ogg")])
def test_other_voice_formats(app, duo, fmt, data, name):
    assert file_post(duo.a, duo, data, name, voice="1", duration="3").get_json()["message"]["type"] == "voice"


def test_caption_is_kept_for_images_but_not_voice(app, duo):
    img = file_post(duo.a, duo, PNG, "p.png", caption="  look at this ").get_json()["message"]
    assert img["body"] == "look at this"
    voice = file_post(duo.a, duo, WEBM, "v.webm", uuid="file-0002-aaaa", voice="1", caption="no").get_json()["message"]
    assert voice["body"] == ""


@pytest.mark.parametrize("data,name,voice", [
    (b"MZ" + b"0" * 100, "virus.exe", None),
    (b"text pretending", "fake.png", None),
    (b"text pretending", "fake.mp4", None),
    (b"text pretending", "fake.pdf", None),
    (b"text pretending", "fake.zip", None),
    (b"RIFF0000WAVE", "voice.exe", "1"),
    (b"not audio", "voice.webm", "1"),
    (PNG, "voice.png", "1"),
    (b"", "empty.png", None),
])
def test_bad_files_are_refused_and_nothing_is_uploaded(app, duo, fake_services, data, name, voice):
    extra = {"voice": voice} if voice else {}
    res = file_post(duo.a, duo, data, name, **extra)
    assert res.status_code == 400 and res.get_json()["ok"] is False
    assert fake_services.uploads == [] and Message.query.count() == 0


@pytest.mark.parametrize("kind,name,data", [("image", "p.png", PNG), ("video", "c.mp4", MP4),
                                            ("document", "d.pdf", PDF), ("file", "z.zip", ZIP)])
def test_size_limits_come_from_config(app, duo, fake_services, kind, name, data):
    app.config["UPLOAD_MAX_BYTES"] = {**app.config["UPLOAD_MAX_BYTES"], kind: 100}
    res = file_post(duo.a, duo, data, name)
    assert res.status_code == 400 and "limit" in res.get_json()["error"]["message"]
    assert fake_services.uploads == []


def test_allowed_extensions_come_from_config(app, duo):
    assert file_post(duo.a, duo, PNG, "p.gif").status_code == 400            # content is PNG, ext gif
    app.config["UPLOAD_ALLOWED_EXTENSIONS"] = {**app.config["UPLOAD_ALLOWED_EXTENSIONS"], "image": ["png"]}
    assert file_post(duo.a, duo, JPG, "p.jpg", uuid="file-0002-aaaa").status_code == 400
    assert file_post(duo.a, duo, PNG, "p.png", uuid="file-0003-aaaa").status_code == 201


def test_cloudinary_failure_saves_no_message(app, duo, monkeypatch):
    from app.services import cloudinary_service

    def boom(stream, **o):
        raise RuntimeError("down")

    monkeypatch.setattr(cloudinary_service.cloudinary.uploader, "upload", boom)
    res = file_post(duo.a, duo, PNG, "p.png")
    assert res.status_code == 400 and "could not upload" in res.get_json()["error"]["message"]
    assert Message.query.count() == 0 and "down" not in res.get_data(as_text=True)


def test_retrying_a_file_does_not_upload_twice(app, duo, fake_services):
    file_post(duo.a, duo, PNG, "p.png")
    again = file_post(duo.a, duo, PNG, "p.png")
    assert again.status_code == 200 and again.get_json()["created"] is False
    assert len(fake_services.uploads) == 1 and Message.query.count() == 1


def test_database_failure_after_upload_cleans_the_cloud_file(app, duo, fake_services, monkeypatch):
    from app.services import message_service

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(message_service, "_after_send", boom)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    res = file_post(duo.a, duo, PNG, "p.png")
    assert res.status_code == 500 and fake_services.deleted == ["pid1"]
    assert Message.query.count() == 0


def test_file_too_large_for_the_whole_request(app, duo):
    app.config["MAX_CONTENT_LENGTH"] = 1000
    res = file_post(duo.a, duo, PNG + b"0" * 5000, "p.png")
    assert res.status_code == 413 and res.get_json()["ok"] is False


# ================================================================ presence & typing
def test_online_status_and_last_seen(app, duo):
    duo.bob.last_seen_at = utcnow()
    db.session.commit()
    peer = duo.a.get(f"{duo.url}/messages").get_json()["peer"]
    assert peer["online"] is True and peer["last_seen"].endswith("Z")


def test_going_quiet_turns_offline_by_itself(app, duo):
    duo.bob.last_seen_at = utcnow() - timedelta(seconds=app.config["PRESENCE_TIMEOUT_SECONDS"] + 5)
    db.session.commit()
    peer = duo.a.get(f"{duo.url}/messages").get_json()["peer"]
    assert peer["online"] is False and peer["last_seen"]


def test_requests_keep_a_person_online(app, duo):
    duo.alice.last_seen_at = utcnow() - timedelta(minutes=10)
    db.session.commit()
    duo.a.get("/api/summary")
    assert duo.b.get(f"{duo.url}/messages").get_json()["peer"]["online"] is True


def test_never_seen_person_is_offline_with_no_last_seen(app, duo):
    duo.bob.last_seen_at = None
    db.session.commit()
    # (bob's own request below would set it, so ask before he does anything)
    peer = duo.a.get(f"{duo.url}/poll").get_json()["peer"]
    assert peer["online"] is False and peer["last_seen"] is None


def test_typing_indicator(app, duo):
    assert duo.a.get(f"{duo.url}/poll").get_json()["peer"]["typing"] is False
    duo.b.post(f"{duo.url}/typing", json={"typing": True})
    assert duo.a.get(f"{duo.url}/poll").get_json()["peer"]["typing"] is True
    assert duo.b.get(f"{duo.url}/poll").get_json()["peer"]["typing"] is False      # not about myself
    duo.b.post(f"{duo.url}/typing", json={"typing": False})
    assert duo.a.get(f"{duo.url}/poll").get_json()["peer"]["typing"] is False


def test_typing_expires_if_the_person_disconnects(app, duo, monkeypatch):
    import time as real_time
    from app.realtime import polling

    duo.b.post(f"{duo.url}/typing", json={"typing": True})
    now = real_time.monotonic()
    monkeypatch.setattr(polling.time, "monotonic", lambda: now + polling.TYPING_SECONDS + 1)
    assert duo.a.get(f"{duo.url}/poll").get_json()["peer"]["typing"] is False


def test_typing_is_per_conversation(app, duo):
    duo.c.post("/u/alice/message")
    other = Conversation.query.filter(Conversation.id != duo.conv).one()
    duo.c.post(f"/api/conversations/{other.id}/typing", json={"typing": True})
    assert duo.a.get(f"{duo.url}/poll").get_json()["peer"]["typing"] is False


def test_realtime_backend_is_replaceable(app):
    from app.realtime.base import RealtimeBackend

    backend = get_backend()
    assert isinstance(backend, RealtimeBackend) and backend.name == "polling"
    backend.publish("message", 1, {})                                  # push is a harmless no-op
    app.config["REALTIME_MODE"] = "socketio"
    assert get_backend().name == "polling"                              # falls back, never breaks


# ================================================================ message notices
def notices_for(user):
    return Notification.query.filter_by(user_id=user.id, type="new_message").all()


def test_one_notice_per_chat_with_a_counter(app, duo):
    send(duo.a, duo, "1", "uuid-0001-aaaa"); send(duo.a, duo, "2", "uuid-0002-aaaa"); send(duo.a, duo, "3", "uuid-0003-aaaa")
    notes = notices_for(duo.bob)
    assert len(notes) == 1 and notes[0].payload["count"] == 3 and notes[0].is_read is False
    assert notes[0].link == f"/chat/{duo.conv}"
    assert notices_for(duo.alice) == []


def test_message_notice_never_contains_message_text(app, duo):
    send(duo.a, duo, "my secret plan")
    note = notices_for(duo.bob)[0]
    assert "secret" not in json.dumps(note.payload) and set(note.payload) == {
        "conversation_id", "user_id", "username", "full_name", "count"}


def test_opening_the_chat_clears_the_notice_and_a_new_message_reopens_it(app, duo):
    send(duo.a, duo, "1", "uuid-0001-aaaa")
    duo.b.post(f"{duo.url}/seen", json={})
    note = notices_for(duo.bob)[0]
    assert note.is_read is True
    send(duo.a, duo, "2", "uuid-0002-aaaa")
    db.session.refresh(note)
    assert note.is_read is False and note.payload["count"] == 1 and len(notices_for(duo.bob)) == 1


def test_notice_can_be_switched_off(app, duo):
    duo.bob.notify_messages = False
    db.session.commit()
    send(duo.a, duo)
    assert notices_for(duo.bob) == []
