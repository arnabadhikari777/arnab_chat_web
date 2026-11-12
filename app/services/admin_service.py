"""Admin actions. Every change is written to the audit log. Admins never read chat text."""
from datetime import timedelta

from flask import current_app, url_for
from sqlalchemy import func, or_

from ..extensions import db
from ..models import (AdminAuditLog, Announcement, Conversation, Message, MessageRequest, ModerationNote,
                      Notification, PushDelivery, PushSubscription, Report, User)
from ..utils.time import utcnow
from . import permissions
from .errors import FieldErrors, ServiceError
from .notification_service import add_notification
from .presence_service import is_online

PAGE = 20
STATUSES = ("active", "suspended", "banned")
REPORT_STATUSES = ("open", "reviewing", "resolved", "dismissed")


def audit(admin: User, action: str, target_type: str | None = None, target_id: int | None = None,
          details: dict | None = None) -> None:
    db.session.add(AdminAuditLog(admin_id=admin.id, action=action, target_type=target_type,
                                 target_id=target_id, details=details or {}))


# ------------------------------------------------------------------ statistics
def expire_suspensions() -> int:
    """Turn finished timed suspensions back into active accounts (so lists and counts are right)."""
    n = (User.query.filter(User.status == "suspended", User.suspended_until.isnot(None),
                           User.suspended_until <= utcnow())
         .update({"status": "active", "suspended_until": None}, synchronize_session=False))
    if n:
        db.session.commit()
    return n


def recent_events(limit: int = 8):
    return AdminAuditLog.query.order_by(AdminAuditLog.id.desc()).limit(limit).all()


def stats() -> dict:
    expire_suspensions()
    now = utcnow()
    day, week = now - timedelta(days=1), now - timedelta(days=7)
    users = User.query
    online_cut = now - timedelta(seconds=30)
    return {
        "users_total": users.count(),
        "users_verified": users.filter(User.is_email_verified.is_(True)).count(),
        "users_public": users.filter(User.is_email_verified.is_(True), User.is_public.is_(True)).count(),
        "users_private": users.filter(User.is_email_verified.is_(True), User.is_public.is_(False)).count(),
        "users_suspended": users.filter(User.status == "suspended").count(),
        "users_banned": users.filter(User.status == "banned").count(),
        "users_new_day": users.filter(User.created_at > day).count(),
        "users_new_week": users.filter(User.created_at > week).count(),
        "online_now": users.filter(User.last_seen_at > online_cut).count(),
        "conversations": Conversation.query.count(),
        "messages_total": Message.query.count(),
        "messages_day": Message.query.filter(Message.created_at > day).count(),
        "requests_pending": MessageRequest.query.filter_by(status="pending").count(),
        "reports_open": Report.query.filter(Report.status.in_(("open", "reviewing"))).count(),
        "announcements_active": Announcement.query.filter_by(is_active=True).count(),
        "notices_unread": Notification.query.filter_by(is_read=False).count(),
        "push_devices": PushSubscription.query.filter_by(status="active").count(),
        "push_devices_bad": PushSubscription.query.filter(PushSubscription.status != "active").count(),
        "push_receipts_day": PushDelivery.query.filter(PushDelivery.acked_at > day).count(),
        "push_problems_day": PushDelivery.query.filter(PushDelivery.updated_at > day,
                                                       PushDelivery.status.notin_(("pending", "accepted"))).count(),
    }


# ------------------------------------------------------------------ users
def list_users(q: str | None, status: str | None, page: int, privacy: str | None = None):
    expire_suspensions()
    query = User.query
    if privacy in ("public", "private"):
        query = query.filter(User.is_public.is_(privacy == "public"))
    term = (q or "").strip().lower().lstrip("@")[:60]
    if term:
        esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{esc}%"
        query = query.filter(or_(User.username.like(like, escape="\\"),
                                 User.email.like(like, escape="\\"),
                                 func.lower(User.full_name).like(like, escape="\\")))
    if status in STATUSES:
        query = query.filter(User.status == status)
    page = max(1, page)
    rows = query.order_by(User.id.desc()).offset((page - 1) * PAGE).limit(PAGE + 1).all()
    return rows[:PAGE], len(rows) > PAGE


def user_detail(user: User) -> dict:
    return {
        "messages_sent": Message.query.filter_by(sender_id=user.id).count(),
        "conversations": Conversation.query.filter(
            or_(Conversation.user_low_id == user.id, Conversation.user_high_id == user.id)).count(),
        "reports_against": Report.query.filter_by(reported_user_id=user.id).count(),
        "reports_made": Report.query.filter_by(reporter_id=user.id).count(),
        "online": is_online(user),
    }


MAX_SUSPEND_DAYS = 365


