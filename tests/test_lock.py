"""Phase 5 tests: the ``schedctl lock`` payload.

Hermetic by construction: time is pinned with the frozen clock, XDG dirs are
per-test temp dirs, and privacy/status/recurrence are seeded through the
repository. All rendering is driven by ``lock.build_lines`` or the CLI entry
point — never the real clock or user data.
"""

import json
from datetime import datetime

import pytest

from hyprschedule.config import load_config
from hyprschedule.lock import PRIVATE_MASK, build_lines
from hyprschedule.models import (
    Event,
    EventStatus,
    EventType,
    Privacy,
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


def text(repo, now: str) -> str:
    return "\n".join(build_lines(load_config(), repo, dt(now)))


def lines(repo, now: str) -> list[str]:
    return build_lines(load_config(), repo, dt(now))


# ----------------------------------------------------------------- empty state


def test_empty_schedule_tomorrow_also_empty(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    out = text(repo, "2026-08-13T19:45:00+07:00")
    assert out == "19:45\n\nKhông có lịch sắp tới."


def test_empty_today_but_tomorrow_has_events(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Sáng mai", "2026-08-14T08:00:00+07:00", "2026-08-14T08:45:00+07:00")
    out = text(repo, "2026-08-13T19:45:00+07:00")
    assert "Không còn lịch hôm nay." in out
    assert "Ngày mai · 1 lịch" in out


def test_empty_state_has_no_placeholder_tokens(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    out = text(repo, "2026-08-13T19:45:00+07:00")
    for token in ("None", "null", "[]", "{}", "Traceback"):
        assert token not in out


# ------------------------------------------------------------- current event


def test_current_public_event(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Dạy 12A1", "2026-08-13T19:30:00+07:00", "2026-08-13T20:15:00+07:00",
        location="A203", event_type=EventType.CLASS, priority=Priority.HIGH)
    out = text(repo, "2026-08-13T19:45:00+07:00")
    assert "ĐANG DIỄN RA" in out
    assert "Dạy 12A1" in out
    assert "19:30 → 20:15" in out
    assert "A203" in out
    assert "còn 30 phút" in out


def test_current_private_masked(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Họp cá nhân", "2026-08-13T19:30:00+07:00", "2026-08-13T20:15:00+07:00",
        location="Phòng riêng", privacy=Privacy.PRIVATE)
    out = text(repo, "2026-08-13T19:45:00+07:00")
    assert "ĐANG DIỄN RA" in out
    assert PRIVATE_MASK in out
    assert "Họp cá nhân" not in out
    assert "Phòng riêng" not in out
    assert "19:30 → 20:15" in out
    assert "còn 30 phút" in out


def test_current_private_shown_when_configured(cli_env, tmp_path, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(cli_env.repo, "Họp cá nhân", "2026-08-13T19:30:00+07:00", "2026-08-13T20:15:00+07:00",
        location="Phòng riêng", privacy=Privacy.PRIVATE)
    config_dir = tmp_path / "config" / "hyprschedule"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text("[lockscreen]\nshow_private = true\n")
    out = "\n".join(build_lines(load_config(), cli_env.repo, dt("2026-08-13T19:45:00+07:00")))
    assert "Họp cá nhân" in out
    assert "Phòng riêng" in out


def test_current_hidden_not_exposed(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Secret", "2026-08-13T19:30:00+07:00", "2026-08-13T20:15:00+07:00",
        privacy=Privacy.HIDDEN)
    out = text(repo, "2026-08-13T19:45:00+07:00")
    assert "ĐANG DIỄN RA" not in out
    assert "Secret" not in out


# ---------------------------------------------------------------- next event


def test_next_public_event(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Họp tổ", "2026-08-13T20:00:00+07:00", "2026-08-13T21:00:00+07:00",
        location="Phòng họp", event_type=EventType.MEETING)
    out = text(repo, "2026-08-13T19:45:00+07:00")
    assert "TIẾP THEO" in out
    assert "20:00 · Họp tổ" in out
    assert "Phòng họp" in out
    assert "sau 15 phút" in out


def test_next_private_masked(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Họp cá nhân", "2026-08-13T20:00:00+07:00", "2026-08-13T21:00:00+07:00",
        location="Phòng riêng", privacy=Privacy.PRIVATE)
    out = text(repo, "2026-08-13T19:45:00+07:00")
    assert "20:00 · Có lịch cá nhân" in out
    assert "Họp cá nhân" not in out
    assert "Phòng riêng" not in out


def test_next_hidden_skipped_visible_selected(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Secret", "2026-08-13T20:00:00+07:00", "2026-08-13T20:30:00+07:00",
        privacy=Privacy.HIDDEN)
    add(repo, "Họp tổ", "2026-08-13T21:00:00+07:00", "2026-08-13T22:00:00+07:00")
    out = text(repo, "2026-08-13T19:45:00+07:00")
    assert "21:00 · Họp tổ" in out
    assert "Secret" not in out
    assert "20:00" not in out


def test_next_prefers_today(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T08:00:00+07:00")
    add(repo, "Ngày kia", "2026-08-15T09:00:00+07:00", "2026-08-15T09:30:00+07:00")
    add(repo, "Hôm nay", "2026-08-13T10:00:00+07:00", "2026-08-13T10:30:00+07:00")
    out = text(repo, "2026-08-13T08:00:00+07:00")
    assert "10:00 · Hôm nay" in out
    assert "Ngày kia" not in out


# ------------------------------------------------------------ current + next


def test_current_and_next(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Đang diễn ra", "2026-08-13T19:30:00+07:00", "2026-08-13T20:15:00+07:00")
    add(repo, "Tiếp theo", "2026-08-13T21:00:00+07:00", "2026-08-13T22:00:00+07:00")
    out = text(repo, "2026-08-13T19:45:00+07:00")
    assert out.index("ĐANG DIỄN RA") < out.index("TIẾP THEO")
    assert "Đang diễn ra" in out
    assert "21:00 · Tiếp theo" in out


# --------------------------------------------------------------- recurrence


def test_recurring_public_event(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-17T06:30:00+07:00")
    add(repo, "Dạy 11A3", "2026-08-10T07:00:00+07:00", "2026-08-10T07:45:00+07:00",
        rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR")
    out = text(repo, "2026-08-17T06:30:00+07:00")
    assert "07:00 · Dạy 11A3" in out
    assert "sau 30 phút" in out


def test_cancelled_recurrence_occurrence_absent(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-17T06:30:00+07:00")
    event = add(repo, "Dạy 11A3", "2026-08-10T07:00:00+07:00", "2026-08-10T07:45:00+07:00",
                rrule="FREQ=WEEKLY;BYDAY=MO")
    repo.add_exception(
        RecurrenceException(
            event_id=event.id,
            occurrence_start=dt("2026-08-17T07:00:00+07:00"),
            action=RecurrenceExceptionAction.CANCEL,
        )
    )
    out = text(repo, "2026-08-17T06:30:00+07:00")
    assert "Dạy 11A3" not in out


def test_modified_recurrence_shows_modified(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-17T06:30:00+07:00")
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
    out = text(repo, "2026-08-17T06:30:00+07:00")
    assert "08:00 · Đổi lịch" in out
    assert "B204" in out
    assert "Dạy 12A1" not in out
    assert "07:00" not in out


# ------------------------------------------------------------------- status


def test_cancelled_status_absent(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    event = add(repo, "Đã hủy", "2026-08-13T20:00:00+07:00", "2026-08-13T20:30:00+07:00")
    repo.update_event(event.id, status=EventStatus.CANCELLED)
    out = text(repo, "2026-08-13T19:45:00+07:00")
    assert "Đã hủy" not in out


def test_done_status_absent(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    event = add(repo, "Đã xong", "2026-08-13T19:30:00+07:00", "2026-08-13T20:15:00+07:00")
    repo.update_event(event.id, status=EventStatus.DONE)
    out = text(repo, "2026-08-13T19:45:00+07:00")
    assert "Đã xong" not in out
    assert "ĐANG DIỄN RA" not in out


# ----------------------------------------------------------- cross-midnight


def test_cross_midnight_current_event(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T17:15:00+00:00")
    add(repo, "Ca trực", "2026-08-13T23:30:00+07:00", "2026-08-14T01:00:00+07:00")
    out = text(repo, "2026-08-13T17:15:00+00:00")
    assert "ĐANG DIỄN RA" in out
    assert "Ca trực" in out
    assert "23:30 → 01:00" in out
    assert "còn 45 phút" in out


def test_timezone_boundary_around_midnight(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T17:30:00+00:00")
    add(repo, "Sáng nay", "2026-08-14T08:00:00+07:00", "2026-08-14T08:45:00+07:00")
    out = text(repo, "2026-08-13T17:30:00+00:00")
    assert out.splitlines()[0] == "00:30"


# ------------------------------------------------------------ tomorrow count


def test_tomorrow_count_footer(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T20:00:00+07:00")
    add(repo, "Dạy", "2026-08-10T07:00:00+07:00", "2026-08-10T07:45:00+07:00",
        rrule="FREQ=WEEKLY;BYDAY=FR")
    add(repo, "Một lần", "2026-08-14T09:00:00+07:00", "2026-08-14T09:45:00+07:00")
    add(repo, "Lần nữa", "2026-08-14T10:00:00+07:00", "2026-08-14T10:45:00+07:00")
    out = text(repo, "2026-08-13T20:00:00+07:00")
    assert "Ngày mai · 3 lịch" in out


def test_tomorrow_count_excludes_hidden(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T20:00:00+07:00")
    add(repo, "Công khai", "2026-08-14T09:00:00+07:00", "2026-08-14T09:45:00+07:00")
    add(repo, "Bí mật", "2026-08-14T10:00:00+07:00", "2026-08-14T10:45:00+07:00",
        privacy=Privacy.HIDDEN)
    out = text(repo, "2026-08-13T20:00:00+07:00")
    assert "Ngày mai · 1 lịch" in out


def test_tomorrow_count_footer_hidden_when_zero(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T20:00:00+07:00")
    out = text(repo, "2026-08-13T20:00:00+07:00")
    assert "Ngày mai" not in out


def test_tomorrow_count_cancelled_recurrence_not_counted(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-16T20:00:00+07:00")
    event = add(repo, "Dạy", "2026-08-10T07:00:00+07:00", "2026-08-10T07:45:00+07:00",
                rrule="FREQ=WEEKLY;BYDAY=MO")
    add(repo, "Họp", "2026-08-17T09:00:00+07:00", "2026-08-17T09:30:00+07:00")
    repo.add_exception(
        RecurrenceException(
            event_id=event.id,
            occurrence_start=dt("2026-08-17T07:00:00+07:00"),
            action=RecurrenceExceptionAction.CANCEL,
        )
    )
    assert "Ngày mai · 1 lịch" in text(repo, "2026-08-16T20:00:00+07:00")


# ------------------------------------------------------------- safety / CLI


def test_no_traceback_on_config_error(cli_env, tmp_path, frozen_clock, run):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    config_dir = tmp_path / "config" / "hyprschedule"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text('timezone = "Not/AZone"\n')
    code, captured = run(["lock"])
    assert code != 0
    assert "Không thể tải lịch" in captured.out
    assert "Traceback" not in captured.out + captured.err


def test_lock_stdout_purity_and_read_only(cli_env, frozen_clock, run):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(cli_env.repo, "Dạy 12A1", "2026-08-13T19:30:00+07:00", "2026-08-13T20:15:00+07:00")
    code, captured = run(["lock"])
    assert code == 0
    assert captured.err == ""
    first = captured.out.splitlines()[0]
    assert len(first) == 5 and first[2] == ":"
    assert "{" not in captured.out and "}" not in captured.out
    assert cli_env.repo.list_events() and captured.out.strip()


def test_lock_help_present(capsys):
    from hyprschedule.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["lock", "--help"])
    assert excinfo.value.code == 0
    assert "usage: schedctl" in capsys.readouterr().out


def test_markup_like_title_rendered_literal(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, '<span size="999999">test</span>', "2026-08-13T20:00:00+07:00",
        "2026-08-13T20:30:00+07:00")
    out = text(repo, "2026-08-13T19:45:00+07:00")
    assert '20:00 · <span size="999999">test</span>' in out


def test_vietnamese_unicode_title(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Kiểm tra 45 phút — Toán 12", "2026-08-13T20:00:00+07:00",
        "2026-08-13T20:45:00+07:00")
    out = text(repo, "2026-08-13T19:45:00+07:00")
    assert "20:00 · Kiểm tra 45 phút — Toán 12" in out


def test_deterministic_with_fixed_clock(repo, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(repo, "Đang diễn ra", "2026-08-13T19:30:00+07:00", "2026-08-13T20:15:00+07:00")
    add(repo, "Tiếp theo", "2026-08-13T21:00:00+07:00", "2026-08-13T22:00:00+07:00")
    first = text(repo, "2026-08-13T19:45:00+07:00")
    second = text(repo, "2026-08-13T19:45:00+07:00")
    assert first == second


def test_max_events_limits_blocks(cli_env, tmp_path, frozen_clock):
    freeze(frozen_clock, "2026-08-13T19:45:00+07:00")
    add(cli_env.repo, "Đang", "2026-08-13T19:30:00+07:00", "2026-08-13T20:15:00+07:00")
    add(cli_env.repo, "Sắp tới", "2026-08-13T21:00:00+07:00", "2026-08-13T22:00:00+07:00")
    config_dir = tmp_path / "config" / "hyprschedule"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text("[lockscreen]\nmax_events = 1\n")
    out = "\n".join(build_lines(load_config(), cli_env.repo, dt("2026-08-13T19:45:00+07:00")))
    assert "ĐANG DIỄN RA" in out
    assert "TIẾP THEO" not in out