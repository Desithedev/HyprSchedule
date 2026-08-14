from datetime import datetime, timedelta

import pytest

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
    Priority,
    Privacy,
    RecurrenceException,
    RecurrenceExceptionAction,
)
from hyprschedule.repository import EventRepository

TZ = "Asia/Ho_Chi_Minh"


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


@pytest.fixture
def repo(tmp_path):
    db = Database(tmp_path / "schedule.db")
    db.initialize()
    try:
        yield EventRepository(db)
    finally:
        db.close()


def make_event(
    start: str = "2026-08-17T09:00:00+07:00",
    end: str = "2026-08-17T09:30:00+07:00",
    **kwargs,
) -> Event:
    defaults = {
        "title": "Standup",
        "start_at": dt(start),
        "end_at": dt(end),
        "timezone": TZ,
    }
    defaults.update(kwargs)
    return Event(**defaults)


def make_recurring_event(
    start: str = "2026-08-17T07:00:00+07:00",
    end: str = "2026-08-17T07:45:00+07:00",
    rrule: str = "FREQ=WEEKLY;BYDAY=MO",
    **kwargs,
) -> Event:
    return make_event(start=start, end=end, rrule=rrule, **kwargs)


def occ_starts(occs) -> list[datetime]:
    return [occ.occurrence_start for occ in occs]


# ------------------------------------------------------------- one-time events


def test_create_and_get_event_round_trip(repo):
    event = make_event(
        description="Team sync",
        location="Room A",
        event_type=EventType.MEETING,
        priority=Priority.HIGH,
        privacy=Privacy.PRIVATE,
        status=EventStatus.ACTIVE,
    )
    saved = repo.create_event(event)
    assert saved.id is not None
    assert isinstance(saved.id, int)
    assert saved.created_at is not None and saved.created_at.tzinfo is not None
    assert saved.updated_at is not None and saved.updated_at.tzinfo is not None

    loaded = repo.get_event(saved.id)
    assert loaded.id == saved.id
    assert loaded.title == "Standup"
    assert loaded.description == "Team sync"
    assert loaded.location == "Room A"
    assert loaded.event_type is EventType.MEETING
    assert loaded.priority is Priority.HIGH
    assert loaded.privacy is Privacy.PRIVATE
    assert loaded.status is EventStatus.ACTIVE
    assert loaded.timezone == TZ
    assert loaded.start_at == dt("2026-08-17T09:00:00+07:00")
    assert loaded.end_at == dt("2026-08-17T09:30:00+07:00")
    assert loaded.created_at == saved.created_at
    assert loaded.updated_at == saved.updated_at


def test_create_crossing_midnight(repo):
    event = make_event(
        start="2026-08-17T23:00:00+07:00",
        end="2026-08-18T01:00:00+07:00",
    )
    saved = repo.create_event(event)
    loaded = repo.get_event(saved.id)
    assert loaded.start_at == dt("2026-08-17T23:00:00+07:00")
    assert loaded.end_at == dt("2026-08-18T01:00:00+07:00")


def test_create_rejects_invalid_events(repo):
    with pytest.raises(InvalidEvent):
        repo.create_event(make_event(title="   "))
    with pytest.raises(InvalidEvent):
        repo.create_event(
            make_event(start="2026-08-17T10:00:00+07:00", end="2026-08-17T09:00:00+07:00")
        )
    with pytest.raises(InvalidEvent):
        repo.create_event(
            make_event(start="2026-08-17T09:00:00+07:00", end="2026-08-17T09:00:00+07:00")
        )
    with pytest.raises(InvalidEvent):
        repo.create_event(make_event(timezone="Not/AZone"))
    with pytest.raises(InvalidEvent):
        repo.create_event(
            Event(
                title="Naive",
                start_at=datetime.fromisoformat("2026-08-17T09:00:00"),
                end_at=dt("2026-08-17T09:30:00+07:00"),
                timezone=TZ,
            )
        )


def test_list_events_ordered_by_start(repo):
    repo.create_event(
        make_event(start="2026-08-17T10:00:00+07:00", end="2026-08-17T10:30:00+07:00")
    )
    repo.create_event(
        make_event(start="2026-08-17T08:00:00+07:00", end="2026-08-17T08:30:00+07:00")
    )
    repo.create_event(make_event(start="2026-08-17T09:00:00+07:00"))
    events = repo.list_events()
    assert [e.start_at for e in events] == [
        dt("2026-08-17T08:00:00+07:00"),
        dt("2026-08-17T09:00:00+07:00"),
        dt("2026-08-17T10:00:00+07:00"),
    ]


