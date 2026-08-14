"""CLI tests for the daily-query commands: today, tomorrow, week, next.

All commands are exercised through ``hyprschedule.cli.main`` with the
module-level clock frozen per test (restored in the fixture teardown) and
XDG dirs pointed at tmp dirs via the ``cli_env`` fixture. Seeds go through
``EventRepository`` directly and are always timezone-aware (+07:00).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from hyprschedule.cli import main
from hyprschedule.models import Event

TZ = "Asia/Ho_Chi_Minh"


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def make_event(start: str, end: str, title: str, location: str = "", rrule: str | None = None, **kw) -> Event:
    return Event(
        title=title,
        start_at=dt(start),
        end_at=dt(end),
        timezone=TZ,
        location=location,
        rrule=rrule,
        **kw,
    )


OCCURRENCE_FIELDS = {
    "event_id",
    "title",
    "description",
    "location",
    "start",
    "end",
    "timezone",
    "event_type",
    "priority",
    "privacy",
    "status",
    "recurring",
}


# ---------------------------------------------------------------- help / usage


def test_help_outputs_usage(capsys):
    for args in (["--help"], ["today", "--help"], ["week", "--help"]):
        with pytest.raises(SystemExit) as exc:
            main(args)
        assert exc.value.code == 0
        assert "usage: schedctl" in capsys.readouterr().out


def test_usage_error_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["today", "--bogus"])
    assert exc.value.code == 2


@pytest.mark.parametrize("cmd", ["today", "tomorrow", "week", "next"])
def test_json_flag_present_on_query_commands(cmd, capsys):
    with pytest.raises(SystemExit) as exc:
        main([cmd, "--help"])
    assert exc.value.code == 0
    assert "--json" in capsys.readouterr().out


@pytest.mark.parametrize("cmd", ["add", "edit", "delete"])
def test_json_flag_absent_on_mutating_commands(cmd, capsys):
    with pytest.raises(SystemExit) as exc:
        main([cmd, "--help"])
    assert exc.value.code == 0
    assert "--json" not in capsys.readouterr().out


# ----------------------------------------------------------------- today


def test_today_text_with_events_only_shows_today(repo, frozen_clock, run):
    frozen_clock(dt("2026-08-13T00:30:00+07:00"))
    repo.create_event(make_event(
        "2026-08-13T07:00:00+07:00", "2026-08-13T07:45:00+07:00",
        "Họp dự án", location="Phòng A",
    ))
    repo.create_event(make_event(
        "2026-08-14T09:00:00+07:00", "2026-08-14T09:30:00+07:00", "Lịch ngày mai",
    ))
    repo.create_event(make_event(
        "2026-08-12T10:00:00+07:00", "2026-08-12T10:30:00+07:00", "Lịch hôm qua",
    ))
    code, out = run(["today"])
    assert code == 0
    assert out.out == (
        "Thứ Năm, 13/08/2026\n"
        "\n"
        "07:00 - 07:45  Họp dự án\n"
        "               Phòng A\n"
    )
    assert "Lịch ngày mai" not in out.out
    assert "Lịch hôm qua" not in out.out


def test_today_empty_text_and_json(frozen_clock, run):
    frozen_clock(dt("2026-08-13T10:00:00+07:00"))
    _, out = run(["today"])
    assert out.out == "Thứ Năm, 13/08/2026\n\nKhông có lịch hôm nay.\n"

    _, out = run(["today", "--json"])
    data = json.loads(out.out)
    assert data == {
        "range": {"start": "2026-08-13T00:00:00+07:00", "end": "2026-08-14T00:00:00+07:00"},
        "events": [],
    }


def test_today_json_occurrence_contract_and_offsets(repo, frozen_clock, run):
    frozen_clock(dt("2026-08-13T00:30:00+07:00"))
    repo.create_event(make_event(
        "2026-08-13T07:00:00+07:00", "2026-08-13T07:45:00+07:00",
        "Họp dự án", location="Phòng A", description="Thảo luận tiến độ",
    ))
    _, out = run(["today", "--json"])
    data = json.loads(out.out)
    assert len(data["events"]) == 1
    ev = data["events"][0]
    assert set(ev.keys()) == OCCURRENCE_FIELDS
    assert ev["event_id"] == 1
    assert ev["title"] == "Họp dự án"
    assert ev["description"] == "Thảo luận tiến độ"
    assert ev["location"] == "Phòng A"
    assert ev["start"] == "2026-08-13T07:00:00+07:00"
    assert ev["end"] == "2026-08-13T07:45:00+07:00"
    assert ev["timezone"] == TZ
    assert ev["event_type"] == "other"
    assert ev["priority"] == "normal"
    assert ev["privacy"] == "public"
    assert ev["status"] == "active"
    assert ev["recurring"] is False
    assert data["range"] == {
        "start": "2026-08-13T00:00:00+07:00",
        "end": "2026-08-14T00:00:00+07:00",
    }


# --------------------------------------------------------------- tomorrow


def test_tomorrow_empty(frozen_clock, run):
    frozen_clock(dt("2026-08-13T10:00:00+07:00"))
    _, out = run(["tomorrow"])
    assert out.out == "Thứ Sáu, 14/08/2026\n\nKhông có lịch ngày mai.\n"


def test_tomorrow_across_month_boundary(frozen_clock, run):
    frozen_clock(dt("2026-08-31T10:00:00+07:00"))
    _, out = run(["tomorrow"])
    assert out.out == "Thứ Ba, 01/09/2026\n\nKhông có lịch ngày mai.\n"


def test_tomorrow_across_year_boundary(frozen_clock, run):
    frozen_clock(dt("2026-12-31T10:00:00+07:00"))
    _, out = run(["tomorrow"])
    assert out.out == "Thứ Sáu, 01/01/2027\n\nKhông có lịch ngày mai.\n"


# ------------------------------------------------------------------- week


def test_week_text_recurring_mo_we_fr(repo, frozen_clock, run):
    frozen_clock(dt("2026-08-12T10:00:00+07:00"))
    assert date(2026, 8, 12).weekday() == 2
    repo.create_event(make_event(
        "2026-08-10T07:00:00+07:00", "2026-08-10T07:45:00+07:00",
        "Lịch học", rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR",
    ))
    code, out = run(["week"])
    assert code == 0
    assert out.out == (
        "Thứ Hai 10/08\n"
        "\n"
        "07:00 - 07:45  Lịch học\n"
        "\n"
        "Thứ Tư 12/08\n"
        "\n"
        "07:00 - 07:45  Lịch học\n"
        "\n"
        "Thứ Sáu 14/08\n"
        "\n"
        "07:00 - 07:45  Lịch học\n"
    )


def test_week_series_starts_after_current_week(repo, frozen_clock, run):
    frozen_clock(dt("2026-08-12T10:00:00+07:00"))
    repo.create_event(make_event(
        "2026-08-17T07:00:00+07:00", "2026-08-17T07:45:00+07:00",
        "Lịch sau", rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR",
    ))
    _, out = run(["week"])
    assert out.out == "Không có lịch trong tuần này.\n"


def test_week_json_range_and_occurrences(repo, frozen_clock, run):
    frozen_clock(dt("2026-08-12T10:00:00+07:00"))
    repo.create_event(make_event(
        "2026-08-10T07:00:00+07:00", "2026-08-10T07:45:00+07:00",
        "Lịch học", rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR",
    ))
    repo.create_event(make_event(
        "2026-08-15T08:00:00+07:00", "2026-08-15T09:00:00+07:00", "Cuối tuần",
    ))
    _, out = run(["week", "--json"])
    data = json.loads(out.out)
    assert data["range"] == {
        "start": "2026-08-10T00:00:00+07:00",
        "end": "2026-08-17T00:00:00+07:00",
    }
    starts = [e["start"] for e in data["events"]]
    assert starts == [
        "2026-08-10T07:00:00+07:00",
        "2026-08-12T07:00:00+07:00",
        "2026-08-14T07:00:00+07:00",
        "2026-08-15T08:00:00+07:00",
    ]
    for e in data["events"]:
        assert set(e.keys()) == OCCURRENCE_FIELDS
        assert "2026-08-10T00:00:00+07:00" <= e["start"] < "2026-08-17T00:00:00+07:00"


# ------------------------------------------------------------------- next


def test_next_returns_next_occurrence(repo, frozen_clock, run):
    frozen_clock(dt("2026-08-17T07:30:00+07:00"))
    repo.create_event(make_event(
        "2026-08-17T07:00:00+07:00", "2026-08-17T07:45:00+07:00", "Lịch đã qua",
    ))
    repo.create_event(make_event(
        "2026-08-17T09:00:00+07:00", "2026-08-17T09:30:00+07:00",
        "Cuộc họp", location="Phòng B",
    ))
    code, out = run(["next"])
    assert code == 0
    assert out.out == (
        "17/08/2026 09:00 - 09:30  Cuộc họp\n"
        "                        Phòng B\n"
    )


def test_next_event_starting_exactly_at_now_is_returned(repo, frozen_clock, run):
    frozen_clock(dt("2026-08-17T07:30:00+07:00"))
    repo.create_event(make_event(
        "2026-08-17T07:30:00+07:00", "2026-08-17T08:00:00+07:00", "Đúng giờ",
    ))
    _, out = run(["next"])
    assert "Đúng giờ" in out.out


def test_next_none_text_and_json_null(frozen_clock, run):
    frozen_clock(dt("2026-08-17T07:30:00+07:00"))
    _, out = run(["next"])
    assert out.out == "Không có lịch sắp tới.\n"

    _, out = run(["next", "--json"])
    assert json.loads(out.out) == {"event": None}


def test_next_horizon_excludes_event_beyond_30_days(repo, frozen_clock, run):
    frozen_clock(dt("2026-08-17T07:30:00+07:00"))
    repo.create_event(make_event(
        "2026-09-17T07:30:00+07:00", "2026-09-17T08:00:00+07:00", "Quá xa",
    ))
    _, out = run(["next", "--json"])
    assert json.loads(out.out) == {"event": None}


def test_next_is_deterministic(repo, frozen_clock, run):
    frozen_clock(dt("2026-08-17T07:30:00+07:00"))
    repo.create_event(make_event(
        "2026-08-18T07:00:00+07:00", "2026-08-18T07:45:00+07:00", "A",
    ))
    repo.create_event(make_event(
        "2026-08-19T07:00:00+07:00", "2026-08-19T07:45:00+07:00", "B",
    ))
    _, first = run(["next"])
    _, second = run(["next"])
    assert first.out == second.out


# --------------------------------------------------- recurring in today / misc


def test_today_shows_recurring_occurrence(repo, frozen_clock, run):
    frozen_clock(dt("2026-08-10T08:00:00+07:00"))
    assert date(2026, 8, 10).weekday() == 0
    repo.create_event(make_event(
        "2026-08-10T07:00:00+07:00", "2026-08-10T07:45:00+07:00",
        "Họp tuần", rrule="FREQ=WEEKLY;BYDAY=MO",
    ))
    code, out = run(["today"])
    assert code == 0
    assert out.out == "Thứ Hai, 10/08/2026\n\n07:00 - 07:45  Họp tuần\n"


def test_timezone_boundary_utc_after_midnight(frozen_clock, run):
    frozen_clock(datetime(2026, 8, 13, 17, 30, tzinfo=timezone.utc))
    _, out = run(["today"])
    assert out.out.startswith("Thứ Sáu, 14/08/2026")
    _, out = run(["tomorrow"])
    assert out.out.startswith("Thứ Bảy, 15/08/2026")


def test_json_purity_for_all_query_commands(repo, frozen_clock, run):
    frozen_clock(dt("2026-08-13T00:30:00+07:00"))
    repo.create_event(make_event(
        "2026-08-13T07:00:00+07:00", "2026-08-13T07:45:00+07:00", "Họp dự án",
    ))
    for cmd in (["today"], ["tomorrow"], ["week"], ["next"]):
        _, out = run([*cmd, "--json"])
        data = json.loads(out.out)
        assert out.out == json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
