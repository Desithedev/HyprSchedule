"""Phase 6 tests: the Add/Edit UI backend contract.

Hermetic by construction: time is pinned with the frozen clock, XDG dirs are
per-test temp dirs, and everything goes through the CLI entry point
(``editor-data`` read-only JSON; ``editor-save`` mutating JSON over stdin).

Contract guarantees exercised here:

- ``editor-data`` never mutates and always prints one JSON document.
- ``editor-save`` reuses the exact ``add``/``edit`` backend paths (validation,
  conflict exit 3, ``force``, daemon reload signal) — nothing is bypassed.
- User values travel as data; hostile strings are stored verbatim and never
  reach a shell.
"""

import io
import json
import sys
from datetime import datetime

import pytest

from hyprschedule.models import (
    Event,
    EventStatus,
    EventType,
    Privacy,
    Priority,
)
from hyprschedule.repository import EventRepository

TZ = "Asia/Ho_Chi_Minh"


def dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def freeze(frozen_clock, iso: str) -> None:
    frozen_clock(dt(iso))


def add(repo, title: str, start: str, end: str, **kwargs) -> Event:
    defaults = dict(
        timezone=TZ,
        event_type=EventType.OTHER,
        priority=Priority.NORMAL,
        privacy=Privacy.PUBLIC,
    )
    defaults.update(kwargs)
    return repo.create_event(
        Event(title=title, start_at=dt(start), end_at=dt(end), **defaults)
    )


def form(doc: dict) -> dict:
    """A fully-populated form document with the editor defaults."""
    base = {
        "mode": "add",
        "id": None,
        "title": "Học Toán",
        "date": "2026-08-14",
        "start": "08:00",
        "end": "08:45",
        "location": "",
        "description": "",
        "event_type": "class",
        "priority": "normal",
        "privacy": "public",
        "reminders": [15, 5, 0],
        "recurrence": {"type": "none", "weekdays": [], "until": None},
        "force": False,
    }
    base.update(doc)
    return base


@pytest.fixture
def save_stdin(monkeypatch):
    """Feed one JSON document to ``editor-save`` via sys.stdin."""

    def _save(doc: dict) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(doc, ensure_ascii=False)))

    return _save


def editor_data(run) -> dict:
    code, captured = run(["editor-data"])
    assert code == 0
    return json.loads(captured.out)


# ---------------------------------------------------------------- read side


