from datetime import datetime

from hyprschedule.models import (
    Event,
    EventStatus,
    EventType,
    Priority,
    Privacy,
    Reminder,
)


def test_event_type_enum_values():
    assert EventType.CLASS.value == "class"
    assert EventType.MEETING.value == "meeting"
    assert EventType.WORK.value == "work"
    assert EventType.PERSONAL.value == "personal"
    assert EventType.DEADLINE.value == "deadline"
    assert EventType.TASK.value == "task"
    assert EventType.OTHER.value == "other"
    assert len(EventType) == 7
    assert all(isinstance(member.value, str) for member in EventType)


def test_priority_enum_values():
    assert Priority.LOW.value == "low"
    assert Priority.NORMAL.value == "normal"
    assert Priority.HIGH.value == "high"
    assert Priority.CRITICAL.value == "critical"
    assert len(Priority) == 4
    assert all(isinstance(member.value, str) for member in Priority)


def test_privacy_enum_values():
    assert Privacy.PUBLIC.value == "public"
    assert Privacy.PRIVATE.value == "private"
    assert Privacy.HIDDEN.value == "hidden"
    assert len(Privacy) == 3
    assert all(isinstance(member.value, str) for member in Privacy)


def test_event_status_enum_values():
    assert EventStatus.ACTIVE.value == "active"
    assert EventStatus.DONE.value == "done"
    assert EventStatus.CANCELLED.value == "cancelled"
    assert len(EventStatus) == 3
    assert all(isinstance(member.value, str) for member in EventStatus)


def test_event_defaults():
    event = Event(
        title="Standup",
        start_at=datetime.fromisoformat("2026-08-13T07:00:00+07:00"),
        end_at=datetime.fromisoformat("2026-08-13T07:30:00+07:00"),
        timezone="Asia/Ho_Chi_Minh",
    )

    assert event.id is None
    assert event.description == ""
    assert event.location == ""
    assert event.event_type is EventType.OTHER
    assert event.priority is Priority.NORMAL
    assert event.rrule is None
    assert event.recurrence_group_id is None
    assert event.privacy is Privacy.PUBLIC
    assert event.status is EventStatus.ACTIVE
    assert event.created_at is None
    assert event.updated_at is None


def test_event_keeps_timezone_aware_datetimes():
    start = datetime.fromisoformat("2026-08-13T07:00:00+07:00")
    end = datetime.fromisoformat("2026-08-13T08:00:00+07:00")
    assert start.tzinfo is not None
    assert end.tzinfo is not None

    event = Event(
        title="Meeting",
        start_at=start,
        end_at=end,
        timezone="Asia/Ho_Chi_Minh",
    )

    assert event.start_at == start
    assert event.end_at == end
    assert event.start_at.utcoffset().total_seconds() == 7 * 3600


def test_reminder_defaults():
    reminder = Reminder(event_id=4, minutes_before=15)

    assert reminder.id is None
    assert reminder.event_id == 4
    assert reminder.minutes_before == 15
    assert reminder.sound is True
    assert reminder.critical is False