def test_delete_event(repo):
    saved = repo.create_event(make_event())
    repo.delete_event(saved.id)
    with pytest.raises(EventNotFound):
        repo.get_event(saved.id)


def test_delete_nonexistent_event_raises(repo):
    with pytest.raises(EventNotFound):
        repo.delete_event(999)


# -------------------------------------------------------------------- update


def test_update_event_persists_changes(repo):
    saved = repo.create_event(make_event())
    updated = repo.update_event(
        saved.id,
        title="Retro",
        location="Room B",
        start_at=dt("2026-08-17T08:00:00+07:00"),
        end_at=dt("2026-08-17T08:30:00+07:00"),
    )
    assert updated.id == saved.id
    assert updated.created_at == saved.created_at
    assert updated.updated_at >= saved.updated_at

    loaded = repo.get_event(saved.id)
    assert loaded.title == "Retro"
    assert loaded.location == "Room B"
    assert loaded.start_at == dt("2026-08-17T08:00:00+07:00")
    assert loaded.end_at == dt("2026-08-17T08:30:00+07:00")
    assert loaded.created_at == saved.created_at


def test_update_no_args_changes_nothing_but_updated_at(repo):
    saved = repo.create_event(make_event(location="Room A"))
    updated = repo.update_event(saved.id)
    assert updated.updated_at >= saved.updated_at
    loaded = repo.get_event(saved.id)
    assert loaded.title == "Standup"
    assert loaded.location == "Room A"
    assert loaded.start_at == dt("2026-08-17T09:00:00+07:00")
    assert loaded.end_at == dt("2026-08-17T09:30:00+07:00")


def test_update_clear_rrule(repo):
    saved = repo.create_event(make_recurring_event())
    assert saved.rrule == "FREQ=WEEKLY;BYDAY=MO"
    repo.update_event(saved.id, clear_rrule=True)
    assert repo.get_event(saved.id).rrule is None


def test_update_to_invalid_state_raises_and_keeps_stored(repo):
    saved = repo.create_event(make_event())
    with pytest.raises(InvalidEvent):
        repo.update_event(saved.id, end_at=dt("2026-08-17T08:00:00+07:00"))
    loaded = repo.get_event(saved.id)
    assert loaded.start_at == dt("2026-08-17T09:00:00+07:00")
    assert loaded.end_at == dt("2026-08-17T09:30:00+07:00")


# --------------------------------------------------------------- occurrences


def test_occurrences_one_time_event_fields(repo):
    saved = repo.create_event(
        make_event(
            title="Lecture",
            location="Auditorium",
            event_type=EventType.CLASS,
            priority=Priority.CRITICAL,
            privacy=Privacy.PRIVATE,
            status=EventStatus.ACTIVE,
        )
    )
    occs = repo.get_occurrences(
        dt("2026-08-17T00:00:00+07:00"), dt("2026-08-18T00:00:00+07:00")
    )
    assert len(occs) == 1
    occ = occs[0]
    assert occ.event_id == saved.id
    assert occ.occurrence_start == dt("2026-08-17T09:00:00+07:00")
    assert occ.occurrence_end == dt("2026-08-17T09:30:00+07:00")
    assert occ.title == "Lecture"
    assert occ.location == "Auditorium"
    assert occ.event_type is EventType.CLASS
    assert occ.priority is Priority.CRITICAL
    assert occ.privacy is Privacy.PRIVATE
    assert occ.status is EventStatus.ACTIVE


def test_occurrences_excludes_event_ending_at_range_start(repo):
    repo.create_event(make_event(start="2026-08-17T09:00:00+07:00", end="2026-08-17T10:00:00+07:00"))
    occs = repo.get_occurrences(
        dt("2026-08-17T10:00:00+07:00"), dt("2026-08-17T11:00:00+07:00")
    )
    assert occs == []


