"""Conflict-detection tests for :func:`find_conflicts`.

Overlap semantics are half-open intervals ``[start, end)``: adjacent events
never conflict. All datetimes are timezone-aware in ``+07:00``
(``Asia/Ho_Chi_Minh``).
"""

from datetime import datetime, timedelta, timezone

import pytest

from hyprschedule.conflicts import Conflict, find_conflicts
from hyprschedule.database import Database
from hyprschedule.errors import InvalidEvent
from hyprschedule.models import (
    Event,
    RecurrenceException,
    RecurrenceExceptionAction,
)
from hyprschedule.repository import EventRepository

TZ_OFFSET = timezone(timedelta(hours=7))

SERIES_START_DAY = "2026-08-10"  # a Monday
CONFLICT_DAY = "2026-08-24"  # also a Monday


def _dt(day: str, clock: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}:00+07:00")


def _make_event(repo, title, day, start, end, *, rrule=None):
    return repo.create_event(
        Event(
            title=title,
            start_at=_dt(day, start),
            end_at=_dt(day, end),
            timezone="Asia/Ho_Chi_Minh",
            rrule=rrule,
        )
    )


def _make_recurring(repo, title, start_day, start, end, *, byday="MO"):
    return _make_event(
        repo, title, start_day, start, end, rrule=f"FREQ=WEEKLY;BYDAY={byday}"
    )


@pytest.fixture
def repo(tmp_path):
    db = Database(tmp_path / "schedule.db")
    db.initialize()
    try:
        yield EventRepository(db)
    finally:
        db.close()


def test_basic_overlap_forms_single_group(repo):
    a = _make_event(repo, "A", CONFLICT_DAY, "07:00", "07:45")
    b = _make_event(repo, "B", CONFLICT_DAY, "07:30", "08:00")

    conflicts = find_conflicts(repo, a.start_at, a.end_at)
    assert len(conflicts) == 1
    assert len(conflicts[0].occurrences) == 2
    assert {occ.event_id for occ in conflicts[0].occurrences} == {a.id, b.id}


def test_adjacent_events_never_conflict(repo):
    a = _make_event(repo, "A", CONFLICT_DAY, "07:00", "07:45")
    b = _make_event(repo, "B", CONFLICT_DAY, "07:45", "08:30")

    assert find_conflicts(repo, a.start_at, a.end_at) == []
    assert find_conflicts(repo, b.start_at, b.end_at) == []


def test_fully_contained_event_conflicts(repo):
    a = _make_event(repo, "A", CONFLICT_DAY, "07:00", "09:00")
    b = _make_event(repo, "B", CONFLICT_DAY, "08:00", "08:30")

    conflicts = find_conflicts(repo, a.start_at, a.end_at)
    assert len(conflicts) == 1
    assert len(conflicts[0]) == 2
    assert {occ.event_id for occ in conflicts[0].occurrences} == {a.id, b.id}


def test_identical_intervals_conflict(repo):
    a = _make_event(repo, "A", CONFLICT_DAY, "07:00", "07:45")
    b = _make_event(repo, "B", CONFLICT_DAY, "07:00", "07:45")

    conflicts = find_conflicts(repo, a.start_at, a.end_at)
    assert len(conflicts) == 1
    assert len(conflicts[0]) == 2


def test_recurring_series_conflicts_with_one_off(repo):
    series = _make_recurring(repo, "Lecture", SERIES_START_DAY, "07:00", "07:45")
    meeting = _make_event(repo, "One-off", CONFLICT_DAY, "07:30", "08:00")

    conflicts = find_conflicts(repo, meeting.start_at, meeting.end_at)
    assert len(conflicts) == 1
    group = conflicts[0]
    assert len(group) == 2
    assert {occ.event_id for occ in group.occurrences} == {series.id, meeting.id}
    series_occ = next(occ for occ in group.occurrences if occ.event_id == series.id)
    assert series_occ.occurrence_start == _dt(CONFLICT_DAY, "07:00")


