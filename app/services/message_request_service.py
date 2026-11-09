"""Who may talk to whom: public accounts directly, private accounts after a request."""
from dataclasses import dataclass

from flask import url_for
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import MessageRequest, User
from ..utils.time import utcnow
from . import conversation_service
from .errors import ServiceError
from .notification_service import add_notification


@dataclass
class Relationship:
    conversation: object = None
    sent_pending: object = None      # I asked them, waiting
    received_pending: object = None  # they asked me, I can answer


def _pending(sender_id: int, receiver_id: int):
    return MessageRequest.query.filter_by(
        sender_id=sender_id, receiver_id=receiver_id, status="pending"
    ).first()


def relationship(me: User, other: User) -> Relationship:
    return Relationship(
        conversation=conversation_service.find_between(me.id, other.id),
        sent_pending=_pending(me.id, other.id),
        received_pending=_pending(other.id, me.id),
    )


def _who(user: User) -> dict:
    return {"user_id": user.id, "username": user.username, "full_name": user.full_name}


def start_conversation(me: User, other: User):
    """Open the chat. Allowed if one exists, or if the other account is public."""
    if me.id == other.id:
        raise ServiceError("request.self")
    existing = conversation_service.find_between(me.id, other.id)
    if existing:
        return existing
    if not other.is_public:
        raise ServiceError("conversation.needs_request")
    conv = conversation_service.get_or_create(me.id, other.id)
    db.session.commit()
    return conv


def send_request(me: User, other: User) -> MessageRequest:
    if me.id == other.id:
        raise ServiceError("request.self")
    if conversation_service.find_between(me.id, other.id):
        raise ServiceError("request.already_chatting")
    if other.is_public:
        raise ServiceError("request.use_message")
    if _pending(other.id, me.id):
        raise ServiceError("request.they_sent")
    if _pending(me.id, other.id):
        raise ServiceError("request.already_pending")

    req = MessageRequest(sender_id=me.id, receiver_id=other.id)
    db.session.add(req)
    try:
        db.session.flush()
    except IntegrityError:  # a double tap got there first
        db.session.rollback()
        raise ServiceError("request.already_pending")
    add_notification(
        other.id, "message_request_received", {**_who(me), "request_id": req.id},
        url_for("people.profile", username=me.username), f"request:{req.id}:received",
    )
    db.session.commit()
    return req


def _answer(me: User, request_id: int, new_status: str) -> MessageRequest:
    """Only the receiver can answer, and only once (the update is atomic)."""
    changed = MessageRequest.query.filter_by(
        id=request_id, receiver_id=me.id, status="pending"
    ).update({"status": new_status, "responded_at": utcnow()})
    req = db.session.get(MessageRequest, request_id)
    if not changed:
        db.session.rollback()
        if req is None or req.receiver_id != me.id:
            raise ServiceError("request.not_found")
        raise ServiceError("request.not_pending")
    return req


def accept_request(me: User, request_id: int):
    req = _answer(me, request_id, "accepted")
    conv = conversation_service.get_or_create(req.sender_id, req.receiver_id)
    db.session.flush()
    add_notification(
        req.sender_id, "message_request_accepted", {**_who(me), "request_id": req.id},
        url_for("chat.room", conversation_id=conv.id), f"request:{req.id}:accepted",
    )
    db.session.commit()
    return conv


def reject_request(me: User, request_id: int) -> MessageRequest:
    req = _answer(me, request_id, "rejected")
    add_notification(
        req.sender_id, "message_request_rejected", {**_who(me), "request_id": req.id},
        url_for("people.profile", username=me.username), f"request:{req.id}:rejected",
    )
    db.session.commit()
    return req
