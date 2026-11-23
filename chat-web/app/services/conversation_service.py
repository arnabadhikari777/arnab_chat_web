from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Conversation


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def find_between(a: int, b: int):
    low, high = _pair(a, b)
    return Conversation.query.filter_by(user_low_id=low, user_high_id=high).first()


def get_or_create(a: int, b: int) -> Conversation:
    """Exactly one conversation per pair of users, even if two requests race."""
    conv = find_between(a, b)
    if conv:
        return conv
    low, high = _pair(a, b)
    try:
        with db.session.begin_nested():  # savepoint: a clash does not undo other work
            conv = Conversation(user_low_id=low, user_high_id=high)
            db.session.add(conv)
    except IntegrityError:
        conv = find_between(a, b)
    return conv


def get_for_member(user_id: int, conversation_id: int):
    """Return the conversation only if this user is one of its two people."""
    conv = db.session.get(Conversation, conversation_id)
    return conv if conv and conv.has_member(user_id) else None
