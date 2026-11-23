from datetime import datetime, timezone


def utcnow() -> datetime:
    """All times are stored as naive UTC. Convert only when displaying."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
