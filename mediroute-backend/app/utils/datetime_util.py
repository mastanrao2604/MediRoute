"""UTC storage helpers — naive DB datetimes serialize with Z for clients."""
from datetime import datetime, timezone
from typing import Optional


def to_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def utc_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return to_utc_naive(dt).isoformat() + "Z"
