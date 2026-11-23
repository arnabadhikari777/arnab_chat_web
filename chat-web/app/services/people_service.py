from sqlalchemy import func

from ..models import User

PER_PAGE = 20


def visible_users():
    """Accounts other people may see: verified and active."""
    return User.query.filter(User.is_email_verified.is_(True), User.status == "active")


def get_visible_user(username: str):
    return visible_users().filter(User.username == (username or "").strip().lower()).first()


def clean_search(q: str | None) -> str:
    return (q or "").strip().lstrip("@").lower()[:32]


def list_people(me: User, q: str | None, page: int):
    """Everyone except me, A-Z by name. Optional username search. Returns (users, has_next)."""
    query = visible_users().filter(User.id != me.id)
    term = clean_search(q)
    if term:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(User.username.like(f"%{escaped}%", escape="\\"))
    page = max(1, page)
    rows = (
        query.order_by(func.lower(User.full_name), User.id)
        .offset((page - 1) * PER_PAGE)
        .limit(PER_PAGE + 1)
        .all()
    )
    return rows[:PER_PAGE], len(rows) > PER_PAGE