def test_occurrences_ordering_same_start_by_event_id(repo):
    first = repo.create_event(make_event())
    second = repo.create_event(make_event(title="Second"))
    occs = repo.get_occurrences(
        dt("2026-08-17T00:00:00+07:00"), dt("2026-08-18T00:00:00+07:00")
    )
    assert [occ.event_id for occ in occs] == [first.id, second.id]


def test_occurrences_ordering_ascending_by_start(repo):
    repo.create_event(make_event(start="2026-08-17T09:00:00+07:00"))
    repo.create_event(make_event(start="2026-08-17T08:00:00+07:00"))
    occs = repo.get_occurrences(
        dt("2026-08-17T00:00:00+07:00"), dt("2026-08-18T00:00:00+07:00")
    )
    assert occ_starts(occs) == [
        dt("2026-08-17T08:00:00+07:00"),
        dt("2026-08-17T09:00:00+07:00"),
    ]


# ------------------------------------------------- weekly recurrence via repo


def test_recurring_event_two_weeks(repo):
    saved = repo.create_event(make_recurring_event())
    occs = repo.get_occurrences(
        dt("2026-08-17T00:00:00+07:00"), dt("2026-08-31T00:00:00+07:00")
    )
    assert occ_starts(occs) == [
        dt("2026-08-17T07:00:00+07:00"),
        dt("2026-08-24T07:00:00+07:00"),
    ]
    assert [occ.occurrence_end for occ in occs] == [
        dt("2026-08-17T07:45:00+07:00"),
        dt("2026-08-24T07:45:00+07:00"),
    ]


