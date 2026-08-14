"""Base domain models and enums for the event engine.

Datetimes are always timezone-aware; the DB stores UTC ISO-8601 TEXT while
each event also keeps its IANA timezone name (see ARCHITECTURE.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class EventType(str, Enum):
    CLASS = "class"
    MEETING = "meeting"
    WORK = "work"
    PERSONAL = "personal"
    DEADLINE = "deadline"
    TASK = "task"
    OTHER = "other"


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class Privacy(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    HIDDEN = "hidden"


class EventStatus(str, Enum):
    ACTIVE = "active"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass
class Event:
    title: str
    start_at: datetime
    end_at: datetime
    timezone: str
    id: int | None = None
    description: str = ""
    location: str = ""
    event_type: EventType = EventType.OTHER
    priority: Priority = Priority.NORMAL
    rrule: str | None = None
    recurrence_group_id: int | None = None
    privacy: Privacy = Privacy.PUBLIC
    status: EventStatus = EventStatus.ACTIVE
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Reminder:
    event_id: int
    minutes_before: int
    id: int | None = None
    sound: bool = True
    critical: bool = False


class RecurrenceExceptionAction(str, Enum):
    CANCEL = "cancel"
    MODIFY = "modify"


@dataclass
class RecurrenceException:
    """Exception for a single occurrence of a recurring series.

    ``occurrence_start`` is the ORIGINAL occurrence start (before any
    override); it is the deterministic identity of the occurrence within the
    series (event id + original start, see ARCHITECTURE.md).
    """

    event_id: int
    occurrence_start: datetime
    action: RecurrenceExceptionAction
    title: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    location: str | None = None
    id: int | None = None


@dataclass
class Occurrence:
    """A concrete instance of an event inside a query range.

    For a modified occurrence, ``occurrence_start``/``occurrence_end`` are the
    overridden times; the original time is not exposed here (see
    ARCHITECTURE.md, exception identity).
    """

    event_id: int
    occurrence_start: datetime
    occurrence_end: datetime
    title: str
    location: str
    event_type: EventType
    priority: Priority
    privacy: Privacy
    status: EventStatus