def test_editor_data_defaults_fresh_form(run, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    payload = editor_data(run)
    assert payload == {
        "id": None,
        "mode": "add",
        "title": "",
        "date": "2026-08-13",
        "start": "20:00",
        "end": "20:45",
        "location": "",
        "description": "",
        "event_type": "class",
        "priority": "normal",
        "privacy": "public",
        "reminders": [15, 5, 0],
        "recurrence": {"type": "none", "weekdays": [], "until": None},
    }


def test_editor_data_defaults_roll_over_after_midnight(run, frozen_clock):
    freeze(frozen_clock, "2026-08-13T23:40:00+07:00")
    payload = editor_data(run)
    assert payload["date"] == "2026-08-14"
    assert payload["start"] == "07:00"
    assert payload["end"] == "07:45"


def test_editor_data_defaults_keeps_evening_slot(run, frozen_clock):
    freeze(frozen_clock, "2026-08-13T22:40:00+07:00")
    payload = editor_data(run)
    assert payload["date"] == "2026-08-13"
    assert payload["start"] == "23:00"
    assert payload["end"] == "23:45"


def test_editor_data_preloads_one_time_event(run, repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Dạy 12A1", "2026-08-14T08:00:00+07:00", "2026-08-14T09:00:00+07:00",
        location="A203", description="Ôn thi HK1", event_type=EventType.CLASS,
        priority=Priority.HIGH, privacy=Privacy.PRIVATE)
    repo.replace_reminders(1, [60, 15, 0])
    code, captured = run(["editor-data", "1"])
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["id"] == 1
    assert payload["mode"] == "edit"
    assert payload["title"] == "Dạy 12A1"
    assert payload["date"] == "2026-08-14"
    assert payload["start"] == "08:00"
    assert payload["end"] == "09:00"
    assert payload["location"] == "A203"
    assert payload["description"] == "Ôn thi HK1"
    assert payload["event_type"] == "class"
    assert payload["priority"] == "high"
    assert payload["privacy"] == "private"
    assert payload["reminders"] == [0, 15, 60]
    assert payload["recurrence"] == {"type": "none", "weekdays": [], "until": None}


def test_editor_data_preloads_recurring_event(run, repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Dạy Toán", "2026-08-17T07:00:00+07:00", "2026-08-17T07:45:00+07:00",
        rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR;UNTIL=20260901")
    code, captured = run(["editor-data", "1"])
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["recurrence"] == {
        "type": "weekly",
        "weekdays": ["mon", "wed", "fri"],
        "until": "2026-09-01",
    }


def test_editor_data_recurring_without_until(run, repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Thể dục", "2026-08-17T07:00:00+07:00", "2026-08-17T08:00:00+07:00",
        rrule="FREQ=WEEKLY;BYDAY=TU")
    code, captured = run(["editor-data", "1"])
    assert json.loads(captured.out)["recurrence"]["until"] is None


def test_editor_data_unicode_round_trip(run, repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Kiểm tra 45' — Toán (cả lớp)", "2026-08-14T08:00:00+07:00",
        "2026-08-14T08:45:00+07:00")
    code, captured = run(["editor-data", "1"])
    assert json.loads(captured.out)["title"] == "Kiểm tra 45' — Toán (cả lớp)"


def test_editor_data_missing_event_exits_4(run, repo):
    code, captured = run(["editor-data", "99"])
    assert code == 4
    assert "error:" in captured.err


def test_editor_data_is_read_only(run, repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Sự kiện", "2026-08-14T08:00:00+07:00", "2026-08-14T09:00:00+07:00")
    before = len(repo.list_events())
    editor_data(run)
    run(["editor-data", "1"])
    assert len(repo.list_events()) == before
    event = repo.get_event(1)
    assert event.title == "Sự kiện"


def test_editor_data_stdout_is_pure_json(run, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    code, captured = run(["editor-data"])
    json.loads(captured.out)
    assert code == 0
    assert captured.err == ""


# ------------------------------------------------------------- add mutations


def test_save_add_one_time_event(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    save_stdin(form({}))
    code, captured = run(["editor-save"])
    assert code == 0
    assert captured.out == "Đã thêm lịch #1: Học Toán\n"
    event = repo.get_event(1)
    assert event.title == "Học Toán"
    assert event.event_type is EventType.CLASS
    assert event.privacy is Privacy.PUBLIC
    assert event.priority is Priority.NORMAL
    assert event.rrule is None
    assert event.start_at == dt("2026-08-14T08:00:00+07:00")
    assert event.end_at == dt("2026-08-14T08:45:00+07:00")
    assert [r.minutes_before for r in repo.list_reminders(1)] == [0, 5, 15]


def test_save_add_without_reminders_has_none(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    save_stdin(form({"reminders": []}))
    assert run(["editor-save"])[0] == 0
    assert repo.list_reminders(1) == []


def test_save_add_recurring_weekly(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    save_stdin(form({
        "title": "Dạy Toán",
        "date": "2026-08-17",
        "start": "07:00",
        "end": "07:45",
        "recurrence": {
            "type": "weekly",
            "weekdays": ["mon", "wed", "fri"],
            "until": "2026-09-01",
        },
    }))
    assert run(["editor-save"])[0] == 0
    event = repo.get_event(1)
    assert event.rrule == "FREQ=WEEKLY;BYDAY=MO,WE,FR;UNTIL=20260901"
    occurrences = repo.get_occurrences(
        dt("2026-08-14T00:00:00+07:00"), dt("2026-08-22T00:00:00+07:00")
    )
    days = sorted(occ.occurrence_start.date().day for occ in occurrences)
    assert days == [17, 19, 21]


def test_save_add_recurring_without_until(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    save_stdin(form({
        "recurrence": {"type": "weekly", "weekdays": ["tue"], "until": None},
    }))
    assert run(["editor-save"])[0] == 0
    assert repo.get_event(1).rrule == "FREQ=WEEKLY;BYDAY=TU"


def test_save_add_cross_midnight_end(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    save_stdin(form({"title": "Làm đêm", "start": "23:00", "end": "01:00"}))
    assert run(["editor-save"])[0] == 0
    event = repo.get_event(1)
    assert event.end_at == dt("2026-08-15T01:00:00+07:00")


def test_save_add_all_field_values(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    save_stdin(form({
        "title": "Họp phụ huynh",
        "location": "Phòng họp",
        "description": "Họp đầu năm",
        "event_type": "meeting",
        "priority": "critical",
        "privacy": "hidden",
    }))
    assert run(["editor-save"])[0] == 0
    event = repo.get_event(1)
    assert event.location == "Phòng họp"
    assert event.description == "Họp đầu năm"
    assert event.event_type is EventType.MEETING
    assert event.priority is Priority.CRITICAL
    assert event.privacy is Privacy.HIDDEN


# ------------------------------------------------------------ edit mutations


def test_save_edit_updates_fields(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Cũ", "2026-08-14T08:00:00+07:00", "2026-08-14T09:00:00+07:00")
    save_stdin(form({
        "mode": "edit",
        "id": 1,
        "title": "Mới",
        "location": "A101",
        "start": "09:30",
        "end": "10:15",
    }))
    code, captured = run(["editor-save"])
    assert code == 0
    assert captured.out == "Đã cập nhật lịch #1.\n"
    event = repo.get_event(1)
    assert event.title == "Mới"
    assert event.location == "A101"
    assert event.start_at == dt("2026-08-14T09:30:00+07:00")
    assert event.end_at == dt("2026-08-14T10:15:00+07:00")


def test_save_edit_replaces_reminders(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Có nhắc", "2026-08-14T08:00:00+07:00", "2026-08-14T09:00:00+07:00")
    repo.replace_reminders(1, [60, 30])
    save_stdin(form({
        "mode": "edit", "id": 1, "reminders": [15, 5],
    }))
    assert run(["editor-save"])[0] == 0
    assert [r.minutes_before for r in repo.list_reminders(1)] == [5, 15]


def test_save_edit_clears_reminders(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Có nhắc", "2026-08-14T08:00:00+07:00", "2026-08-14T09:00:00+07:00")
    repo.replace_reminders(1, [60])
    save_stdin(form({"mode": "edit", "id": 1, "reminders": []}))
    assert run(["editor-save"])[0] == 0
    assert repo.list_reminders(1) == []


def test_save_edit_converts_recurring_to_one_time(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Lặp", "2026-08-17T07:00:00+07:00", "2026-08-17T07:45:00+07:00",
        rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR")
    save_stdin(form({
        "mode": "edit", "id": 1,
        "date": "2026-08-14",
        "recurrence": {"type": "none", "weekdays": [], "until": None},
    }))
    assert run(["editor-save"])[0] == 0
    event = repo.get_event(1)
    assert event.rrule is None
    assert event.start_at == dt("2026-08-14T08:00:00+07:00")


def test_save_edit_converts_one_time_to_weekly(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Một lần", "2026-08-14T08:00:00+07:00", "2026-08-14T08:45:00+07:00")
    save_stdin(form({
        "mode": "edit", "id": 1,
        "recurrence": {"type": "weekly", "weekdays": ["sat"], "until": None},
    }))
    assert run(["editor-save"])[0] == 0
    event = repo.get_event(1)
    assert event.rrule == "FREQ=WEEKLY;BYDAY=SA"
    assert event.start_at == dt("2026-08-14T08:00:00+07:00")


def test_save_edit_missing_event_exits_4(run, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    save_stdin(form({"mode": "edit", "id": 99}))
    code, captured = run(["editor-save"])
    assert code == 4
    assert "error:" in captured.err


# --------------------------------------------------------------- conflicts


def test_save_conflict_exits_3_and_does_not_save(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Đang có", "2026-08-14T08:00:00+07:00", "2026-08-14T09:00:00+07:00")
    save_stdin(form({"title": "Trùng giờ"}))
    code, captured = run(["editor-save"])
    assert code == 3
    assert "Conflict detected" in captured.err
    assert len(repo.list_events()) == 1


def test_save_force_overrides_conflict(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Đang có", "2026-08-14T08:00:00+07:00", "2026-08-14T09:00:00+07:00")
    save_stdin(form({"title": "Trùng giờ", "force": True}))
    code, captured = run(["editor-save"])
    assert code == 0
    assert len(repo.list_events()) == 2


def test_save_conflict_without_force_never_auto_saves(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Đang có", "2026-08-14T08:00:00+07:00", "2026-08-14T09:00:00+07:00")
    save_stdin(form({"title": "Trùng giờ"}))
    assert run(["editor-save"])[0] == 3
    save_stdin(form({"title": "Trùng giờ"}))
    assert run(["editor-save"])[0] == 3
    assert len(repo.list_events()) == 1


# --------------------------------------------------------------- invalid input


@pytest.mark.parametrize(
    "mutation",
    [
        {"title": "  "},
        {"start": "25:00"},
        {"date": "2026-13-40"},
        {"event_type": "rock"},
        {"priority": "urgent"},
        {"privacy": "secret"},
        {"reminders": ["soon"]},
        {"reminders": [-1]},
        {"recurrence": {"type": "daily"}},
        {"recurrence": {"type": "weekly", "weekdays": []}},
        {"recurrence": {"type": "weekly", "weekdays": ["someday"]}},
    ],
)
def test_save_invalid_form_exits_2(run, repo, save_stdin, frozen_clock, mutation):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    save_stdin(form(mutation))
    code, captured = run(["editor-save"])
    assert code == 2
    assert "error:" in captured.err
    assert len(repo.list_events()) == 0


def test_save_invalid_json_exits_2(run, repo, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("không phải json"))
    code, captured = run(["editor-save"])
    assert code == 2
    assert "error:" in captured.err
    assert len(repo.list_events()) == 0


def test_save_empty_stdin_exits_2(run, repo, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    code, captured = run(["editor-save"])
    assert code == 2
    assert "error:" in captured.err


def test_save_wrong_mode_exits_2(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    save_stdin(form({"mode": "explode"}))
    code, captured = run(["editor-save"])
    assert code == 2
    assert '"mode"' in captured.err


def test_save_list_document_exits_2(run, repo, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("[1, 2, 3]"))
    code, captured = run(["editor-save"])
    assert code == 2
    assert "error:" in captured.err


def test_save_duplicate_reminders_deduped(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    save_stdin(form({"reminders": [15, 15, 5]}))
    assert run(["editor-save"])[0] == 0
    assert [r.minutes_before for r in repo.list_reminders(1)] == [5, 15]


# ------------------------------------------------------------------- delete


def test_delete_via_cli_removes_event(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    save_stdin(form({}))
    assert run(["editor-save"])[0] == 0
    code, captured = run(["delete", "1"])
    assert code == 0
    assert captured.out == "Đã xóa lịch #1.\n"
    with pytest.raises(Exception):
        repo.get_event(1)


def test_delete_recurring_removes_entire_series(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    save_stdin(form({
        "recurrence": {"type": "weekly", "weekdays": ["mon", "wed", "fri"], "until": None},
    }))
    assert run(["editor-save"])[0] == 0
    assert run(["delete", "1"])[0] == 0
    assert repo.list_events() == []


# ------------------------------------------------------------ daemon reload


def test_editor_save_and_delete_signal_daemon(run, repo, save_stdin, frozen_clock, monkeypatch):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    import hyprschedule.commands as commands

    calls: list[str] = []
    monkeypatch.setattr(commands, "signal_running_daemon", lambda: calls.append("signal"))
    save_stdin(form({}))
    assert run(["editor-save"])[0] == 0
    assert calls == ["signal"]
    save_stdin(form({"mode": "edit", "id": 1, "title": "Đổi tên"}))
    assert run(["editor-save"])[0] == 0
    assert calls == ["signal", "signal"]
    assert run(["delete", "1"])[0] == 0
    assert calls == ["signal", "signal", "signal"]


# ---------------------------------------------------------------- security


@pytest.mark.parametrize(
    "hostile",
    [
        "$(rm -rf /tmp/hyprschedule-pwned)",
        "`rm -rf /tmp/hyprschedule-pwned`",
        'echo "hacked"; rm -rf /tmp/hyprschedule-pwned &',
        "tiêu đề 'với' \"nháy kép\"",
        "sudo reboot now",
        "| sh",
        "<span foreground='red'>markup</span>",
        "Lịch & lịch || true",
    ],
)
def test_hostile_titles_are_stored_as_data(run, repo, save_stdin, frozen_clock,
                                           tmp_path, hostile):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    marker = tmp_path / "hyprschedule-pwned"
    save_stdin(form({"title": hostile}))
    code, captured = run(["editor-save"])
    assert code == 0
    assert not marker.exists(), f"title was executed: {hostile!r}"
    event = repo.get_event(1)
    assert event.title == hostile
    code, captured = run(["editor-data", "1"])
    assert json.loads(captured.out)["title"] == hostile


def test_hostile_location_and_description_round_trip(run, repo, save_stdin,
                                                     frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    save_stdin(form({"location": "$(touch /tmp/pwned-loc)", "description": "`touch /tmp/pwned-desc`"}))
    assert run(["editor-save"])[0] == 0
    event = repo.get_event(1)
    assert event.location == "$(touch /tmp/pwned-loc)"
    assert event.description == "`touch /tmp/pwned-desc`"


def test_save_never_touches_shell_machinery(run, repo, save_stdin, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    save_stdin(form({}))
    assert run(["editor-save"])[0] == 0
    assert len(repo.list_events()) == 1
    save_stdin(form({"mode": "edit", "id": 1}))
    assert run(["editor-save"])[0] == 0


# ------------------------------------------------------------ CLI registration


def test_editor_subcommands_in_help(run):
    code, captured = run([])
    assert code == 2
    assert "editor-data" in captured.err
    assert "editor-save" in captured.err


def test_editor_data_rejects_non_numeric_id(run):
    with pytest.raises(SystemExit) as exc_info:
        run(["editor-data", "abc"])
    assert exc_info.value.code == 2