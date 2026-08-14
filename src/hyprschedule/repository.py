"""Event repository: the persistence boundary of the event engine.

All event, reminder and recurrence-exception access goes through
:class:`EventRepository`; no raw SQLite escapes to callers. Occurrence
expansion applies recurrence rules and exceptions here, so every consumer
(CLI, daemon, widgets) gets consistent data.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable
from zoneinfo import ZoneInfo

from hyprschedule import recurrence
from hyprschedule.database import Database
from hyprschedule.errors import (
    EventNotFound,
    InvalidEvent,
    InvalidException,
    InvalidRecurrence,
    InvalidReminder,
)
from hyprschedule.models import (
    Event,
    EventStatus,
    EventType,
    Occurrence,
    Priority,
    Privacy,
    RecurrenceException,
    RecurrenceExceptionAction,
    Reminder,
)
from hyprschedule.timeutil import ensure_aware, from_utc_iso, to_utc_iso

_EVENT_COLUMNS = (
    "id, title, description, location, start_at, end_at, timezone, event_type, "
    "priority, rrule, recurrence_group_id, privacy, status, created_at, updated_at"
)


def _coerce_enum(value: object, enum_cls: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError:
            raise InvalidEvent(f"invalid {field_name} {value!r}") from None
    raise InvalidEvent(f"invalid {field_name} {value!r}")


def validate_event(event: Event) -> None:
    """Validate an event's domain rules; raise on violation.

    - title must be non-empty
    - start_at / end_at must be timezone-aware
    - end_at must be strictly after start_at (full-datetime comparison, so
      events crossing midnight are valid)
    - timezone must be a valid IANA name
    - enum fields must be valid members
    - rrule, when present, must be within the supported weekly subset
    """
    if not isinstance(event.title, str) or not event.title.strip():
        raise InvalidEvent("title must not be empty")
    start = ensure_aware(event.start_at, "start_at")
    end = ensure_aware(event.end_at, "end_at")
    if end <= start:
        raise InvalidEvent(
            f"end_at {end.isoformat()} must be after start_at {start.isoformat()}"
        )
    try:
        ZoneInfo(event.timezone)
    except Exception:
        raise InvalidEvent(f"invalid timezone {event.timezone!r}") from None
    _coerce_enum(event.event_type, EventType, "event_type")
    _coerce_enum(event.priority, Priority, "priority")
    _coerce_enum(event.privacy, Privacy, "privacy")
    _coerce_enum(event.status, EventStatus, "status")
    if event.rrule is not None:
        recurrence.parse_weekly_rule(
            event.rrule, start_at=event.start_at, end_at=event.end_at, timezone=event.timezone
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EventRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ events

    def create_event(self, event: Event) -> Event:
        """Insert a new event; returns it with id and timestamps set."""
        validate_event(event)
        now = _now()
        created = event.created_at or now
        updated = event.updated_at or now
        with self._db.transaction() as conn:
            cur = conn.execute(
                f"INSERT INTO events ({_EVENT_COLUMNS}) VALUES "
                "(NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.title,
                    event.description,
                    event.location,
                    to_utc_iso(event.start_at),
                    to_utc_iso(event.end_at),
                    event.timezone,
                    event.event_type.value,
                    event.priority.value,
                    event.rrule,
                    event.recurrence_group_id,
                    event.privacy.value,
                    event.status.value,
                    to_utc_iso(created),
                    to_utc_iso(updated),
                ),
            )
        event.id = int(cur.lastrowid)
        event.created_at = created
        event.updated_at = updated
        return event

    def get_event(self, event_id: int) -> Event:
        row = self._db.connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise EventNotFound(f"event {event_id} does not exist")
        return self._row_to_event(row)

    def update_event(
        self,
        event_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        location: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        timezone: str | None = None,
        event_type: EventType | str | None = None,
        priority: Priority | str | None = None,
        privacy: Privacy | str | None = None,
        status: EventStatus | str | None = None,
        rrule: str | None = None,
        clear_rrule: bool = False,
    ) -> Event:
        """Update an event; ``None`` means "unchanged". Returns the saved event.

        ``clear_rrule=True`` sets the recurrence rule to NULL.
        """
        existing = self.get_event(event_id)
        if start_at is not None:
            existing.start_at = start_at
        if end_at is not None:
            existing.end_at = end_at
        merged = Event(
            title=title if title is not None else existing.title,
            description=description if description is not None else existing.description,
            location=location if location is not None else existing.location,
            start_at=existing.start_at,
            end_at=existing.end_at,
            timezone=timezone if timezone is not None else existing.timezone,
            event_type=_coerce_enum(
                event_type if event_type is not None else existing.event_type,
                EventType,
                "event_type",
            ),
            priority=_coerce_enum(
                priority if priority is not None else existing.priority, Priority, "priority"
            ),
            rrule=None if clear_rrule else (rrule if rrule is not None else existing.rrule),
            recurrence_group_id=existing.recurrence_group_id,
            privacy=_coerce_enum(
                privacy if privacy is not None else existing.privacy, Privacy, "privacy"
            ),
            status=_coerce_enum(
                status if status is not None else existing.status, EventStatus, "status"
            ),
            created_at=existing.created_at,
            updated_at=existing.updated_at,
        )
        validate_event(merged)
        updated = _now()
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE events SET title = ?, description = ?, location = ?, "
                "start_at = ?, end_at = ?, timezone = ?, event_type = ?, "
                "priority = ?, rrule = ?, privacy = ?, status = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    merged.title,
                    merged.description,
                    merged.location,
                    to_utc_iso(merged.start_at),
                    to_utc_iso(merged.end_at),
                    merged.timezone,
                    merged.event_type.value,
                    merged.priority.value,
                    merged.rrule,
                    merged.privacy.value,
                    merged.status.value,
                    to_utc_iso(updated),
                    event_id,
                ),
            )
        merged.id = event_id
        merged.updated_at = updated
        return merged

    def delete_event(self, event_id: int) -> None:
        """Hard-delete the event; reminders and exceptions cascade."""
        with self._db.transaction() as conn:
            cur = conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
            if cur.rowcount == 0:
                raise EventNotFound(f"event {event_id} does not exist")

    def list_events(self) -> list[Event]:
        rows = self._db.connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM events ORDER BY start_at"
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def search_events(self, query: str) -> list[Event]:
        """Case-insensitive substring match over title, description, location.

        A plain ``LIKE`` query is sufficient for Phase 2 (no FTS5).
        """
        escaped = query.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
        rows = self._db.connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM events "
            "WHERE title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' "
            "OR location LIKE ? ESCAPE '\\' "
            "ORDER BY start_at",
            (pattern, pattern, pattern),
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def max_duration(self) -> timedelta:
        """Longest stored event duration; used to bound conflict searches.

        Computed in SQL (stored timestamps are UTC ISO-8601, which
        ``julianday`` parses) so it never loads every event row.
        """
        row = self._db.connection.execute(
            "SELECT MAX("
            "CAST(strftime('%s', end_at) AS INTEGER) "
            "- CAST(strftime('%s', start_at) AS INTEGER)"
            ") AS seconds FROM events"
        ).fetchone()
        seconds = row["seconds"]
        return timedelta(seconds=seconds) if seconds is not None else timedelta(0)

    @staticmethod
    def _row_to_event(row: object) -> Event:
        event = Event(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            location=row["location"],
            start_at=from_utc_iso(row["start_at"]),
            end_at=from_utc_iso(row["end_at"]),
            timezone=row["timezone"],
            event_type=_coerce_enum(row["event_type"], EventType, "event_type"),
            priority=_coerce_enum(row["priority"], Priority, "priority"),
            rrule=row["rrule"],
            recurrence_group_id=row["recurrence_group_id"],
            privacy=_coerce_enum(row["privacy"], Privacy, "privacy"),
            status=_coerce_enum(row["status"], EventStatus, "status"),
            created_at=from_utc_iso(row["created_at"]),
            updated_at=from_utc_iso(row["updated_at"]),
        )
        return event

    # ----------------------------------------------------------- occurrences

    def get_occurrences(
        self, range_start: datetime, range_end: datetime
    ) -> list[Occurrence]:
        """All occurrences overlapping ``[range_start, range_end)``.

        Deterministic ordering: occurrence_start ascending, then event_id.
        Only events that can possibly overlap the range are loaded: recurring
        events always (expansion is range-bounded), one-time events filtered
        by their stored UTC bounds — the widget daemon polls every few
        seconds, so the query must never scan every event ever created.
        """
        self._validate_range(range_start, range_end)
        exceptions_by_event: dict[int, dict[str, RecurrenceException]] = defaultdict(dict)
        for exc in self.list_exceptions():
            exceptions_by_event[exc.event_id][to_utc_iso(exc.occurrence_start)] = exc
        occurrences: list[Occurrence] = []
        for event in self._events_overlapping(range_start, range_end):
            occurrences.extend(
                self._expand_event(
                    event, range_start, range_end, exceptions_by_event[event.id]
                )
            )
        occurrences.sort(key=lambda occ: (occ.occurrence_start, occ.event_id))
        return occurrences

    def _events_overlapping(
        self, range_start: datetime, range_end: datetime
    ) -> list[Event]:
        """Events that can possibly overlap the range (see get_occurrences).

        Stored ``start_at``/``end_at`` are UTC ISO-8601 with a consistent
        offset, so lexicographic comparison is chronological. The predicate
        mirrors the one-time overlap check in :meth:`_expand_event`.
        """
        rows = self._db.connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM events "
            "WHERE rrule IS NOT NULL OR (start_at < ? AND end_at > ?) "
            "ORDER BY start_at",
            (to_utc_iso(range_end), to_utc_iso(range_start)),
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def get_event_occurrences(
        self, event_id: int, range_start: datetime, range_end: datetime
    ) -> list[Occurrence]:
        """Occurrences of a single event overlapping the range (exceptions applied)."""
        self._validate_range(range_start, range_end)
        event = self.get_event(event_id)
        exceptions = {to_utc_iso(e.occurrence_start): e for e in self.list_exceptions(event_id)}
        return self._expand_event(event, range_start, range_end, exceptions)

    @staticmethod
    def _validate_range(range_start: datetime, range_end: datetime) -> None:
        ensure_aware(range_start, "range_start")
        ensure_aware(range_end, "range_end")
        if range_end <= range_start:
            raise InvalidEvent(
                f"range_end {range_end.isoformat()} must be after range_start {range_start.isoformat()}"
            )

    def _expand_event(
        self,
        event: Event,
        range_start: datetime,
        range_end: datetime,
        exceptions: dict[str, RecurrenceException],
    ) -> list[Occurrence]:
        if event.rrule is None:
            if event.start_at < range_end and event.end_at > range_start:
                return [self._occurrence_from(event, event.start_at, event.end_at)]
            return []

        rule = recurrence.parse_weekly_rule(
            event.rrule, start_at=event.start_at, end_at=event.end_at, timezone=event.timezone
        )
        tz = ZoneInfo(event.timezone)
        duration = event.end_at - event.start_at
        result: list[Occurrence] = []
        for original_start in recurrence.expand_weekly(rule, range_start, range_end):
            exc = exceptions.get(to_utc_iso(original_start))
            if exc is not None and exc.action is RecurrenceExceptionAction.CANCEL:
                continue
            title = event.title
            location = event.location
            start = original_start
            end = start + duration
            if exc is not None:
                title = exc.title if exc.title is not None else title
                if exc.location is not None:
                    location = exc.location
                if exc.start_at is not None:
                    start = exc.start_at.astimezone(tz)
                if exc.end_at is not None:
                    end = exc.end_at.astimezone(tz)
                else:
                    end = start + duration
            result.append(self._occurrence_from(event, start, end, title, location))
        return result

    @staticmethod
    def _occurrence_from(
        event: Event,
        start: datetime,
        end: datetime,
        title: str | None = None,
        location: str | None = None,
    ) -> Occurrence:
        return Occurrence(
            event_id=event.id if event.id is not None else -1,
            occurrence_start=start,
            occurrence_end=end,
            title=title if title is not None else event.title,
            location=location if location is not None else event.location,
            event_type=event.event_type,
            priority=event.priority,
            privacy=event.privacy,
            status=event.status,
        )

    # -------------------------------------------------------------- reminders

    def add_reminder(
        self, event_id: int, minutes_before: int, *, sound: bool = True, critical: bool = False
    ) -> Reminder:
        """Attach a reminder to an event.

        Duplicate ``minutes_before`` for the same event is rejected
        (``InvalidReminder``); a UNIQUE index backs this up.
        """
        self.get_event(event_id)
        minutes = self._validate_minutes(minutes_before)
        if self._reminder_exists(event_id, minutes):
            raise InvalidReminder(
                f"event {event_id} already has a reminder {minutes} minutes before"
            )
        try:
            with self._db.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO reminders (event_id, minutes_before, sound, critical) "
                    "VALUES (?, ?, ?, ?)",
                    (event_id, minutes, int(sound), int(critical)),
                )
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise InvalidReminder(
                    f"event {event_id} already has a reminder {minutes} minutes before"
                ) from exc
            raise
        return Reminder(
            id=int(cur.lastrowid), event_id=event_id, minutes_before=minutes,
            sound=sound, critical=critical,
        )

    def list_reminders(self, event_id: int) -> list[Reminder]:
        rows = self._db.connection.execute(
            "SELECT id, event_id, minutes_before, sound, critical FROM reminders "
            "WHERE event_id = ? ORDER BY minutes_before",
            (event_id,),
        ).fetchall()
        return [
            Reminder(
                id=row["id"],
                event_id=row["event_id"],
                minutes_before=row["minutes_before"],
                sound=bool(row["sound"]),
                critical=bool(row["critical"]),
            )
            for row in rows
        ]

    def remove_reminder(self, event_id: int, minutes_before: int) -> None:
        minutes = self._validate_minutes(minutes_before)
        with self._db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM reminders WHERE event_id = ? AND minutes_before = ?",
                (event_id, minutes),
            )
        if cur.rowcount == 0:
            raise InvalidReminder(
                f"event {event_id} has no reminder {minutes} minutes before"
            )

    def replace_reminders(self, event_id: int, offsets: Iterable[int]) -> list[Reminder]:
        """Replace all reminders of an event with ``offsets``.

        Duplicate offsets in the input are rejected (``InvalidReminder``).
        """
        self.get_event(event_id)
        minutes_list = [self._validate_minutes(m) for m in offsets]
        if len(minutes_list) != len(set(minutes_list)):
            raise InvalidReminder("reminder offsets must not contain duplicates")
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM reminders WHERE event_id = ?", (event_id,))
            for minutes in minutes_list:
                conn.execute(
                    "INSERT INTO reminders (event_id, minutes_before, sound, critical) "
                    "VALUES (?, ?, 1, 0)",
                    (event_id, minutes),
                )
        return self.list_reminders(event_id)

    def max_reminder_offset(self) -> int | None:
        """Largest reminder offset across all events (``None`` when no
        reminders exist). The scheduler uses it to bound the search for
        occurrences whose reminders may already be due."""
        row = self._db.connection.execute(
            "SELECT MAX(minutes_before) AS m FROM reminders"
        ).fetchone()
        return row["m"]

    def _reminder_exists(self, event_id: int, minutes: int) -> bool:
        row = self._db.connection.execute(
            "SELECT 1 FROM reminders WHERE event_id = ? AND minutes_before = ?",
            (event_id, minutes),
        ).fetchone()
        return row is not None

    @staticmethod
    def _validate_minutes(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise InvalidReminder(
                f"minutes_before must be a non-negative integer, got {value!r}"
            )
        return value

    # ------------------------------------------------------------ exceptions

    def add_exception(self, exception: RecurrenceException) -> RecurrenceException:
        """Add a cancel/modify exception for one occurrence of a recurring event."""
        event = self.get_event(exception.event_id)
        if event.rrule is None:
            raise InvalidException(
                f"event {exception.event_id} is not recurring; exceptions require rrule"
            )
        original = ensure_aware(exception.occurrence_start, "occurrence_start")
        action = _coerce_enum(exception.action, RecurrenceExceptionAction, "action")

        start = exception.start_at
        end = exception.end_at
        if start is not None:
            start = ensure_aware(start, "start_at")
        if end is not None:
            end = ensure_aware(end, "end_at")

        if action is RecurrenceExceptionAction.CANCEL:
            if start is not None or end is not None or exception.title is not None or exception.location is not None:
                raise InvalidException("cancel exceptions must not carry overrides")
        else:
            if (
                start is None
                and end is None
                and exception.title is None
                and exception.location is None
            ):
                raise InvalidException("modify exceptions require at least one override")
            if start is not None and end is not None and end <= start:
                raise InvalidException("exception end_at must be after start_at")

        original_iso = to_utc_iso(original)
        existing = self._db.connection.execute(
            "SELECT 1 FROM recurrence_exceptions WHERE event_id = ? AND occurrence_start = ?",
            (exception.event_id, original_iso),
        ).fetchone()
        if existing is not None:
            raise InvalidException(
                f"event {exception.event_id} already has an exception for {original_iso}"
            )

        with self._db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO recurrence_exceptions "
                "(event_id, occurrence_start, action, title, start_at, end_at, location) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    exception.event_id,
                    original_iso,
                    action.value,
                    exception.title,
                    to_utc_iso(start) if start is not None else None,
                    to_utc_iso(end) if end is not None else None,
                    exception.location,
                ),
            )
        return RecurrenceException(
            id=int(cur.lastrowid),
            event_id=exception.event_id,
            occurrence_start=original,
            action=action,
            title=exception.title,
            start_at=start,
            end_at=end,
            location=exception.location,
        )

    def list_exceptions(
        self, event_id: int | None = None
    ) -> list[RecurrenceException]:
        if event_id is None:
            rows = self._db.connection.execute(
                "SELECT * FROM recurrence_exceptions ORDER BY occurrence_start"
            ).fetchall()
        else:
            rows = self._db.connection.execute(
                "SELECT * FROM recurrence_exceptions WHERE event_id = ? "
                "ORDER BY occurrence_start",
                (event_id,),
            ).fetchall()
        return [
            RecurrenceException(
                id=row["id"],
                event_id=row["event_id"],
                occurrence_start=from_utc_iso(row["occurrence_start"]),
                action=_coerce_enum(
                    row["action"], RecurrenceExceptionAction, "action"
                ),
                title=row["title"],
                start_at=from_utc_iso(row["start_at"]) if row["start_at"] is not None else None,
                end_at=from_utc_iso(row["end_at"]) if row["end_at"] is not None else None,
                location=row["location"],
            )
            for row in rows
        ]

    def remove_exception(self, event_id: int, occurrence_start: datetime) -> None:
        original = ensure_aware(occurrence_start, "occurrence_start")
        with self._db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM recurrence_exceptions WHERE event_id = ? AND occurrence_start = ?",
                (event_id, to_utc_iso(original)),
            )
        if cur.rowcount == 0:
            raise InvalidException(
                f"no exception for event {event_id} at {to_utc_iso(original)}"
            )

    # --------------------------------------------------------- notification log

    def notification_exists(
        self, event_id: int, occurrence_start: datetime, minutes_before: int
    ) -> bool:
        """True when this exact reminder instance was already notified.

        The daemon is the only writer of ``notification_log``; this query is
        the dedup boundary that survives daemon restarts.
        """
        start = ensure_aware(occurrence_start, "occurrence_start")
        row = self._db.connection.execute(
            "SELECT 1 FROM notification_log "
            "WHERE event_id = ? AND occurrence_start = ? AND reminder_minutes = ?",
            (event_id, to_utc_iso(start), minutes_before),
        ).fetchone()
        return row is not None

    def any_notification(self, event_id: int, occurrence_start: datetime) -> bool:
        """True when any notification was emitted for this occurrence.

        Used by suspend/resume handling: a missed-event notice is suppressed
        when the occurrence's regular reminders already fired.
        """
        start = ensure_aware(occurrence_start, "occurrence_start")
        row = self._db.connection.execute(
            "SELECT 1 FROM notification_log "
            "WHERE event_id = ? AND occurrence_start = ? LIMIT 1",
            (event_id, to_utc_iso(start)),
        ).fetchone()
        return row is not None

    def record_notification(
        self,
        event_id: int,
        occurrence_start: datetime,
        minutes_before: int,
        notified_at: datetime | None = None,
    ) -> None:
        """Insert a notification-log entry (idempotent).

        ``INSERT OR IGNORE`` makes duplicate writes safe even when two daemon
        processes race; the UNIQUE(event_id, occurrence_start, reminder_minutes)
        constraint is the source of truth.
        """
        start = ensure_aware(occurrence_start, "occurrence_start")
        notified = notified_at or _now()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO notification_log "
                "(event_id, occurrence_start, reminder_minutes, notified_at) "
                "VALUES (?, ?, ?, ?)",
                (event_id, to_utc_iso(start), minutes_before, to_utc_iso(notified)),
            )