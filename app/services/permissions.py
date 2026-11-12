"""Who may do what in the admin area.

Levels (highest first):
  super_admin  everyone listed in ADMIN_EMAILS (the root of trust; cannot be changed from the website)
  admin        granted by a super admin
  moderator    granted by a super admin: day-to-day moderation only

Nothing here lets a person raise their own level. Unknown admin pages need the highest level.
"""
LEVELS = ("moderator", "admin", "super_admin")

_MODERATOR = frozenset({"admin.view", "users.view", "users.moderate", "reports.manage"})
_ADMIN = _MODERATOR | {"users.ban", "announcements.manage", "settings.manage", "audit.view"}
_SUPER = _ADMIN | {"staff.manage"}
GRANTS = {"moderator": _MODERATOR, "admin": _ADMIN, "super_admin": _SUPER}

# admin endpoint -> permission. Anything not listed needs "staff.manage" (safe by default).
ENDPOINT_PERMS = {
    "admin.dashboard": "admin.view",
    "admin.users": "users.view", "admin.user": "users.view",
    "admin.user_status": "users.moderate", "admin.user_note": "users.moderate",
    "admin.user_force_logout": "users.moderate",
    "admin.reports": "reports.manage", "admin.report": "reports.manage", "admin.report_status": "reports.manage",
    "admin.announcements": "announcements.manage", "admin.announcement_edit": "announcements.manage",
    "admin.announcement_delete": "announcements.manage", "admin.announcement_toggle": "announcements.manage",
    "admin.settings": "settings.manage",
    "admin.audit": "audit.view",
    "admin.staff": "staff.manage",
}


def has(user, permission: str) -> bool:
    if not getattr(user, "is_authenticated", False) or not user.is_admin:
        return False
    return permission in GRANTS.get(user.staff_level, ())


def required_for(endpoint: str | None) -> str:
    return ENDPOINT_PERMS.get(endpoint or "", "staff.manage")
