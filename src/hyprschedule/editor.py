"""Add/Edit UI contract (Phase 6).

This module defines the read-only JSON contract between the Eww editor UI
(``eww/bin/hyprschedule_editor.py``) and the backend. It NEVER mutates the
database:

- ``build_form_defaults`` / ``event_to_editor_dict`` produce the form payload
  for ``schedctl editor-data`` (read-only; see ARCHITECTURE.md).
- ``add_args`` / ``edit_args`` translate the form document back into argument
  objects that ``cmd_add`` / ``cmd_edit`` already understand, so validation,
  conflict detection, ``--force`` semantics and the daemon reload signal stay
  in the existing backend paths.

The UI is a presentation layer only: every mutation goes through
``schedctl editor-save``, which reads exactly one JSON document from stdin.
User values never reach a shell.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from hyprschedule import recurrence
from hyprschedule.config import Config
from hyprschedule.models import Event, EventType, Priority, Privacy

DEFAULT_DURATION_MINUTES = 45

# Monday == 0, matching date.weekday() and recurrence.WEEKDAYS.
WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

DAY_START = time(7, 0)


class InvalidForm(Exception):
    """Malformed editor form document; printed to stderr with EXIT_USAGE."""


# --------------------------------------------------------------- read side


def _ceil_half_hour(dt: datetime) -> datetime:
    """The next half-hour boundary strictly after ``dt``."""
    minutes = ((dt.minute // 30) + 1) * 30
    return dt.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minutes)


def build_form_defaults(config: Config, now: datetime) -> dict[str, Any]:
    """A fresh "add" form: today in the config timezone, starting at the next
    half-hour (07:00 tomorrow when the half-hour would fall after midnight),
    lasting :data:`DEFAULT_DURATION_MINUTES`, pre-filled with the config's
    default reminder offsets. Purely read-only.
    """
    tz = ZoneInfo(config.timezone)
    local = now.astimezone(tz)
    candidate = _ceil_half_hour(local)
    if candidate.date() > local.date():
        candidate = datetime.combine(candidate.date(), DAY_START, tzinfo=tz)
    end = candidate + timedelta(minutes=DEFAULT_DURATION_MINUTES)
    return {
        "id": None,
        "mode": "add",
        "title": "",
        "date": candidate.date().isoformat(),
        "start": candidate.strftime("%H:%M"),
        "end": end.strftime("%H:%M"),
        "location": "",
        "description": "",
        "event_type": "class",
        "priority": "normal",
        "privacy": "public",
        "reminders": list(config.notification.default_reminders),
        "recurrence": {"type": "none", "weekdays": [], "until": None},
    }


def event_to_editor_dict(event: Event, reminders: Sequence[int]) -> dict[str, Any]:
    """The "edit" form for one event, including its recurrence and reminder
    offsets. Read-only."""
    tz = ZoneInfo(event.timezone)
    start = event.start_at.astimezone(tz)
    end = event.end_at.astimezone(tz)
    recurrence_dict: dict[str, Any] = {"type": "none", "weekdays": [], "until": None}
    if event.rrule is not None:
        rule = recurrence.parse_weekly_rule(
            event.rrule,
            start_at=event.start_at,
            end_at=event.end_at,
            timezone=event.timezone,
        )
        recurrence_dict = {
            "type": "weekly",
            "weekdays": [WEEKDAY_NAMES[i] for i in rule.byday],
            "until": rule.until.isoformat() if rule.until is not None else None,
        }
    return {
        "id": event.id,
        "mode": "edit",
        "title": event.title,
        "date": start.date().isoformat(),
        "start": start.strftime("%H:%M"),
        "end": end.strftime("%H:%M"),
        "location": event.location,
        "description": event.description,
        "event_type": event.event_type.value,
        "priority": event.priority.value,
        "privacy": event.privacy.value,
        "reminders": sorted(int(r) for r in reminders),
        "recurrence": recurrence_dict,
    }


# -------------------------------------------------------------- write side


def _parse_str(doc: dict[str, Any], key: str, *, required: bool = False) -> str:
    if key not in doc:
        if required:
            raise InvalidForm(f'thiếu trường "{key}"')
        return ""
    value = doc[key]
    if not isinstance(value, str):
        raise InvalidForm(f'"{key}" phải là chuỗi')
    return value


def _parse_date_field(doc: dict[str, Any], key: str) -> date:
    value = doc.get(key)
    if not isinstance(value, str):
        raise InvalidForm(f'"{key}" phải là chuỗi ngày YYYY-MM-DD')
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise InvalidForm(f'"{key}" phải có dạng YYYY-MM-DD') from None


def _parse_time_field(doc: dict[str, Any], key: str) -> time:
    value = doc.get(key)
    if not isinstance(value, str):
        raise InvalidForm(f'"{key}" phải là chuỗi giờ HH:MM')
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        raise InvalidForm(f'"{key}" phải có dạng HH:MM') from None


def _parse_enum(doc: dict[str, Any], key: str, enum_cls: type[Any]) -> Any:
    value = doc.get(key)
    if not isinstance(value, str):
        raise InvalidForm(f'thiếu trường "{key}"')
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(member.value for member in enum_cls)
        raise InvalidForm(f'"{key}" phải là một trong: {allowed}') from None


def _parse_reminders(doc: dict[str, Any], key: str) -> list[int] | None:
    value = doc.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value
    ):
        raise InvalidForm(f'"{key}" phải là danh sách số phút >= 0')
    return sorted(set(value))


def _parse_recurrence(
    doc: dict[str, Any],
) -> tuple[str, list[int] | None, date | None]:
    raw = doc.get("recurrence")
    if raw is None:
        return "none", None, None
    if not isinstance(raw, dict):
        raise InvalidForm('"recurrence" phải là một object')
    rtype = raw.get("type", "none")
    if rtype == "none":
        return "none", None, None
    if rtype != "weekly":
        raise InvalidForm('"recurrence.type" chỉ hỗ trợ "none" hoặc "weekly"')
    weekdays_raw = raw.get("weekdays")
    if not isinstance(weekdays_raw, list) or not weekdays_raw:
        raise InvalidForm(
            '"recurrence.weekdays" phải là danh sách ngày (ví dụ ["mon","wed","fri"])'
        )
    try:
        weekdays = sorted({WEEKDAY_NAMES.index(str(day)) for day in weekdays_raw})
    except ValueError:
        raise InvalidForm(
            '"recurrence.weekdays" chỉ chấp nhận: ' + ", ".join(WEEKDAY_NAMES)
        ) from None
    until: date | None = None
    until_raw = raw.get("until")
    if until_raw not in (None, ""):
        if not isinstance(until_raw, str):
            raise InvalidForm('"recurrence.until" phải là chuỗi YYYY-MM-DD')
        try:
            until = datetime.strptime(until_raw, "%Y-%m-%d").date()
        except ValueError:
            raise InvalidForm('"recurrence.until" phải có dạng YYYY-MM-DD') from None
    return "weekly", weekdays, until


def _form_common(doc: dict[str, Any], config: Config) -> dict[str, Any]:
    """Parse the fields shared by add and edit. The backend remains
    authoritative for semantic validation (title length, conflict rules,
    --until vs --from ordering, ...); here we only coerce shapes."""
    rtype, weekdays, until = _parse_recurrence(doc)
    return {
        "title": _parse_str(doc, "title", required=True),
        "description": _parse_str(doc, "description"),
        "location": _parse_str(doc, "location"),
        "event_type": _parse_enum(doc, "event_type", EventType),
        "priority": _parse_enum(doc, "priority", Priority),
        "privacy": _parse_enum(doc, "privacy", Privacy),
        "date": _parse_date_field(doc, "date"),
        "start": _parse_time_field(doc, "start"),
        "end": _parse_time_field(doc, "end"),
        "remind": _parse_reminders(doc, "reminders"),
        "force": bool(doc.get("force")),
        "rtype": rtype,
        "weekdays": weekdays,
        "until": until,
    }


def add_args(doc: dict[str, Any], config: Config) -> SimpleNamespace:
    """Argument object for ``cmd_add``. ``date`` stays None for recurring
    forms (the CLI reserves it for one-time events); ``--from`` carries the
    first occurrence day."""
    common = _form_common(doc, config)
    if common["rtype"] == "weekly":
        repeat, date_arg, from_date, weekday = "weekly", None, common["date"], common["weekdays"]
    else:
        repeat, date_arg, from_date, weekday = "none", common["date"], None, None
    return SimpleNamespace(
        title=common["title"],
        description=common["description"],
        location=common["location"],
        type=common["event_type"],
        priority=common["priority"],
        privacy=common["privacy"],
        timezone=None,
        date=date_arg,
        start=common["start"],
        end=common["end"],
        repeat=repeat,
        weekday=weekday,
        from_date=from_date,
        until=common["until"],
        remind=common["remind"],
        force=common["force"],
    )


def edit_args(doc: dict[str, Any], config: Config) -> SimpleNamespace:
    """Argument object for ``cmd_edit``. ``until`` is only forwarded for
    weekly forms so the backend's "--weekday/--until require --repeat weekly"
    guard can never fire spuriously."""
    event_id = doc.get("id")
    if isinstance(event_id, bool) or not isinstance(event_id, int):
        raise InvalidForm('"id" phải là số nguyên')
    common = _form_common(doc, config)
    if common["rtype"] == "weekly":
        repeat, weekday, until = "weekly", common["weekdays"], common["until"]
    else:
        repeat, weekday, until = "none", None, None
    return SimpleNamespace(
        id=event_id,
        title=common["title"],
        description=common["description"],
        location=common["location"],
        type=common["event_type"],
        priority=common["priority"],
        privacy=common["privacy"],
        timezone=None,
        date=common["date"],
        start=common["start"],
        end=common["end"],
        repeat=repeat,
        weekday=weekday,
        until=until,
        remind=common["remind"],
        force=common["force"],
    )
