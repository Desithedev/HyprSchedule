from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from hyprschedule.errors import InvalidRecurrence
from hyprschedule.recurrence import expand_weekly, parse_weekly_rule

TZ = "Asia/Ho_Chi_Minh"
TZ_INFO = ZoneInfo(TZ)


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def make_rule(
    rrule: str,
    start: str = "2026-08-17T07:00:00+07:00",
    end: str = "2026-08-17T07:45:00+07:00",
):
    return parse_weekly_rule(
        rrule, start_at=dt(start), end_at=dt(end), timezone=TZ
    )


def expand(
    rrule: str,
    range_start: str,
    range_end: str,
    start: str = "2026-08-17T07:00:00+07:00",
    end: str = "2026-08-17T07:45:00+07:00",
):
    return expand_weekly(make_rule(rrule, start, end), dt(range_start), dt(range_end))


# --------------------------------------------------------------- parse


def test_parse_byday_mo_and_until():
    rule = make_rule("FREQ=WEEKLY;BYDAY=MO;UNTIL=20260824")
    assert rule.byday == (0,)
    assert rule.until == date(2026, 8, 24)


def test_parse_byday_multiple_sorted():
    rule = make_rule("FREQ=WEEKLY;BYDAY=FR,MO,WE")
    assert rule.byday == (0, 2, 4)
    assert rule.until is None


def test_parse_byday_omitted_uses_dtstart_weekday():
    rule = make_rule("FREQ=WEEKLY")
    assert rule.byday == (0,)


def test_parse_rule_fields():
    rule = make_rule("FREQ=WEEKLY;BYDAY=MO")
    assert rule.dtstart == date(2026, 8, 17)
    assert rule.start_time == time(7, 0)
    assert rule.duration == timedelta(minutes=45)
    assert rule.timezone == TZ


def test_parse_rejects_freq_daily():
    with pytest.raises(InvalidRecurrence) as exc_info:
        make_rule("FREQ=DAILY")
    assert "WEEKLY" in str(exc_info.value)


def test_parse_rejects_unknown_property():
    with pytest.raises(InvalidRecurrence) as exc_info:
        make_rule("FREQ=WEEKLY;COUNT=5")
    assert "supported subset" in str(exc_info.value)


def test_parse_rejects_invalid_weekday():
    with pytest.raises(InvalidRecurrence):
        make_rule("FREQ=WEEKLY;BYDAY=XX")


def test_parse_rejects_invalid_until_format():
    with pytest.raises(InvalidRecurrence):
        make_rule("FREQ=WEEKLY;UNTIL=24-08-2026")


def test_parse_rejects_empty_rrule():
    with pytest.raises(InvalidRecurrence):
        make_rule("")
    with pytest.raises(InvalidRecurrence):
        make_rule("   ")


# --------------------------------------------------------------- expand


def test_expand_single_weekday():
    occs = expand(
        "FREQ=WEEKLY;BYDAY=MO",
        "2026-08-17T00:00:00+07:00",
        "2026-08-31T00:00:00+07:00",
    )
    assert occs == [
        dt("2026-08-17T07:00:00+07:00"),
        dt("2026-08-24T07:00:00+07:00"),
    ]


def test_expand_multiple_weekdays_exact_dates():
    occs = expand(
        "FREQ=WEEKLY;BYDAY=MO,WE,FR",
        "2026-08-17T00:00:00+07:00",
        "2026-09-01T00:00:00+07:00",
    )
    assert occs == [
        dt("2026-08-17T07:00:00+07:00"),
        dt("2026-08-19T07:00:00+07:00"),
        dt("2026-08-21T07:00:00+07:00"),
        dt("2026-08-24T07:00:00+07:00"),
        dt("2026-08-26T07:00:00+07:00"),
        dt("2026-08-28T07:00:00+07:00"),
        dt("2026-08-31T07:00:00+07:00"),
    ]


def test_expand_start_boundary_no_occurrence_before_dtstart():
    occs = expand(
        "FREQ=WEEKLY;BYDAY=MO",
        "2026-08-17T00:00:00+07:00",
        "2026-09-01T00:00:00+07:00",
        start="2026-08-19T07:00:00+07:00",
        end="2026-08-19T07:45:00+07:00",
    )
    assert occs == [
        dt("2026-08-24T07:00:00+07:00"),
        dt("2026-08-31T07:00:00+07:00"),
    ]


def test_expand_until_boundary():
    occs = expand(
        "FREQ=WEEKLY;BYDAY=MO;UNTIL=20260824",
        "2026-08-17T00:00:00+07:00",
        "2026-08-31T00:00:00+07:00",
    )
    assert occs == [
        dt("2026-08-17T07:00:00+07:00"),
        dt("2026-08-24T07:00:00+07:00"),
    ]


def test_expand_range_far_before_series_is_empty():
    occs = expand(
        "FREQ=WEEKLY;BYDAY=MO",
        "2026-01-01T00:00:00+07:00",
        "2026-01-08T00:00:00+07:00",
    )
    assert occs == []


def test_expand_range_after_until_is_empty():
    occs = expand(
        "FREQ=WEEKLY;BYDAY=MO;UNTIL=20260824",
        "2026-09-01T00:00:00+07:00",
        "2026-09-08T00:00:00+07:00",
    )
    assert occs == []


def test_expand_without_until_is_safe_later_in_year():
    occs = expand(
        "FREQ=WEEKLY;BYDAY=MO",
        "2026-09-01T00:00:00+07:00",
        "2026-09-08T00:00:00+07:00",
    )
    assert occs == [dt("2026-09-07T07:00:00+07:00")]


def test_expand_occurrences_in_event_timezone():
    occs = expand(
        "FREQ=WEEKLY;BYDAY=MO",
        "2026-08-17T00:00:00+07:00",
        "2026-08-31T00:00:00+07:00",
    )
    assert occs
    for occ in occs:
        assert occ.tzinfo is TZ_INFO
        assert occ.utcoffset() == timedelta(hours=7)


def test_expand_is_bounded_for_old_series():
    occs = expand(
        "FREQ=WEEKLY;BYDAY=MO",
        "2026-08-17T00:00:00+07:00",
        "2026-08-24T00:00:00+07:00",
        start="2020-01-06T07:00:00+07:00",
        end="2020-01-06T07:45:00+07:00",
    )
    assert occs == [dt("2026-08-17T07:00:00+07:00")]