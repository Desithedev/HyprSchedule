"""Timezone-aware datetime helpers shared by the event engine.

Business logic never works with naive datetimes. Storage format is UTC
ISO-8601 TEXT with an explicit offset (see ARCHITECTURE.md).
"""

from __future__ import annotations

from datetime import datetime, timezone

from hyprschedule.errors import InvalidEvent


def ensure_aware(value: datetime, what: str = "datetime") -> datetime:
    """Reject naive datetimes with a clear domain error."""
    if value.tzinfo is None:
        raise InvalidEvent(f"{what} must be timezone-aware, got naive datetime {value}")
    return value


def to_utc_iso(value: datetime) -> str:
    """Serialize a timezone-aware datetime as UTC ISO-8601 TEXT."""
    ensure_aware(value)
    return value.astimezone(timezone.utc).isoformat()


def from_utc_iso(text: str) -> datetime:
    """Parse a stored UTC ISO-8601 TEXT value back into an aware datetime."""
    return datetime.fromisoformat(text)