def test_recurring_event_multiple_weekdays(repo):
    repo.create_event(make_recurring_event(rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR"))
    occs = repo.get_occurrences(
        dt("2026-08-17T00:00:00+07:00"), dt("2026-09-01T00:00:00+07:00")
    )
    assert occ_starts(occs) == [
        dt("2026-08-17T07:00:00+07:00"),
        dt("2026-08-19T07:00:00+07:00"),
        dt("2026-08-21T07:00:00+07:00"),
        dt("2026-08-24T07:00:00+07:00"),
        dt("2026-08-26T07:00:00+07:00"),
        dt("2026-08-28T07:00:00+07:00"),
        dt("2026-08-31T07:00:00+07:00"),
    ]


def test_recurring_event_until_stops_series(repo):
    repo.create_event(make_recurring_event(rrule="FREQ=WEEKLY;BYDAY=MO;UNTIL=20260824"))
    occs = repo.get_occurrences(
        dt("2026-08-17T00:00:00+07:00"), dt("2026-09-01T00:00:00+07:00")
    )
    assert occ_starts(occs) == [
        dt("2026-08-17T07:00:00+07:00"),
        dt("2026-08-24T07:00:00+07:00"),
    ]


def test_create_with_invalid_rrule_raises(repo):
    with pytest.raises(InvalidRecurrence):
        repo.create_event(make_recurring_event(rrule="FREQ=DAILY"))


def test_get_event_occurrences(repo):
    saved = repo.create_event(make_recurring_event())
    occs = repo.get_event_occurrences(
        saved.id, dt("2026-08-17T00:00:00+07:00"), dt("2026-08-31T00:00:00+07:00")
    )
    assert [occ.event_id for occ in occs] == [saved.id, saved.id]


def test_max_duration(repo):
    assert repo.max_duration() == timedelta(0)
    repo.create_event(make_event())
    repo.create_event(make_event(start="2026-08-17T09:00:00+07:00", end="2026-08-17T10:00:00+07:00"))
    assert repo.max_duration() == timedelta(hours=1)


# ---------------------------------------------------------------- exceptions


def test_cancel_exception_removes_occurrence(repo):
    saved = repo.create_event(make_recurring_event())
    repo.add_exception(
        RecurrenceException(
            event_id=saved.id,
            occurrence_start=dt("2026-08-24T07:00:00+07:00"),
            action=RecurrenceExceptionAction.CANCEL,
        )
    )
    occs = repo.get_occurrences(
        dt("2026-08-17T00:00:00+07:00"), dt("2026-09-01T00:00:00+07:00")
    )
    assert occ_starts(occs) == [
        dt("2026-08-17T07:00:00+07:00"),
        dt("2026-08-31T07:00:00+07:00"),
    ]


def test_modify_exception_overrides_occurrence(repo):
    saved = repo.create_event(make_recurring_event())
    repo.add_exception(
        RecurrenceException(
            event_id=saved.id,
            occurrence_start=dt("2026-08-31T07:00:00+07:00"),
            action=RecurrenceExceptionAction.MODIFY,
            start_at=dt("2026-08-31T08:00:00+07:00"),
            end_at=dt("2026-08-31T08:45:00+07:00"),
            location="B201",
        )
    )
    occs = repo.get_occurrences(
        dt("2026-08-17T00:00:00+07:00"), dt("2026-09-01T00:00:00+07:00")
    )
    assert dt("2026-08-31T07:00:00+07:00") not in occ_starts(occs)
    modified = next(
        occ for occ in occs if occ.occurrence_start == dt("2026-08-31T08:00:00+07:00")
    )
    assert modified.occurrence_end == dt("2026-08-31T08:45:00+07:00")
    assert modified.location == "B201"


def test_modify_exception_start_only_derives_end(repo):
    saved = repo.create_event(make_recurring_event())
    repo.add_exception(
        RecurrenceException(
            event_id=saved.id,
            occurrence_start=dt("2026-08-31T07:00:00+07:00"),
            action=RecurrenceExceptionAction.MODIFY,
            start_at=dt("2026-08-31T08:00:00+07:00"),
        )
    )
    occs = repo.get_occurrences(
        dt("2026-08-17T00:00:00+07:00"), dt("2026-09-01T00:00:00+07:00")
    )
    modified = next(
        occ for occ in occs if occ.occurrence_start == dt("2026-08-31T08:00:00+07:00")
    )
    assert modified.occurrence_end == dt("2026-08-31T08:45:00+07:00")


def test_exception_on_one_time_event_raises(repo):
    saved = repo.create_event(make_event())
    with pytest.raises(InvalidException):
        repo.add_exception(
            RecurrenceException(
                event_id=saved.id,
                occurrence_start=dt("2026-08-17T09:00:00+07:00"),
                action=RecurrenceExceptionAction.CANCEL,
            )
        )


def test_duplicate_exception_raises(repo):
    saved = repo.create_event(make_recurring_event())
    exception = RecurrenceException(
        event_id=saved.id,
        occurrence_start=dt("2026-08-24T07:00:00+07:00"),
        action=RecurrenceExceptionAction.CANCEL,
    )
    repo.add_exception(exception)
    with pytest.raises(InvalidException):
        repo.add_exception(exception)


def test_cancel_exception_with_overrides_raises(repo):
    saved = repo.create_event(make_recurring_event())
    with pytest.raises(InvalidException):
        repo.add_exception(
            RecurrenceException(
                event_id=saved.id,
                occurrence_start=dt("2026-08-24T07:00:00+07:00"),
                action=RecurrenceExceptionAction.CANCEL,
                location="B201",
            )
        )


def test_remove_exception_restores_occurrence(repo):
    saved = repo.create_event(make_recurring_event())
    repo.add_exception(
        RecurrenceException(
            event_id=saved.id,
            occurrence_start=dt("2026-08-24T07:00:00+07:00"),
            action=RecurrenceExceptionAction.CANCEL,
        )
    )
    assert dt("2026-08-24T07:00:00+07:00") not in occ_starts(
        repo.get_occurrences(dt("2026-08-17T00:00:00+07:00"), dt("2026-09-01T00:00:00+07:00"))
    )
    repo.remove_exception(saved.id, dt("2026-08-24T07:00:00+07:00"))
    occs = repo.get_occurrences(
        dt("2026-08-17T00:00:00+07:00"), dt("2026-09-01T00:00:00+07:00")
    )
    assert dt("2026-08-24T07:00:00+07:00") in occ_starts(occs)


def test_list_exceptions(repo):
    saved = repo.create_event(make_recurring_event())
    repo.add_exception(
        RecurrenceException(
            event_id=saved.id,
            occurrence_start=dt("2026-08-24T07:00:00+07:00"),
            action=RecurrenceExceptionAction.CANCEL,
        )
    )
    repo.add_exception(
        RecurrenceException(
            event_id=saved.id,
            occurrence_start=dt("2026-08-31T07:00:00+07:00"),
            action=RecurrenceExceptionAction.MODIFY,
            start_at=dt("2026-08-31T08:00:00+07:00"),
        )
    )
    excs = repo.list_exceptions()
    assert len(excs) == 2
    assert sorted(e.action for e in excs) == [
        RecurrenceExceptionAction.CANCEL,
        RecurrenceExceptionAction.MODIFY,
    ]
    excs_filtered = repo.list_exceptions(saved.id)
    assert len(excs_filtered) == 2
    assert excs_filtered[0].occurrence_start == dt("2026-08-24T07:00:00+07:00")


# ---------------------------------------------------------------- reminders


def test_add_and_list_reminders_ordered(repo):
    saved = repo.create_event(make_event())
    repo.add_reminder(saved.id, 60)
    repo.add_reminder(saved.id, 15, sound=False, critical=True)
    repo.add_reminder(saved.id, 5)
    repo.add_reminder(saved.id, 0)
    reminders = repo.list_reminders(saved.id)
    assert [r.minutes_before for r in reminders] == [0, 5, 15, 60]
    by_minutes = {r.minutes_before: r for r in reminders}
    assert by_minutes[15].sound is False
    assert by_minutes[15].critical is True
    assert by_minutes[60].sound is True
    assert by_minutes[60].critical is False


def test_duplicate_reminder_rejected(repo):
    saved = repo.create_event(make_event())
    repo.add_reminder(saved.id, 15)
    with pytest.raises(InvalidReminder):
        repo.add_reminder(saved.id, 15)


def test_negative_minutes_rejected(repo):
    saved = repo.create_event(make_event())
    with pytest.raises(InvalidReminder):
        repo.add_reminder(saved.id, -5)


def test_remove_reminder(repo):
    saved = repo.create_event(make_event())
    repo.add_reminder(saved.id, 15)
    repo.remove_reminder(saved.id, 15)
    assert repo.list_reminders(saved.id) == []
    with pytest.raises(InvalidReminder):
        repo.remove_reminder(saved.id, 15)


def test_replace_reminders(repo):
    saved = repo.create_event(make_event())
    repo.add_reminder(saved.id, 60)
    replaced = repo.replace_reminders(saved.id, [15, 5])
    assert [r.minutes_before for r in replaced] == [5, 15]
    assert [r.minutes_before for r in repo.list_reminders(saved.id)] == [5, 15]
    with pytest.raises(InvalidReminder):
        repo.replace_reminders(saved.id, [15, 15])


def test_add_reminder_nonexistent_event(repo):
    with pytest.raises(EventNotFound):
        repo.add_reminder(999, 15)


# --------------------------------------------------------- notification log


def test_record_and_check_notification(repo):
    event = repo.create_event(make_event())
    repo.add_reminder(event.id, 15)
    occ_start = event.start_at
    assert repo.notification_exists(event.id, occ_start, 15) is False
    repo.record_notification(event.id, occ_start, 15)
    assert repo.notification_exists(event.id, occ_start, 15) is True
    assert repo.notification_exists(event.id, occ_start, 5) is False


def test_record_notification_is_idempotent(repo):
    event = repo.create_event(make_event())
    repo.record_notification(event.id, event.start_at, 15)
    repo.record_notification(event.id, event.start_at, 15)
    assert repo.notification_exists(event.id, event.start_at, 15) is True
    assert repo.any_notification(event.id, event.start_at) is True


def test_any_notification_spans_all_minutes(repo):
    event = repo.create_event(make_event())
    repo.record_notification(event.id, event.start_at, 5)
    assert repo.any_notification(event.id, event.start_at) is True
    assert repo.any_notification(event.id, event.start_at + timedelta(hours=1)) is False


def test_notification_log_cascades_on_delete(repo):
    event = repo.create_event(make_event())
    repo.record_notification(event.id, event.start_at, 15)
    repo.delete_event(event.id)
    assert repo.any_notification(event.id, event.start_at) is False


def test_max_reminder_offset(repo):
    assert repo.max_reminder_offset() is None
    first = repo.create_event(make_event())
    repo.add_reminder(first.id, 15)
    second = repo.create_event(make_event(start="2026-08-18T10:00:00+07:00", end="2026-08-18T10:30:00+07:00"))
    repo.add_reminder(second.id, 45)
    assert repo.max_reminder_offset() == 45