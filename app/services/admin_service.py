"""Admin actions. Every change is written to the audit log. Admins never read chat text."""
from datetime import timedelta

from flask import url_for
from sqlalchemy import func, or_

from ..extensions import db
from ..models import (AdminAuditLog, Announcement, Conversation, Message, MessageRequest,
                      Notification, Report, User)
from ..utils.time import utcnow
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
def stats() -> dict:
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
    }


# ------------------------------------------------------------------ users
def list_users(q: str | None, status: str | None, page: int):
    query = User.query
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


def set_status(admin: User, target: User, status: str) -> None:
    if status not in STATUSES:
        raise ServiceError("admin.bad_status")
    if target.id == admin.id:
        raise ServiceError("admin.not_self")
    if target.is_admin:
        raise ServiceError("admin.not_admin")
    if target.status == status:
        return
    old = target.status
    target.status = status
    audit(admin, "user.status", "user", target.id, {"from": old, "to": status})
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
