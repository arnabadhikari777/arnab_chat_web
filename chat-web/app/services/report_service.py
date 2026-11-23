from ..extensions import db
from ..models import Report, User
from .errors import FieldErrors, ServiceError

REASONS = ("spam", "harassment", "fake", "inappropriate", "other")


def create_report(reporter: User, reported: User, reason: str, details: str) -> Report:
    if reporter.id == reported.id:
        raise ServiceError("request.self")
    details = (details or "").strip()
    errors = {}
    if reason not in REASONS:
        errors["reason"] = ("err.report_reason", {})
    if len(details) > 1000:
        errors["details"] = ("err.report_details", {"max": 1000})
    if errors:
        raise FieldErrors(errors)
    open_one = Report.query.filter(
        Report.reporter_id == reporter.id, Report.reported_user_id == reported.id,
        Report.status.in_(("open", "reviewing")),
    ).first()
    if open_one:
        raise ServiceError("report.duplicate")
    report = Report(reporter_id=reporter.id, reported_user_id=reported.id, reason=reason, details=details)
    db.session.add(report)
    db.session.commit()
    return report
