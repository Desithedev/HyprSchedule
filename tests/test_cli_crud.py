"""CLI tests for the Phase 2 ``schedctl`` CRUD commands.

Every test drives ``hyprschedule.cli.main`` directly (no subprocess) and
verifies exit codes, stdout/stderr, and — where required — the database via
``EventRepository`` bound to the isolated XDG data dir. All datetimes are
timezone-aware (Asia/Ho_Chi_Minh unless noted).
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from hyprschedule.cli import main
from hyprschedule.errors import EventNotFound
from hyprschedule.models import EventType

pytestmark = pytest.mark.usefixtures("cli_env")

TZ = "Asia/Ho_Chi_Minh"
VIET = ZoneInfo(TZ)


def local(dt: datetime) -> datetime:
    return dt.astimezone(VIET)


def dt_local(day: str, clock: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}:00+07:00")


# ------------------------------------------------------------------ add


def test_add_one_time_success(cli_env, capsys):
    rc = main(
        [
            "add", "--title", "Dạy 12A1", "--description", "Lớp toán",
            "--location", "A203", "--type", "class", "--priority", "high",
            "--privacy", "private", "--date", "2026-08-17",
            "--start", "07:00", "--end", "07:45",
        ]
    )
    out, err = capsys.readouterr()
    assert rc == 0
    assert out == "Đã thêm lịch #1: Dạy 12A1\n"
    assert err == ""
    event = cli_env.repo.get_event(1)
    assert event.title == "Dạy 12A1"
    assert event.description == "Lớp toán"
    assert event.location == "A203"
    assert event.timezone == TZ
    assert event.event_type is EventType.CLASS
    assert event.start_at.tzinfo is not None
    assert event.end_at.tzinfo is not None
    assert local(event.start_at) == dt_local("2026-08-17", "07:00")
    assert local(event.end_at) == dt_local("2026-08-17", "07:45")


def test_add_cross_midnight(cli_env, capsys):
    rc = main(
        ["add", "--title", "Đêm", "--date", "2026-08-17",
         "--start", "23:00", "--end", "01:00"]
    )
    out, err = capsys.readouterr()
    assert rc == 0
    assert out == "Đã thêm lịch #1: Đêm\n"
    event = cli_env.repo.get_event(1)
    assert local(event.start_at) == dt_local("2026-08-17", "23:00")
    assert local(event.end_at) == dt_local("2026-08-18", "01:00")


def test_add_equal_start_end_rejected(capsys):
    rc = main(
        ["add", "--title", "X", "--date", "2026-08-17",
         "--start", "07:45", "--end", "07:45"]
    )
    out, err = capsys.readouterr()
    assert rc == 2
    assert "error:" in err


def test_add_conflict_rejected(cli_env, capsys):
    assert main(
        ["add", "--title", "A", "--date", "2026-08-17",
         "--start", "07:00", "--end", "07:45"]
    ) == 0
    capsys.readouterr()
    rc = main(
        ["add", "--title", "B", "--date", "2026-08-17",
         "--start", "07:30", "--end", "08:00"]
    )
    out, err = capsys.readouterr()
    assert rc == 3
    assert out == ""
    assert "Conflict detected:" in err
    assert "Use --force to save anyway." in err
    assert "07:00 - 07:45  A" in err
    with pytest.raises(EventNotFound):
        cli_env.repo.get_event(2)


def test_add_conflict_force(cli_env, capsys):
    assert main(
        ["add", "--title", "A", "--date", "2026-08-17",
         "--start", "07:00", "--end", "07:45"]
    ) == 0
    capsys.readouterr()
    rc = main(
        ["add", "--title", "B", "--date", "2026-08-17",
         "--start", "07:30", "--end", "08:00", "--force"]
    )
    out, err = capsys.readouterr()
    assert rc == 0
    assert out == "Đã thêm lịch #2: B\n"
    assert cli_env.repo.get_event(2).title == "B"


def test_add_recurring_conflict(cli_env, capsys):
    rc = main(
        ["add", "--title", "Series", "--repeat", "weekly", "--weekday", "mon",
         "--from", "2026-08-10", "--start", "07:00", "--end", "07:45"]
    )
    assert rc == 0
    rc = main(
        ["add", "--title", "One-off", "--date", "2026-08-24",
         "--start", "07:30", "--end", "08:00"]
    )
    out, err = capsys.readouterr()
    assert rc == 3
    assert "Conflict detected:" in err
    assert "Use --force to save anyway." in err
    assert "07:00 - 07:45  Series" in err
    with pytest.raises(EventNotFound):
        cli_env.repo.get_event(2)


def test_add_recurring_weekly(cli_env, capsys):
    rc = main(
        ["add", "--title", "Tiếng Anh", "--repeat", "weekly",
         "--weekday", "mon,wed,fri", "--from", "2026-08-17",
         "--until", "2026-12-31", "--start", "07:00", "--end", "07:45"]
    )
    out, err = capsys.readouterr()
    assert rc == 0
    assert out == "Đã thêm lịch #1: Tiếng Anh\n"
    event = cli_env.repo.get_event(1)
    assert event.rrule == "FREQ=WEEKLY;BYDAY=MO,WE,FR;UNTIL=20261231"
    occurrences = cli_env.repo.get_occurrences(
        dt_local("2026-08-24", "00:00"), dt_local("2026-08-29", "00:00")
    )
    assert [local(o.occurrence_start) for o in occurrences] == [
        dt_local("2026-08-24", "07:00"),
        dt_local("2026-08-26", "07:00"),
        dt_local("2026-08-28", "07:00"),
    ]


def test_add_reminders(cli_env, capsys):
    rc = main(
        ["add", "--title", "R", "--date", "2026-08-20",
         "--start", "09:00", "--end", "09:30", "--remind", "15,5,0"]
    )
    out, err = capsys.readouterr()
    assert rc == 0
    assert [r.minutes_before for r in cli_env.repo.list_reminders(1)] == [0, 5, 15]


def test_add_duplicate_reminders_rejected(capsys):
    rc = main(
        ["add", "--title", "R2", "--date", "2026-08-20",
         "--start", "10:00", "--end", "10:30", "--remind", "15,15"]
    )
    out, err = capsys.readouterr()
    assert rc == 2
    assert "error: reminder offsets must not contain duplicates" in err
    assert "Traceback" not in err


# ------------------------------------------------------------------ edit


def test_edit_title_only_changes_title(cli_env, capsys):
    main(
        [
            "add", "--title", "Dạy 12A1", "--description", "Lớp toán",
            "--location", "A203", "--type", "class", "--date", "2026-08-17",
            "--start", "07:00", "--end", "07:45",
        ]
    )
    capsys.readouterr()
    rc = main(["edit", "1", "--title", "New Title"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert out == "Đã cập nhật lịch #1.\n"
    event = cli_env.repo.get_event(1)
    assert event.title == "New Title"
    assert event.description == "Lớp toán"
    assert event.location == "A203"
    assert event.timezone == TZ
    assert event.event_type is EventType.CLASS
    assert local(event.start_at) == dt_local("2026-08-17", "07:00")
    assert local(event.end_at) == dt_local("2026-08-17", "07:45")


def test_edit_start_preserves_duration(cli_env, capsys):
    main(
        ["add", "--title", "A", "--date", "2026-08-17",
         "--start", "07:00", "--end", "07:45"]
    )
    rc = main(["edit", "1", "--start", "09:00"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert "Conflict detected:" not in err
    event = cli_env.repo.get_event(1)
    assert local(event.start_at) == dt_local("2026-08-17", "09:00")
    assert local(event.end_at) == dt_local("2026-08-17", "09:45")


def test_edit_start_and_end_cross_midnight(cli_env, capsys):
    main(
        ["add", "--title", "A", "--date", "2026-08-17",
         "--start", "07:00", "--end", "07:45"]
    )
    rc = main(["edit", "1", "--start", "23:00", "--end", "01:00"])
    out, err = capsys.readouterr()
    assert rc == 0
    event = cli_env.repo.get_event(1)
    assert local(event.start_at) == dt_local("2026-08-17", "23:00")
    assert local(event.end_at) == dt_local("2026-08-18", "01:00")


def test_edit_end_only_cross_midnight(cli_env, capsys):
    main(
        ["add", "--title", "A", "--date", "2026-08-17",
         "--start", "07:00", "--end", "07:45"]
    )
    rc = main(["edit", "1", "--end", "01:00"])
    out, err = capsys.readouterr()
    assert rc == 0
    event = cli_env.repo.get_event(1)
    assert local(event.start_at) == dt_local("2026-08-17", "07:00")
    assert local(event.end_at) == dt_local("2026-08-18", "01:00")


def test_edit_timezone_reinterprets_wall_clock(cli_env, capsys):
    main(
        ["add", "--title", "A", "--date", "2026-08-17",
         "--start", "08:00", "--end", "08:30"]
    )
    rc = main(["edit", "1", "--timezone", "Asia/Tokyo"])
    out, err = capsys.readouterr()
    assert rc == 0
    tokyo = ZoneInfo("Asia/Tokyo")
    event = cli_env.repo.get_event(1)
    assert event.timezone == "Asia/Tokyo"
    assert event.start_at.astimezone(tokyo) == datetime(2026, 8, 17, 8, 0, tzinfo=tokyo)
    assert event.end_at.astimezone(tokyo) == datetime(2026, 8, 17, 8, 30, tzinfo=tokyo)


def test_edit_self_overlap_succeeds_without_force(cli_env, capsys):
    main(
        ["add", "--title", "A", "--date", "2026-08-17",
         "--start", "07:00", "--end", "07:45"]
    )
    rc = main(["edit", "1", "--start", "10:00"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert "Conflict detected:" not in err
    event = cli_env.repo.get_event(1)
    assert local(event.start_at) == dt_local("2026-08-17", "10:00")


def test_edit_excludes_self_from_conflict(cli_env, capsys):
    assert main(
        ["add", "--title", "A", "--date", "2026-08-18",
         "--start", "07:00", "--end", "07:45"]
    ) == 0
    assert main(
        ["add", "--title", "B", "--date", "2026-08-18",
         "--start", "07:30", "--end", "08:00", "--force"]
    ) == 0
    capsys.readouterr()
    rc = main(["edit", "1", "--title", "A-new"])
    out, err = capsys.readouterr()
    assert rc == 3
    assert out == ""
    assert "Conflict detected:" in err
    assert "Use --force to save anyway." in err
    assert "07:30 - 08:00  B" in err
    assert "07:00 - 07:45  A" not in err
    assert cli_env.repo.get_event(1).title == "A"
    rc = main(["edit", "1", "--title", "A-new", "--force"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert out == "Đã cập nhật lịch #1.\n"
    assert cli_env.repo.get_event(1).title == "A-new"


def test_edit_missing_id(capsys):
    rc = main(["edit", "99", "--title", "x"])
    out, err = capsys.readouterr()
    assert rc == 4
    assert out == ""
    assert "error: event 99 does not exist" in err


# ------------------------------------------------------------------ delete


def test_delete_event_and_reminders_cascade(cli_env, capsys):
    main(
        ["add", "--title", "R", "--date", "2026-08-20",
         "--start", "09:00", "--end", "09:30", "--remind", "15,5,0"]
    )
    capsys.readouterr()
    rc = main(["delete", "1"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert out == "Đã xóa lịch #1.\n"
    with pytest.raises(EventNotFound):
        cli_env.repo.get_event(1)
    assert cli_env.repo.list_reminders(1) == []
    rc = main(["delete", "1"])
    out, err = capsys.readouterr()
    assert rc == 4
    assert "error: event 1 does not exist" in err


def test_delete_missing_id(capsys):
    rc = main(["delete", "99"])
    out, err = capsys.readouterr()
    assert rc == 4
    assert out == ""
    assert "error: event 99 does not exist" in err


# ------------------------------------------------------------------ show


def test_show_text(capsys):
    main(
        [
            "add", "--title", "Dạy 12A1", "--description", "Lớp toán",
            "--location", "A203", "--type", "class", "--priority", "high",
            "--privacy", "private", "--date", "2026-08-17",
            "--start", "07:00", "--end", "07:45", "--remind", "15,5,0",
        ]
    )
    capsys.readouterr()
    rc = main(["show", "1"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert err == ""
    assert out.splitlines() == [
        "ID:          1",
        "Title:       Dạy 12A1",
        "Type:        class",
        "Start:       17/08/2026 07:00",
        "End:         17/08/2026 07:45",
        "Timezone:    Asia/Ho_Chi_Minh",
        "Location:    A203",
        "Description: Lớp toán",
        "Priority:    high",
        "Privacy:     private",
        "Status:      active",
        "Recurrence:  Không lặp lại",
        "Reminders:   0m, 5m, 15m",
    ]


def test_show_text_recurring(capsys):
    main(
        ["add", "--title", "Tiếng Anh", "--repeat", "weekly",
         "--weekday", "mon,wed,fri", "--from", "2026-08-17",
         "--until", "2026-12-31", "--start", "07:00", "--end", "07:45"]
    )
    capsys.readouterr()
    rc = main(["show", "1"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert "Recurrence:  weekly · Thứ Hai, Thứ Tư, Thứ Sáu · đến 31/12/2026" in out
    assert "weekly ·" in out
    assert "· đến 31/12/2026" in out
    assert "Reminders:   không có" in out


def test_show_json(capsys):
    main(
        [
            "add", "--title", "Dạy 12A1", "--description", "Lớp toán",
            "--location", "A203", "--type", "class", "--priority", "high",
            "--privacy", "private", "--date", "2026-08-17",
            "--start", "07:00", "--end", "07:45", "--remind", "15,5,0",
        ]
    )
    capsys.readouterr()
    rc = main(["show", "1", "--json"])
    out, err = capsys.readouterr()
    assert rc == 0
    data = json.loads(out)
    assert data["id"] == 1
    assert data["title"] == "Dạy 12A1"
    assert data["description"] == "Lớp toán"
    assert data["location"] == "A203"
    assert data["timezone"] == TZ
    assert data["event_type"] == "class"
    assert data["priority"] == "high"
    assert data["privacy"] == "private"
    assert data["status"] == "active"
    assert data["rrule"] is None
    assert data["recurring"] is False
    assert data["reminders"] == [0, 5, 15]
    for key in ("start", "end", "created_at", "updated_at"):
        parsed = datetime.fromisoformat(data[key])
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() is not None


def test_show_json_recurring(capsys):
    main(
        ["add", "--title", "Tiếng Anh", "--repeat", "weekly",
         "--weekday", "mon,wed,fri", "--from", "2026-08-17",
         "--until", "2026-12-31", "--start", "07:00", "--end", "07:45"]
    )
    capsys.readouterr()
    rc = main(["show", "1", "--json"])
    out, err = capsys.readouterr()
    assert rc == 0
    data = json.loads(out)
    assert data["recurring"] is True
    assert data["rrule"] == "FREQ=WEEKLY;BYDAY=MO,WE,FR;UNTIL=20261231"


def test_show_missing_id(capsys):
    rc = main(["show", "99"])
    out, err = capsys.readouterr()
    assert rc == 4
    assert out == ""
    assert "error: event 99 does not exist" in err


# ------------------------------------------------------------------ search


def test_search_text(capsys):
    main(
        ["add", "--title", "Dạy 12A1", "--location", "A203",
         "--date", "2026-08-17", "--start", "07:00", "--end", "07:45"]
    )
    capsys.readouterr()
    rc = main(["search", "12A1"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert out.splitlines() == [
        '1 kết quả cho "12A1":',
        "",
        "#1  17/08/2026 07:00 - 07:45  Dạy 12A1",
    ]
    rc = main(["search", "12a1"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert '1 kết quả cho "12a1":' in out
    assert "#1  17/08/2026 07:00 - 07:45  Dạy 12A1" in out
    rc = main(["search", "A203"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert "#1  17/08/2026 07:00 - 07:45  Dạy 12A1" in out
    rc = main(["search", "zzz"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert out == 'Không có kết quả cho "zzz".\n'
    assert err == ""


def test_search_json(capsys):
    main(
        ["add", "--title", "Dạy 12A1", "--location", "A203",
         "--date", "2026-08-17", "--start", "07:00", "--end", "07:45"]
    )
    capsys.readouterr()
    rc = main(["search", "12A1", "--json"])
    out, err = capsys.readouterr()
    assert rc == 0
    data = json.loads(out)
    assert data["query"] == "12A1"
    assert len(data["events"]) == 1
    assert data["events"][0]["id"] == 1
    assert data["events"][0]["title"] == "Dạy 12A1"
    rc = main(["search", "zzz", "--json"])
    out, err = capsys.readouterr()
    assert rc == 0
    data = json.loads(out)
    assert data["query"] == "zzz"
    assert data["events"] == []


# ------------------------------------------------------------------ help


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out, err = capsys.readouterr()
    assert "usage: schedctl" in out


def test_add_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["add", "--help"])
    assert exc.value.code == 0
    out, err = capsys.readouterr()
    assert "usage: schedctl add" in out


# ------------------------------------------------------- argparse-level errors


def test_add_without_title_exits_two(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["add", "--date", "2026-08-17", "--start", "07:00", "--end", "07:45"])
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert "the following arguments are required: --title" in err


def test_show_without_id_exits_two(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["show"])
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert "the following arguments are required: ID" in err


def test_add_invalid_date_exits_two(capsys):
    with pytest.raises(SystemExit) as exc:
        main(
            ["add", "--title", "x", "--date", "2026-13-40",
             "--start", "07:00", "--end", "07:45"]
        )
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert "argument --date: invalid date" in err
    assert "Traceback" not in err


def test_add_invalid_start_time_exits_two(capsys):
    with pytest.raises(SystemExit) as exc:
        main(
            ["add", "--title", "x", "--date", "2026-08-17",
             "--start", "25:00", "--end", "07:45"]
        )
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert "argument --start: invalid time" in err
    assert "Traceback" not in err


def test_add_invalid_weekday_exits_two(capsys):
    with pytest.raises(SystemExit) as exc:
        main(
            ["add", "--title", "x", "--repeat", "weekly", "--weekday", "xyz",
             "--from", "2026-08-17", "--start", "07:00", "--end", "07:45"]
        )
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert "argument --weekday: invalid weekday" in err
    assert "Traceback" not in err


def test_add_negative_remind_exits_two(capsys):
    with pytest.raises(SystemExit) as exc:
        main(
            ["add", "--title", "x", "--date", "2026-08-17",
             "--start", "07:00", "--end", "07:45", "--remind", "-5"]
        )
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert "argument --remind: reminder minutes must be >= 0" in err
    assert "Traceback" not in err


def test_add_invalid_repeat_choice_exits_two(capsys):
    with pytest.raises(SystemExit) as exc:
        main(
            ["add", "--title", "x", "--repeat", "daily",
             "--start", "07:00", "--end", "07:45"]
        )
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert "argument --repeat: invalid choice" in err
    assert "Traceback" not in err


# -------------------------------------------------- command-level usage errors


def test_add_weekly_requires_from(capsys):
    rc = main(
        ["add", "--title", "x", "--repeat", "weekly",
         "--start", "07:00", "--end", "07:45"]
    )
    out, err = capsys.readouterr()
    assert rc == 2
    assert "error: --from is required with --repeat weekly" in err
    assert "Traceback" not in err


def test_add_empty_title_rejected(cli_env, capsys):
    rc = main(
        ["add", "--title", "", "--date", "2026-08-17",
         "--start", "07:00", "--end", "07:45"]
    )
    out, err = capsys.readouterr()
    assert rc == 2
    assert "error: title must not be empty" in err
    assert "Traceback" not in err
    with pytest.raises(EventNotFound):
        cli_env.repo.get_event(1)


def test_add_invalid_timezone_is_usage_error(capsys):
    rc = main(
        ["add", "--title", "x", "--date", "2026-08-17",
         "--start", "07:00", "--end", "07:45", "--timezone", "Not/AZone"]
    )
    out, err = capsys.readouterr()
    assert rc == 2
    assert "error:" in err
    assert "Traceback" not in err