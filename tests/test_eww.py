"""Phase 4 backend tests: the ``schedctl eww`` payload.

All time-dependent behavior is driven by the frozen ``clock`` singleton; the
real current time is never used. Events are seeded through the repository
with explicit +07:00 datetimes.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from hyprschedule.config import load_config
from hyprschedule.eww import build_payload, format_duration, progress_bar_text
from hyprschedule.models import (
    Event,
    EventType,
    Priority,
    RecurrenceException,
    RecurrenceExceptionAction,
)

TZ = "Asia/Ho_Chi_Minh"


def dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def freeze(frozen_clock, iso: str) -> None:
    frozen_clock(dt(iso))


def add(repo, title: str, start: str, end: str, **kwargs) -> Event:
    defaults = dict(timezone=TZ, event_type=EventType.OTHER, priority=Priority.NORMAL)
    defaults.update(kwargs)
    return repo.create_event(
        Event(title=title, start_at=dt(start), end_at=dt(end), **defaults)
    )


def payload(repo, now: str) -> dict:
    return build_payload(load_config(), repo, dt(now))


# ------------------------------------------------------------------ CLI level


def test_cli_eww_json_purity(cli_env, frozen_clock, run):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    code, captured = run(["eww"])
    assert code == 0
    assert captured.err == ""
    data = json.loads(captured.out)
    assert data["date"]["iso"] == "2026-08-13"


def test_cli_eww_help_present(cli_env, capsys):
    with pytest.raises(SystemExit) as excinfo:
        from hyprschedule.cli import main

        main(["eww", "--help"])
    assert excinfo.value.code == 0
    assert "usage: schedctl" in capsys.readouterr().out


def test_eww_is_read_only(cli_env, frozen_clock, run):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    run(["eww"])
    assert cli_env.repo.list_events() == []


# --------------------------------------------------------------- empty state


def test_empty_day(cli_env, frozen_clock, run):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    code, captured = run(["eww"])
    assert code == 0
    data = json.loads(captured.out)
    assert data["generated_at"].endswith("+07:00")
    assert data["timezone"] == TZ
    assert data["date"] == {
        "iso": "2026-08-13",
        "weekday": "Thứ Năm",
        "display": "Thứ Năm · 13/08",
    }
    assert data["now"] == {"time": "19:45"}
    assert data["current"] is None
    assert data["next"] is None
    assert data["events"] == []
    assert data["free_time"] is None or data["free_time"]["duration_minutes"] > 0
    assert data["tomorrow"] == {"count": 0, "display": "0 lịch"}
    assert data["widget"] == {"show_free_time": True, "show_tomorrow_count": True}


# -------------------------------------------------------------- current event


def test_current_event_progress_and_countdown(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(
        repo,
        "Dạy 12A1",
        "2026-08-13T19:30:00+07:00",
        "2026-08-13T20:15:00+07:00",
        location="A203",
        event_type=EventType.CLASS,
        priority=Priority.HIGH,
    )
    data = payload(repo, "2026-08-13T19:45:00+07:00")
    current = data["current"]
    assert current is not None
    assert current["event_id"] == 1
    assert current["title"] == "Dạy 12A1"
    assert current["location"] == "A203"
    assert current["start"] == "2026-08-13T19:30:00+07:00"
    assert current["end"] == "2026-08-13T20:15:00+07:00"
    assert current["start_display"] == "19:30"
    assert current["end_display"] == "20:15"
    assert current["remaining_seconds"] == 1800
    assert current["remaining_display"] == "30 phút"
    assert abs(current["progress"] - 1 / 3) < 0.001
    assert 0.0 <= current["progress"] <= 1.0
    assert current["progress_pct"] == 33
    assert current["event_type"] == "class"
    assert current["priority"] == "high"
    assert current["progress_bar"] == "███░░░░░░░"


def test_current_event_just_started_progress_zero(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:30:00+07:00")
    add(repo, "A", "2026-08-13T19:30:00+07:00", "2026-08-13T20:00:00+07:00")
    current = payload(repo, "2026-08-13T19:30:00+07:00")["current"]
    assert current["progress"] == 0.0
    assert current["remaining_display"] == "30 phút"


def test_current_event_progress_clamped(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:50:00+07:00")
    add(repo, "A", "2026-08-13T19:30:00+07:00", "2026-08-13T20:00:00+07:00")
    current = payload(repo, "2026-08-13T19:50:00+07:00")["current"]
    assert abs(current["progress"] - 2 / 3) < 0.001
    assert current["remaining_display"] == "10 phút"


def test_no_current_event_when_nothing_active(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "A", "2026-08-13T18:00:00+07:00", "2026-08-13T19:00:00+07:00")
    add(repo, "B", "2026-08-13T20:00:00+07:00", "2026-08-13T21:00:00+07:00")
    assert payload(repo, "2026-08-13T19:45:00+07:00")["current"] is None


# ----------------------------------------------------------------- next event


def test_next_event_countdown(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Họp tổ", "2026-08-13T21:00:00+07:00", "2026-08-13T22:00:00+07:00",
        location="Phòng họp", event_type=EventType.MEETING, priority=Priority.HIGH)
    add(repo, "Sớm hơn", "2026-08-13T20:00:00+07:00", "2026-08-13T20:30:00+07:00")
    data = payload(repo, "2026-08-13T19:45:00+07:00")
    nxt = data["next"]
    assert nxt["title"] == "Sớm hơn"
    assert nxt["starts_in_seconds"] == 900
    assert nxt["starts_in_display"] == "15 phút"


def test_next_event_is_nearest_not_parent(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T08:00:00+07:00")
    add(repo, "Dạy 11A3", "2026-08-10T09:00:00+07:00", "2026-08-10T09:45:00+07:00",
        rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR")
    add(repo, "Sớm hơn", "2026-08-13T09:00:00+07:00", "2026-08-13T09:30:00+07:00")
    nxt = payload(repo, "2026-08-13T08:00:00+07:00")["next"]
    assert nxt["title"] == "Sớm hơn"
    assert nxt["event_id"] == 2


def test_next_skips_current_event(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Đang diễn ra", "2026-08-13T19:30:00+07:00", "2026-08-13T20:15:00+07:00")
    add(repo, "Tiếp theo", "2026-08-13T20:30:00+07:00", "2026-08-13T21:00:00+07:00")
    data = payload(repo, "2026-08-13T19:45:00+07:00")
    assert data["current"]["title"] == "Đang diễn ra"
    assert data["next"]["title"] == "Tiếp theo"


def test_next_horizon_bounded(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Xa", "2026-09-13T10:00:00+07:00", "2026-09-13T10:30:00+07:00")
    assert payload(repo, "2026-08-13T19:45:00+07:00")["next"] is None


# ------------------------------------------------------- current + next coexist


def test_current_and_next_coexist(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Đang diễn ra", "2026-08-13T19:30:00+07:00", "2026-08-13T20:15:00+07:00")
    add(repo, "Tiếp theo", "2026-08-13T21:00:00+07:00", "2026-08-13T22:00:00+07:00")
    data = payload(repo, "2026-08-13T19:45:00+07:00")
    assert data["current"]["event_id"] == 1
    assert data["next"]["event_id"] == 2
    assert data["events"][0]["state"] == "next"
    assert data["events"][0]["event_id"] == 2


# ------------------------------------------------------------------ today list


def test_today_events_ordered_states_and_past_omitted(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T10:15:00+07:00")
    add(repo, "Đã qua", "2026-08-13T08:00:00+07:00", "2026-08-13T08:45:00+07:00")
    add(repo, "Đang diễn ra", "2026-08-13T10:00:00+07:00", "2026-08-13T10:30:00+07:00")
    add(repo, "Sắp tới 1", "2026-08-13T11:00:00+07:00", "2026-08-13T11:45:00+07:00")
    add(repo, "Sắp tới 2", "2026-08-13T12:00:00+07:00", "2026-08-13T12:45:00+07:00")
    events = payload(repo, "2026-08-13T10:15:00+07:00")["events"]
    assert [e["title"] for e in events] == ["Sắp tới 1", "Sắp tới 2"]
    assert [e["state"] for e in events] == ["next", "future"]
    assert events[0]["start_display"] == "11:00"


def test_today_events_exclude_current(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T10:15:00+07:00")
    add(repo, "Đang diễn ra", "2026-08-13T10:00:00+07:00", "2026-08-13T10:30:00+07:00")
    events = payload(repo, "2026-08-13T10:15:00+07:00")["events"]
    assert [e["title"] for e in events] == []


def test_max_events_limit_applied(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T08:00:00+07:00")
    for i in range(10):
        add(repo, f"Lịch {i}", f"2026-08-13T{9 + i:02d}:00:00+07:00",
            f"2026-08-13T{9 + i:02d}:45:00+07:00")
    events = payload(repo, "2026-08-13T08:00:00+07:00")["events"]
    assert len(events) == 6
    assert events[0]["state"] == "next"
    assert all(e["state"] == "future" for e in events[1:])


def test_late_event_after_day_end_still_today(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T22:00:00+07:00")
    add(repo, "Khuya", "2026-08-13T23:30:00+07:00", "2026-08-14T01:00:00+07:00")
    data = payload(repo, "2026-08-13T22:00:00+07:00")
    assert [e["title"] for e in data["events"]] == ["Khuya"]


# ---------------------------------------------------------------- recurrence


def test_recurring_occurrence_expanded_not_parent(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-17T06:30:00+07:00")
    add(repo, "Dạy 11A3", "2026-08-10T07:00:00+07:00", "2026-08-10T07:45:00+07:00",
        rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR")
    data = payload(repo, "2026-08-17T06:30:00+07:00")
    events = data["events"]
    assert len(events) == 1
    assert events[0]["title"] == "Dạy 11A3"
    assert events[0]["start_display"] == "07:00"
    assert events[0]["state"] == "next"
    assert data["next"]["start_display"] == "07:00"


def test_cancelled_recurrence_occurrence_not_counted(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-16T20:00:00+07:00")
    add(repo, "Dạy", "2026-08-10T07:00:00+07:00", "2026-08-10T07:45:00+07:00",
        rrule="FREQ=WEEKLY;BYDAY=MO")
    add(repo, "Họp", "2026-08-17T09:00:00+07:00", "2026-08-17T09:30:00+07:00")
    assert payload(repo, "2026-08-16T20:00:00+07:00")["tomorrow"]["count"] == 2

    dong_ho = repo.search_events("Dạy")[0]
    repo.add_exception(
        RecurrenceException(
            event_id=dong_ho.id,
            occurrence_start=dt("2026-08-17T07:00:00+07:00"),
            action=RecurrenceExceptionAction.CANCEL,
        )
    )
    assert payload(repo, "2026-08-16T20:00:00+07:00")["tomorrow"]["count"] == 1


def test_modified_recurrence_reflects_override(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-16T20:00:00+07:00")
    event = add(repo, "Dạy 12A1", "2026-08-10T07:00:00+07:00", "2026-08-10T07:45:00+07:00",
        location="A203", rrule="FREQ=WEEKLY;BYDAY=MO")
    repo.add_exception(
        RecurrenceException(
            event_id=event.id,
            occurrence_start=dt("2026-08-17T07:00:00+07:00"),
            action=RecurrenceExceptionAction.MODIFY,
            title="Đổi lịch",
            start_at=dt("2026-08-17T08:00:00+07:00"),
            end_at=dt("2026-08-17T08:30:00+07:00"),
            location="B204",
        )
    )
    data = payload(repo, "2026-08-16T20:00:00+07:00")
    assert data["events"] == []
    assert data["tomorrow"]["count"] == 1
    nxt = data["next"]
    assert nxt["title"] == "Đổi lịch"
    assert nxt["start_display"] == "08:00"
    assert nxt["end_display"] == "08:30"
    assert nxt["location"] == "B204"


# ----------------------------------------------------------------- free time


def test_free_time_between_events(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Đang diễn ra", "2026-08-13T19:30:00+07:00", "2026-08-13T20:15:00+07:00")
    add(repo, "Tiếp theo", "2026-08-13T21:00:00+07:00", "2026-08-13T22:00:00+07:00")
    free_time = payload(repo, "2026-08-13T19:45:00+07:00")["free_time"]
    assert free_time["start"] == "2026-08-13T20:15:00+07:00"
    assert free_time["end"] == "2026-08-13T21:00:00+07:00"
    assert free_time["start_display"] == "20:15"
    assert free_time["end_display"] == "21:00"
    assert free_time["duration_minutes"] == 45
    assert free_time["duration_display"] == "45 phút"


def test_free_time_starts_at_now_when_free(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T12:00:00+07:00")
    add(repo, "Chiều", "2026-08-13T14:00:00+07:00", "2026-08-13T15:00:00+07:00")
    free_time = payload(repo, "2026-08-13T12:00:00+07:00")["free_time"]
    assert free_time["start_display"] == "12:00"
    assert free_time["end_display"] == "14:00"


def test_free_time_until_day_end(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T21:30:00+07:00")
    add(repo, "Đang diễn ra", "2026-08-13T19:30:00+07:00", "2026-08-13T20:15:00+07:00")
    free_time = payload(repo, "2026-08-13T21:30:00+07:00")["free_time"]
    assert free_time["start_display"] == "21:30"
    assert free_time["end_display"] == "23:00"
    assert free_time["duration_minutes"] == 90


def test_no_free_time_when_day_covered(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Dài", "2026-08-13T19:00:00+07:00", "2026-08-13T23:30:00+07:00")
    assert payload(repo, "2026-08-13T19:45:00+07:00")["free_time"] is None


def test_no_free_time_after_day_end(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T23:30:00+07:00")
    assert payload(repo, "2026-08-13T23:30:00+07:00")["free_time"] is None


def test_free_time_requires_minimum_duration(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:55:00+07:00")
    add(repo, "Sắp tới", "2026-08-13T20:00:00+07:00", "2026-08-13T21:00:00+07:00")
    free_time = payload(repo, "2026-08-13T19:55:00+07:00")["free_time"]
    assert free_time is None or free_time["duration_minutes"] >= 15


def test_free_time_respects_configurable_minimum(cli_env, tmp_path, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:55:00+07:00")
    add(cli_env.repo, "Sắp tới", "2026-08-13T20:00:00+07:00", "2026-08-13T21:00:00+07:00")
    config_dir = tmp_path / "config" / "hyprschedule"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text("[schedule]\nmin_free_minutes = 5\n")
    free_time = build_payload(
        load_config(), cli_env.repo, dt("2026-08-13T19:55:00+07:00")
    )["free_time"]
    assert free_time is not None
    assert free_time["start_display"] == "19:55"
    assert free_time["end_display"] == "20:00"
    assert free_time["duration_minutes"] == 5


def test_free_time_before_day_start(cli_env, frozen_clock):
    freeze(frozen_clock, "2026-08-13T05:30:00+07:00")
    data = build_payload(load_config(), cli_env.repo, dt("2026-08-13T05:30:00+07:00"))
    free_time = data["free_time"]
    assert free_time is not None
    assert free_time["start_display"] == "06:00"
    assert free_time["end_display"] == "23:00"


# ------------------------------------------------------------- tomorrow count


def test_tomorrow_count_counts_occurrences(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T20:00:00+07:00")
    add(repo, "Dạy", "2026-08-10T07:00:00+07:00", "2026-08-10T07:45:00+07:00",
        rrule="FREQ=WEEKLY;BYDAY=FR")
    add(repo, "Một lần", "2026-08-14T09:00:00+07:00", "2026-08-14T09:45:00+07:00")
    add(repo, "Lần nữa", "2026-08-14T10:00:00+07:00", "2026-08-14T10:45:00+07:00")
    tomorrow = payload(repo, "2026-08-13T20:00:00+07:00")["tomorrow"]
    assert tomorrow["count"] == 3
    assert tomorrow["display"] == "3 lịch"


def test_tomorrow_count_zero_when_empty(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T20:00:00+07:00")
    assert payload(repo, "2026-08-13T20:00:00+07:00")["tomorrow"]["count"] == 0


# ----------------------------------------------------------------- countdown


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0 giây"),
        (45, "45 giây"),
        (59, "59 giây"),
        (60, "1 phút"),
        (300, "5 phút"),
        (3599, "59 phút"),
        (3600, "1 giờ"),
        (4500, "1 giờ 15 phút"),
        (7200, "2 giờ"),
        (8100, "2 giờ 15 phút"),
        (-5, "0 giây"),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


def test_progress_bar_text():
    assert progress_bar_text(0.0) == "░" * 10
    assert progress_bar_text(1.0) == "█" * 10
    assert progress_bar_text(0.25) == "███░░░░░░░"
    assert progress_bar_text(1.5) == "█" * 10
    assert progress_bar_text(-1) == "░" * 10


# ------------------------------------------------------------ time boundaries


def test_midnight_boundary_date_rollover(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T23:30:00+07:00")
    add(repo, "Ngày mai", "2026-08-14T07:00:00+07:00", "2026-08-14T07:45:00+07:00")
    data = payload(repo, "2026-08-13T23:30:00+07:00")
    assert data["date"]["iso"] == "2026-08-13"
    assert data["tomorrow"]["count"] == 1


def test_utc_moment_after_local_midnight(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T17:30:00+00:00")
    data = payload(repo, "2026-08-13T17:30:00+00:00")
    assert data["date"]["iso"] == "2026-08-14"
    assert data["date"]["weekday"] == "Thứ Sáu"
    assert data["now"] == {"time": "00:30"}


def test_cross_midnight_current_event(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T17:15:00+00:00")
    add(repo, "Ca trực", "2026-08-13T23:30:00+07:00", "2026-08-14T01:00:00+07:00")
    data = payload(repo, "2026-08-13T17:15:00+00:00")
    current = data["current"]
    assert current is not None
    assert current["title"] == "Ca trực"
    assert current["start_display"] == "23:30"
    assert current["end_display"] == "01:00"
    assert current["remaining_seconds"] == 2700
    assert current["remaining_display"] == "45 phút"
    assert abs(current["progress"] - 0.5) < 0.001


def test_next_event_after_midnight_is_tomorrow(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T17:15:00+00:00")
    add(repo, "Sáng mai", "2026-08-14T07:00:00+07:00", "2026-08-14T07:45:00+07:00")
    data = payload(repo, "2026-08-13T17:15:00+00:00")
    assert data["next"]["title"] == "Sáng mai"
    assert data["next"]["starts_in_display"] == "6 giờ 45 phút"


# ------------------------------------------------------------- determinism


def test_payload_deterministic_with_fixed_clock(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Đang diễn ra", "2026-08-13T19:30:00+07:00", "2026-08-13T20:15:00+07:00")
    add(repo, "Tiếp theo", "2026-08-13T21:00:00+07:00", "2026-08-13T22:00:00+07:00")
    first = payload(repo, "2026-08-13T19:45:00+07:00")
    second = payload(repo, "2026-08-13T19:45:00+07:00")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_generated_at_uses_config_timezone(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T12:45:00+00:00")
    data = payload(repo, "2026-08-13T12:45:00+00:00")
    assert data["generated_at"] == "2026-08-13T19:45:00+07:00"
    assert data["now"] == {"time": "19:45"}