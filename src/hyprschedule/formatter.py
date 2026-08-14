"""Formatting primitives and JSON contracts (Phase 2).

Human text formatting and the machine-readable JSON schema for occurrences
and events live here. The JSON contracts are the foundation for the Eww and
hyprlock formatters of later phases (ARCHITECTURE.md, JSON contracts).
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from hyprschedule import recurrence
from hyprschedule.models import Event, Occurrence

WEEKDAY_NAMES_VI = (
    "Thứ Hai",
    "Thứ Ba",
    "Thứ Tư",
    "Thứ Năm",
    "Thứ Sáu",
    "Thứ Bảy",
    "Chủ Nhật",
)


def format_date(value: date) -> str:
    """``14/08/2026``"""
    return value.strftime("%d/%m/%Y")


def format_date_short(value: date) -> str:
    """``14/08``"""
    return value.strftime("%d/%m")


def format_time(value: time) -> str:
    """``07:00``"""
    return value.strftime("%H:%M")


def weekday_name(value: date) -> str:
    return WEEKDAY_NAMES_VI[value.weekday()]


def local_time(value: datetime, timezone_name: str) -> datetime:
    """Convert a datetime into an event's IANA timezone for display."""
    return value.astimezone(ZoneInfo(timezone_name))


def range_dict(start: datetime, end: datetime) -> dict[str, str]:
    return {"start": start.isoformat(), "end": end.isoformat()}


def occurrence_to_dict(occ: Occurrence, event: Event) -> dict[str, Any]:
    """JSON occurrence contract (stable, documented in ARCHITECTURE.md).

    Timestamps are ISO-8601 with an explicit offset, expressed in the
    event's own timezone.
    """
    tz = ZoneInfo(event.timezone)
    return {
        "event_id": occ.event_id,
        "title": occ.title,
        "description": event.description,
        "location": occ.location,
        "start": occ.occurrence_start.astimezone(tz).isoformat(),
        "end": occ.occurrence_end.astimezone(tz).isoformat(),
        "timezone": event.timezone,
        "event_type": occ.event_type.value,
        "priority": occ.priority.value,
        "privacy": occ.privacy.value,
        "status": occ.status.value,
        "recurring": event.rrule is not None,
    }


def event_to_dict(event: Event, reminders: list[int] | None = None) -> dict[str, Any]:
    """JSON event contract for ``show`` / ``search`` output."""
    return {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "location": event.location,
        "start": event.start_at.isoformat(),
        "end": event.end_at.isoformat(),
        "timezone": event.timezone,
        "event_type": event.event_type.value,
        "priority": event.priority.value,
        "privacy": event.privacy.value,
        "status": event.status.value,
        "rrule": event.rrule,
        "recurring": event.rrule is not None,
        "reminders": sorted(reminders) if reminders else [],
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "updated_at": event.updated_at.isoformat() if event.updated_at else None,
    }


def recurrence_text(event: Event) -> str:
    """Human-readable recurrence summary, e.g. ``weekly · Thứ Hai · đến 31/12/2026``."""
    if event.rrule is None:
        return "Không lặp lại"
    rule = recurrence.parse_weekly_rule(
        event.rrule,
        start_at=event.start_at,
        end_at=event.end_at,
        timezone=event.timezone,
    )
    days = ", ".join(WEEKDAY_NAMES_VI[i] for i in rule.byday)
    text = f"weekly · {days}"
    if rule.until is not None:
        text += f" · đến {format_date(rule.until)}"
    return text