def _days(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        days = int(value)
    except (TypeError, ValueError):
        raise ServiceError("admin.bad_days")
    if not 1 <= days <= MAX_SUSPEND_DAYS:
        raise ServiceError("admin.bad_days")
    return days


def set_status(admin: User, target: User, status: str, days=None) -> None:
    """days only matters for "suspended": the suspension then ends by itself after that many days."""
    if status not in STATUSES:
        raise ServiceError("admin.bad_status")
    days = _days(days) if status == "suspended" else None
    if target.id == admin.id:
        raise ServiceError("admin.not_self")
    if target.is_admin:
        raise ServiceError("admin.not_admin")
    if (status == "banned" or target.status == "banned") and not permissions.has(admin, "users.ban"):
        raise ServiceError("admin.no_permission")          # moderators cannot ban or unban
    until = utcnow() + timedelta(days=days) if days else None
    if target.status == status and (status != "suspended" or target.suspended_until == until):
        return
    old = target.status
    target.status = status
    target.suspended_until = until
    audit(admin, "user.status", "user", target.id, {"from": old, "to": status, "days": days})
    if status == "active":
        add_notification(target.id, "account_restored", {}, None, f"restored:{target.id}:{utcnow().timestamp()}")
    db.session.commit()


# ------------------------------------------------------------------ announcements
def create_announcement(admin: User, form) -> Announcement:
    title = (form.get("title") or "").strip()
    body = (form.get("body") or "").strip()
    title_bn = (form.get("title_bn") or "").strip() or None
    body_bn = (form.get("body_bn") or "").strip() or None
    errors = {}
    if not 3 <= len(title) <= 150:
        errors["title"] = ("err.ann_title", {})
    if not 3 <= len(body) <= 3000:
        errors["body"] = ("err.ann_body", {})
    if title_bn and len(title_bn) > 150:
        errors["title_bn"] = ("err.ann_title", {})
    if body_bn and len(body_bn) > 3000:
        errors["body_bn"] = ("err.ann_body", {})
    if errors:
        raise FieldErrors(errors)

    ann = Announcement(title=title, body=body, title_bn=title_bn, body_bn=body_bn,
                       is_active=True, created_by=admin.id)
    db.session.add(ann)
    db.session.flush()
    recipients = User.query.filter(User.is_email_verified.is_(True), User.status == "active",
                                   User.notify_announcements.is_(True)).all()
    now = utcnow()
    db.session.bulk_insert_mappings(Notification, [
        {"user_id": u.id, "type": "announcement", "payload": {"announcement_id": ann.id},
         "link": url_for("notices.announcement", announcement_id=ann.id), "is_read": False,
         "dedupe_key": f"announcement:{ann.id}", "created_at": now}
        for u in recipients
    ])
    audit(admin, "announcement.create", "announcement", ann.id, {"recipients": len(recipients)})
    db.session.commit()
    # Best-effort push for announcement notices (capped to avoid long request on free hosts).
    try:
        from . import push_service
        if push_service.vapid_configured() and recipients:
            # Inline pushes would hold this request open, so only the first few go out now. Everyone
            # still gets the notice in their Notice Box (and the bell badge).
            notes = (
                Notification.query.filter_by(type="announcement", dedupe_key=f"announcement:{ann.id}")
                .limit(int(current_app.config.get("PUSH_ANNOUNCEMENT_INLINE_LIMIT", 40)))
                .all()
            )
            for note in notes:
                push_service.notify_after_commit(note)
    except Exception:  # noqa: BLE001
        pass
    return ann


def toggle_announcement(admin: User, announcement_id: int) -> Announcement:
    ann = db.session.get(Announcement, announcement_id)
    if ann is None:
        raise ServiceError("admin.not_found")
    ann.is_active = not ann.is_active
    audit(admin, "announcement.toggle", "announcement", ann.id, {"active": ann.is_active})
    db.session.commit()
    return ann


# ------------------------------------------------------------------ reports
def list_reports(status: str | None, page: int):
    q = Report.query
    if status in REPORT_STATUSES:
        q = q.filter(Report.status == status)
    page = max(1, page)
    rows = q.order_by(Report.id.desc()).offset((page - 1) * PAGE).limit(PAGE + 1).all()
    return rows[:PAGE], len(rows) > PAGE


def set_report_status(admin: User, report_id: int, status: str) -> Report:
    report = db.session.get(Report, report_id)
    if report is None:
        raise ServiceError("admin.not_found")
    if status not in REPORT_STATUSES:
        raise ServiceError("admin.bad_status")
    old = report.status
    report.status = status
    report.handled_by = admin.id
    report.handled_at = utcnow()
    audit(admin, "report.status", "report", report.id, {"from": old, "to": status})
    if status in ("resolved", "dismissed") and old != status:
        add_notification(report.reporter_id, "report_update", {"report_id": report.id}, None,
                         f"report:{report.id}:{status}")
    db.session.commit()
    return report


def list_audit(page: int):
    page = max(1, page)
    rows = (AdminAuditLog.query.order_by(AdminAuditLog.id.desc())
            .offset((page - 1) * PAGE).limit(PAGE + 1).all())
    return rows[:PAGE], len(rows) > PAGE


# ------------------------------------------------------------------ moderation notes, history, sessions
def add_note(admin: User, target: User, text: str) -> ModerationNote:
    text = (text or "").strip()
    if not 2 <= len(text) <= 500:
        raise ServiceError("admin.bad_note")
    note = ModerationNote(user_id=target.id, admin_id=admin.id, note=text)
    db.session.add(note)
    audit(admin, "user.note", "user", target.id, {})       # the note text stays out of the log
    db.session.commit()
    return note


def notes_for(target: User, limit: int = 30):
    return ModerationNote.query.filter_by(user_id=target.id).order_by(ModerationNote.id.desc()).limit(limit).all()


def history_for(target: User, limit: int = 20):
    return (AdminAuditLog.query.filter_by(target_type="user", target_id=target.id)
            .order_by(AdminAuditLog.id.desc()).limit(limit).all())


def force_logout(admin: User, target: User) -> None:
    """Sign the person out on every device. They can sign in again with their password."""
    if target.id == admin.id:
        raise ServiceError("admin.not_self")
    if target.is_admin:
        raise ServiceError("admin.not_admin")
    target.session_epoch = (target.session_epoch or 0) + 1
    audit(admin, "user.force_logout", "user", target.id, {})
    db.session.commit()


# ------------------------------------------------------------------ announcements: edit / delete
def _announcement_fields(form) -> dict:
    title = (form.get("title") or "").strip()
    body = (form.get("body") or "").strip()
    title_bn = (form.get("title_bn") or "").strip() or None
    body_bn = (form.get("body_bn") or "").strip() or None
    errors = {}
    if not 3 <= len(title) <= 150:
        errors["title"] = ("err.ann_title", {})
    if not 3 <= len(body) <= 3000:
        errors["body"] = ("err.ann_body", {})
    if title_bn and len(title_bn) > 150:
        errors["title_bn"] = ("err.ann_title", {})
    if body_bn and len(body_bn) > 3000:
        errors["body_bn"] = ("err.ann_body", {})
    if errors:
        raise FieldErrors(errors)
    return {"title": title, "body": body, "title_bn": title_bn, "body_bn": body_bn}


def update_announcement(admin: User, announcement_id: int, form) -> Announcement:
    ann = db.session.get(Announcement, announcement_id)
    if ann is None:
        raise ServiceError("admin.not_found")
    for key, value in _announcement_fields(form).items():
        setattr(ann, key, value)
    audit(admin, "announcement.edit", "announcement", ann.id, {})
    db.session.commit()
    return ann


def delete_announcement(admin: User, announcement_id: int) -> None:
    """Removes the announcement AND the notices already sent to people's Notice Box."""
    ann = db.session.get(Announcement, announcement_id)
    if ann is None:
        raise ServiceError("admin.not_found")
    removed = (Notification.query.filter_by(type="announcement", dedupe_key=f"announcement:{ann.id}")
               .delete(synchronize_session=False))
    db.session.delete(ann)
    audit(admin, "announcement.delete", "announcement", announcement_id, {"notices_removed": removed})
    db.session.commit()


# ------------------------------------------------------------------ staff levels (super admin only)
def list_staff():
    return User.query.filter(User.role == "admin").order_by(User.id).all()


def set_staff_level(admin: User, username: str, level: str) -> User:
    """Give someone the admin or moderator level, or take it away (level "none").

    Only super admins reach this (route permission, re-checked here). The env-listed super admins and
    yourself cannot be changed, so the website can never be left without an admin."""
    if not permissions.has(admin, "staff.manage"):
        raise ServiceError("admin.no_permission")
    if level not in ("admin", "moderator", "none"):
        raise ServiceError("admin.bad_level")
    target = User.query.filter_by(username=(username or "").strip().lower().lstrip("@")).first()
    if target is None:
        raise ServiceError("admin.not_found")
    if target.id == admin.id:
        raise ServiceError("admin.not_self")
    if target.email in current_app.config["ADMIN_EMAILS"]:
        raise ServiceError("admin.staff_env")
    if level != "none" and not (target.is_email_verified and target.status == "active"):
        raise ServiceError("admin.staff_target")
    old = target.staff_level
    if level == "none":
        target.role, target.admin_role = "user", None
    else:
        target.role, target.admin_role = "admin", level
    audit(admin, "staff.level", "user", target.id, {"from": old, "to": None if level == "none" else level})
    db.session.commit()
    return target