def test_cancelled_occurrence_does_not_conflict(repo):
    series = _make_recurring(repo, "Lecture", SERIES_START_DAY, "07:00", "07:45")
    meeting = _make_event(repo, "One-off", CONFLICT_DAY, "07:30", "08:00")
    repo.add_exception(
        RecurrenceException(
            event_id=series.id,
            occurrence_start=_dt(CONFLICT_DAY, "07:00"),
            action=RecurrenceExceptionAction.CANCEL,
        )
    )

    assert find_conflicts(repo, meeting.start_at, meeting.end_at) == []


def test_modified_occurrence_moved_out_of_conflict(repo):
    series = _make_recurring(repo, "Lecture", SERIES_START_DAY, "07:00", "07:45")
    meeting = _make_event(repo, "One-off", CONFLICT_DAY, "07:30", "08:00")
    repo.add_exception(
        RecurrenceException(
            event_id=series.id,
            occurrence_start=_dt(CONFLICT_DAY, "07:00"),
            action=RecurrenceExceptionAction.MODIFY,
            start_at=_dt(CONFLICT_DAY, "06:00"),
            end_at=_dt(CONFLICT_DAY, "06:30"),
        )
    )

    assert find_conflicts(repo, meeting.start_at, meeting.end_at) == []


def test_exclude_event_id_drops_self_from_conflict_group(repo):
    a = _make_event(repo, "A", CONFLICT_DAY, "07:00", "07:45")
    b = _make_event(repo, "B", CONFLICT_DAY, "07:30", "08:00")
    c = _make_event(repo, "C", CONFLICT_DAY, "07:30", "08:00")

    full = find_conflicts(repo, a.start_at, a.end_at)
    assert len(full) == 1
    assert {occ.event_id for occ in full[0].occurrences} == {a.id, b.id, c.id}

    excluded = find_conflicts(repo, a.start_at, a.end_at, exclude_event_id=a.id)
    assert len(excluded) == 1
    assert {occ.event_id for occ in excluded[0].occurrences} == {b.id, c.id}


def test_exclude_event_id_single_remaining_occurrence_reports_nothing(repo):
    a = _make_event(repo, "A", CONFLICT_DAY, "07:00", "07:45")
    b = _make_event(repo, "B", CONFLICT_DAY, "07:30", "08:00")

    assert find_conflicts(repo, a.start_at, a.end_at, exclude_event_id=a.id) == []
    assert len(find_conflicts(repo, a.start_at, a.end_at)) == 1


def test_window_spanning_shared_boundary_instant(repo):
    a = _make_event(repo, "A", CONFLICT_DAY, "07:00", "07:45")
    b = _make_event(repo, "B", CONFLICT_DAY, "07:45", "08:30")

    assert (
        find_conflicts(repo, _dt(CONFLICT_DAY, "07:30"), _dt(CONFLICT_DAY, "08:00"))
        == []
    )


def test_invalid_window_raises(repo):
    start = _dt(CONFLICT_DAY, "07:00")
    with pytest.raises(InvalidEvent):
        find_conflicts(repo, start, start)
    with pytest.raises(InvalidEvent):
        find_conflicts(repo, start, _dt(CONFLICT_DAY, "06:00"))


def test_conflicts_are_deterministic_and_sorted(repo):
    a = _make_event(repo, "A", CONFLICT_DAY, "07:00", "07:45")
    b = _make_event(repo, "B", CONFLICT_DAY, "07:30", "08:00")
    c = _make_event(repo, "C", CONFLICT_DAY, "07:50", "08:20")

    first = find_conflicts(repo, a.start_at, c.end_at)
    second = find_conflicts(repo, a.start_at, c.end_at)

    assert isinstance(first, list)
    assert all(isinstance(group, Conflict) for group in first)
    assert first == second
    assert len(first) == 1
    starts = [occ.occurrence_start for occ in first[0].occurrences]
    assert starts == sorted(starts)
