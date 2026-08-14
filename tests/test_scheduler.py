"""Phase 3 scheduler tests.

Hermetic by construction: notifications go to a recording notifier, time is
pinned with the frozen clock, XDG dirs are per-test temp dirs, and the loop
tests use real signals against this process with a real (short) clock.
No desktop session, systemd, or user database is ever touched.
"""

import asyncio
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from hyprschedule import paths
from hyprschedule.clock import clock
from hyprschedule.config import load_config
from hyprschedule.database import Database
from hyprschedule.models import (
    Event,
    EventStatus,
    Priority,
    RecurrenceException,
    RecurrenceExceptionAction,
)
from hyprschedule.repository import EventRepository
from hyprschedule.scheduler import (
    MISSED_REMINDER_MINUTES,
    Notifier,
    Scheduler,
    build_notification,
    collect_deadlines,
    notify_send_command,
    signal_running_daemon,
)

TZ = "Asia/Ho_Chi_Minh"


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class RecordingNotifier:
    def __init__(self) -> None:
        self.notifications = []

    def notify(self, notification, *, sound_file: str = "") -> bool:
        self.notifications.append(notification)
        return True


class CountingScheduler(Scheduler):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.process_calls = 0

    def process_due(self, now: datetime) -> list:
        self.process_calls += 1
        return super().process_due(now)


@pytest.fixture
def sched_env(tmp_path, monkeypatch):
    """Isolated scheduler environment: XDG config/data/runtime in a temp dir."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    db = Database(paths.database_path())
    db.initialize()
    env = SimpleNamespace(
        db=db,
        repo=EventRepository(db),
        config=load_config(),
        notifier=RecordingNotifier(),
        tmp=tmp_path,
    )
    try:
        yield env
    finally:
        db.close()


def make_scheduler(env, notifier=None):
    return Scheduler(env.repo, env.config, notifier or env.notifier)


def add_event(
    env,
    start: str = "2026-08-17T19:00:00+07:00",
    end: str = "2026-08-17T19:45:00+07:00",
    *,
    title: str = "Dạy 12A1",
    location: str = "A203",
    priority: Priority = Priority.NORMAL,
    rrule: str | None = None,
    remind: list[int] | None = (15, 5, 0),
    timezone: str = TZ,
):
    event = env.repo.create_event(
        Event(
            title=title,
            start_at=dt(start),
            end_at=dt(end),
            timezone=timezone,
            location=location,
            priority=priority,
            rrule=rrule,
        )
    )
    if remind:
        for offset in remind:
            env.repo.add_reminder(event.id, offset)
    return event


def occurrence_for(env, event, start: str):
    occs = env.repo.get_event_occurrences(
        event.id, dt(start) - timedelta(days=1), dt(start) + timedelta(days=2)
    )
    return occs[0]


@pytest.fixture
def occ_factory(sched_env):
    def make(start: str = "2026-08-17T19:00:00+07:00",
             end: str = "2026-08-17T19:45:00+07:00",
             **kwargs):
        event = add_event(
            sched_env, start=start, end=end,
            title=kwargs.pop("title", "Dạy 12A1"),
            location=kwargs.pop("location", "A203"),
            priority=kwargs.pop("priority", Priority.NORMAL),
            remind=None,
        )
        return occurrence_for(sched_env, event, start), event
    return make


# ---------------------------------------------------------------- deadlines


def test_collect_deadlines_single_event_reminders(sched_env):
    event = add_event(sched_env)
    now = dt("2026-08-17T00:00:00+07:00")
    deadlines = collect_deadlines(sched_env.repo, now, now + timedelta(days=1))

    assert [d.minutes_before for d in deadlines] == [15, 5, 0]
    assert deadlines[0].at == dt("2026-08-17T18:45:00+07:00")
    assert deadlines[1].at == dt("2026-08-17T18:55:00+07:00")
    assert deadlines[2].at == dt("2026-08-17T19:00:00+07:00")
    assert all(d.event_id == event.id for d in deadlines)


def test_collect_deadlines_multiple_events_merged_and_sorted(sched_env):
    add_event(sched_env, start="2026-08-17T19:00:00+07:00", remind=(15,))
    add_event(sched_env, start="2026-08-17T18:30:00+07:00", remind=(5,))
    now = dt("2026-08-17T00:00:00+07:00")

    deadlines = collect_deadlines(sched_env.repo, now, now + timedelta(days=1))

    assert [d.at for d in deadlines] == [
        dt("2026-08-17T18:25:00+07:00"),
        dt("2026-08-17T18:45:00+07:00"),
    ]


def test_collect_deadlines_same_timestamp_order_is_deterministic(sched_env):
    first = add_event(sched_env, start="2026-08-17T19:00:00+07:00", title="A", remind=(15,))
    second = add_event(sched_env, start="2026-08-17T19:00:00+07:00", title="B", remind=(15,))
    now = dt("2026-08-17T00:00:00+07:00")

    deadlines = collect_deadlines(sched_env.repo, now, now + timedelta(days=1))

    assert [d.event_id for d in deadlines] == [first.id, second.id]


def test_no_reminders_means_no_deadlines(sched_env):
    add_event(sched_env, remind=None)
    now = dt("2026-08-17T00:00:00+07:00")
    assert collect_deadlines(sched_env.repo, now, now + timedelta(days=1)) == []


# ------------------------------------------------------------- notifications


def test_build_notification_upcoming_15(occ_factory):
    occ, event = occ_factory()
    notification = build_notification(occ, event, 15)
    assert notification.title == "Sắp tới · 15 phút"
    assert notification.body == "Dạy 12A1\n19:00 - 19:45 · A203"
    assert notification.urgency == "normal"


def test_build_notification_zero_minutes(occ_factory):
    occ, event = occ_factory()
    notification = build_notification(occ, event, 0)
    assert notification.title == "Bắt đầu ngay"


def test_build_notification_missed(occ_factory):
    occ, event = occ_factory()
    notification = build_notification(occ, event, MISSED_REMINDER_MINUTES)
    assert notification.title == "Dạy 12A1"
    assert notification.body == "Bắt đầu lúc 19:00 · A203"


def test_build_notification_without_location(occ_factory):
    occ, event = occ_factory(location="")
    notification = build_notification(occ, event, 15)
    assert notification.body == "Dạy 12A1\n19:00 - 19:45"
    missed = build_notification(occ, event, MISSED_REMINDER_MINUTES)
    assert missed.body == "Bắt đầu lúc 19:00"


def test_build_notification_critical_urgency(occ_factory):
    occ, event = occ_factory(priority=Priority.CRITICAL)
    notification = build_notification(occ, event, 15)
    assert notification.urgency == "critical"


def _plain_notification(urgency: str = "normal", sound: bool = False):
    return SimpleNamespace(
        event_id=1,
        occurrence_start=dt("2026-08-17T19:00:00+07:00"),
        minutes_before=15,
        title="Sắp tới · 15 phút",
        body="Dạy 12A1\n19:00 - 19:45 · A203",
        urgency=urgency,
        sound=sound,
    )


def test_notify_send_command_normal():
    assert notify_send_command(_plain_notification()) == [
        "notify-send", "Sắp tới · 15 phút", "Dạy 12A1\n19:00 - 19:45 · A203",
    ]


def test_notify_send_command_critical():
    assert notify_send_command(_plain_notification(urgency="critical")) == [
        "notify-send", "-u", "critical", "Sắp tới · 15 phút", "Dạy 12A1\n19:00 - 19:45 · A203",
    ]


# ------------------------------------------------------------ process_due


def test_process_due_fires_exact_reminder(sched_env, frozen_clock):
    add_event(sched_env)
    frozen_clock(dt("2026-08-17T18:45:00+07:00"))
    scheduler = make_scheduler(sched_env)

    emitted = scheduler.process_due(clock.now())

    assert len(emitted) == 1
    notification = emitted[0]
    assert notification.minutes_before == 15
    assert notification.title == "Sắp tới · 15 phút"
    assert notification.body == "Dạy 12A1\n19:00 - 19:45 · A203"


def test_process_due_reminders_15_5_0_fire_in_sequence(sched_env, frozen_clock):
    add_event(sched_env)
    scheduler = make_scheduler(sched_env)

    frozen_clock(dt("2026-08-17T18:45:00+07:00"))
    scheduler.process_due(clock.now())
    frozen_clock(dt("2026-08-17T18:55:00+07:00"))
    scheduler.process_due(clock.now())
    frozen_clock(dt("2026-08-17T19:00:00+07:00"))
    scheduler.process_due(clock.now())

    fired = [n.minutes_before for n in sched_env.notifier.notifications]
    assert fired == [15, 5, 0]


def test_process_due_not_before_deadline(sched_env, frozen_clock):
    add_event(sched_env)
    frozen_clock(dt("2026-08-17T18:44:00+07:00"))

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert emitted == []


def test_process_due_catches_up_after_deadline(sched_env, frozen_clock):
    add_event(sched_env)
    frozen_clock(dt("2026-08-17T18:50:00+07:00"))

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert [n.minutes_before for n in emitted] == [15]


def test_process_due_multiple_reminders_same_timestamp(sched_env, frozen_clock):
    first = add_event(sched_env, title="A", remind=(15,))
    second = add_event(sched_env, title="B", remind=(15,))
    frozen_clock(dt("2026-08-17T18:45:00+07:00"))

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert [n.event_id for n in emitted] == [first.id, second.id]
    assert {n.title for n in emitted} == {"Sắp tới · 15 phút"}


def test_process_due_same_instant_does_not_duplicate(sched_env, frozen_clock):
    add_event(sched_env)
    frozen_clock(dt("2026-08-17T18:45:00+07:00"))
    scheduler = make_scheduler(sched_env)

    scheduler.process_due(clock.now())
    scheduler.process_due(clock.now())

    assert len(sched_env.notifier.notifications) == 1


def test_process_due_restart_does_not_duplicate(sched_env, frozen_clock):
    add_event(sched_env)
    frozen_clock(dt("2026-08-17T18:45:00+07:00"))
    make_scheduler(sched_env).process_due(clock.now())

    restarted = make_scheduler(sched_env, RecordingNotifier())
    restarted.process_due(clock.now())

    assert restarted._notifier.notifications == []


def test_process_due_cancelled_occurrence_not_notified(sched_env, frozen_clock):
    event = add_event(
        sched_env, rrule="FREQ=WEEKLY;BYDAY=MO",
        start="2026-08-17T19:00:00+07:00",
    )
    sched_env.repo.add_exception(
        RecurrenceException(
            event_id=event.id,
            occurrence_start=dt("2026-08-17T19:00:00+07:00"),
            action=RecurrenceExceptionAction.CANCEL,
        )
    )
    frozen_clock(dt("2026-08-17T18:45:00+07:00"))

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert emitted == []


def test_process_due_modified_occurrence_uses_modified_time(sched_env, frozen_clock):
    event = add_event(
        sched_env, rrule="FREQ=WEEKLY;BYDAY=MO",
        start="2026-08-17T19:00:00+07:00",
    )
    sched_env.repo.add_exception(
        RecurrenceException(
            event_id=event.id,
            occurrence_start=dt("2026-08-17T19:00:00+07:00"),
            action=RecurrenceExceptionAction.MODIFY,
            start_at=dt("2026-08-17T18:55:00+07:00"),
            end_at=dt("2026-08-17T19:40:00+07:00"),
        )
    )
    scheduler = make_scheduler(sched_env)

    frozen_clock(dt("2026-08-17T18:45:00+07:00"))
    emitted = scheduler.process_due(clock.now())
    assert len(emitted) == 1
    assert "18:55 - 19:40" in emitted[0].body

    frozen_clock(dt("2026-08-17T19:00:00+07:00"))
    assert scheduler.process_due(clock.now()) == []


def test_process_due_deleted_event_not_notified(sched_env, frozen_clock):
    event = add_event(sched_env)
    sched_env.repo.delete_event(event.id)
    frozen_clock(dt("2026-08-17T18:45:00+07:00"))

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert emitted == []


def test_process_due_done_event_not_notified(sched_env, frozen_clock):
    event = add_event(sched_env)
    sched_env.repo.update_event(event.id, status=EventStatus.DONE)
    frozen_clock(dt("2026-08-17T18:45:00+07:00"))

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert emitted == []


def test_process_due_cancelled_event_not_notified(sched_env, frozen_clock):
    event = add_event(sched_env)
    sched_env.repo.update_event(event.id, status=EventStatus.CANCELLED)
    frozen_clock(dt("2026-08-17T18:45:00+07:00"))

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert emitted == []


def test_process_due_active_event_still_notified_after_done_edit(sched_env, frozen_clock):
    event = add_event(sched_env)
    sched_env.repo.update_event(event.id, status=EventStatus.DONE)
    frozen_clock(dt("2026-08-17T18:45:00+07:00"))
    assert make_scheduler(sched_env).process_due(clock.now()) == []

    sched_env.repo.update_event(event.id, status=EventStatus.ACTIVE)
    frozen_clock(dt("2026-08-17T18:45:00+07:00"))
    assert len(make_scheduler(sched_env).process_due(clock.now())) == 1


def test_collect_deadlines_skips_done_and_cancelled(sched_env):
    add_event(sched_env)
    done = add_event(sched_env, start="2026-08-17T20:00:00+07:00",
                     end="2026-08-17T20:45:00+07:00", title="Đã xong")
    cancelled = add_event(sched_env, start="2026-08-17T21:00:00+07:00",
                          end="2026-08-17T21:45:00+07:00", title="Đã hủy")
    sched_env.repo.update_event(done.id, status=EventStatus.DONE)
    sched_env.repo.update_event(cancelled.id, status=EventStatus.CANCELLED)

    deadlines = collect_deadlines(
        sched_env.repo,
        dt("2026-08-17T00:00:00+07:00"),
        dt("2026-08-18T00:00:00+07:00"),
    )

    assert [d.event_id for d in deadlines] == [1, 1, 1]


def test_missed_event_done_event_no_notice(sched_env, frozen_clock):
    add_event(sched_env, remind=None)
    event = add_event(
        sched_env, start="2026-08-17T08:00:00+07:00",
        end="2026-08-17T08:45:00+07:00", title="Đã xong", remind=None,
    )
    sched_env.repo.update_event(event.id, status=EventStatus.DONE)
    frozen_clock(dt("2026-08-17T08:30:00+07:00"))

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert emitted == []


def test_missed_event_cancelled_event_no_notice(sched_env, frozen_clock):
    event = add_event(
        sched_env, start="2026-08-17T08:00:00+07:00",
        end="2026-08-17T08:45:00+07:00", title="Đã hủy", remind=None,
    )
    sched_env.repo.update_event(event.id, status=EventStatus.CANCELLED)
    frozen_clock(dt("2026-08-17T08:30:00+07:00"))

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert emitted == []


def test_process_due_edited_event_reschedules(sched_env, frozen_clock):
    event = add_event(sched_env)
    scheduler = make_scheduler(sched_env)

    frozen_clock(dt("2026-08-17T18:45:00+07:00"))
    scheduler.process_due(clock.now())
    assert len(sched_env.notifier.notifications) == 1

    sched_env.repo.update_event(
        event.id,
        start_at=dt("2026-08-17T20:00:00+07:00"),
        end_at=dt("2026-08-17T20:45:00+07:00"),
    )
    frozen_clock(dt("2026-08-17T18:45:00+07:00"))
    assert scheduler.process_due(clock.now()) == []

    frozen_clock(dt("2026-08-17T19:45:00+07:00"))
    emitted = scheduler.process_due(clock.now())
    assert len(emitted) == 1
    assert "20:00 - 20:45" in emitted[0].body


def test_process_due_event_without_reminders(sched_env, frozen_clock):
    add_event(sched_env, remind=None)
    frozen_clock(dt("2026-08-17T19:00:00+07:00"))

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert emitted == []


# ------------------------------------------------------------ timezone/clock


def test_process_due_timezone_conversion(sched_env, frozen_clock):
    add_event(
        sched_env,
        start="2026-08-17T19:00:00-04:00",
        end="2026-08-17T19:45:00-04:00",
        timezone="America/New_York",
        remind=(15,),
    )
    frozen_clock(dt("2026-08-17T22:45:00+00:00"))  # 18:45 EDT

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert len(emitted) == 1
    assert "19:00 - 19:45" in emitted[0].body


def test_process_due_cross_midnight_occurrence(sched_env, frozen_clock):
    add_event(
        sched_env, start="2026-08-17T23:00:00+07:00", end="2026-08-18T01:00:00+07:00",
        remind=(0,),
    )
    frozen_clock(dt("2026-08-17T23:00:00+07:00"))

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert len(emitted) == 1
    assert "23:00 - 01:00" in emitted[0].body


# ---------------------------------------------------------- suspend/resume


def test_missed_event_single_notification(sched_env, frozen_clock):
    event = add_event(
        sched_env, start="2026-08-17T18:50:00+07:00", end="2026-08-17T19:35:00+07:00",
    )
    frozen_clock(dt("2026-08-17T19:00:00+07:00"))

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert len(emitted) == 1
    notification = emitted[0]
    assert notification.minutes_before == MISSED_REMINDER_MINUTES
    assert notification.title == event.title
    assert notification.body == "Bắt đầu lúc 18:50 · A203"


def test_missed_event_not_replayed_as_individual_reminders(sched_env, frozen_clock):
    add_event(sched_env, start="2026-08-17T18:50:00+07:00")
    frozen_clock(dt("2026-08-17T19:00:00+07:00"))

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert len(emitted) == 1
    assert emitted[0].minutes_before == MISSED_REMINDER_MINUTES


def test_missed_event_suppressed_when_reminders_already_fired(sched_env, frozen_clock):
    event = add_event(sched_env, start="2026-08-17T18:50:00+07:00")
    frozen_clock(dt("2026-08-17T18:45:00+07:00"))
    scheduler = make_scheduler(sched_env)
    scheduler.process_due(clock.now())

    frozen_clock(dt("2026-08-17T19:00:00+07:00"))
    emitted = scheduler.process_due(clock.now())

    assert emitted == []


def test_missed_event_stale_beyond_window_ignored(sched_env, frozen_clock):
    add_event(
        sched_env, start="2026-08-17T18:00:00+07:00", end="2026-08-17T19:30:00+07:00",
    )
    frozen_clock(dt("2026-08-17T19:00:00+07:00"))  # started 60 min ago (> 30)

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert emitted == []


def test_missed_event_ongoing_but_started_before_window_ignored(sched_env, frozen_clock):
    add_event(
        sched_env, start="2026-08-17T17:30:00+07:00", end="2026-08-17T19:30:00+07:00",
    )
    frozen_clock(dt("2026-08-17T19:00:00+07:00"))

    emitted = make_scheduler(sched_env).process_due(clock.now())

    assert emitted == []


def test_missed_event_dedup_across_restart(sched_env, frozen_clock):
    add_event(sched_env, start="2026-08-17T18:50:00+07:00")
    frozen_clock(dt("2026-08-17T19:00:00+07:00"))
    make_scheduler(sched_env).process_due(clock.now())

    restarted = make_scheduler(sched_env, RecordingNotifier())
    restarted.process_due(clock.now())

    assert restarted._notifier.notifications == []


# ------------------------------------------------------------------ Notifier


def test_notifier_sends_sound_when_configured():
    calls = []
    notifier = Notifier(runner=calls.append)
    notification = _plain_notification(sound=True)

    delivered = notifier.notify(notification, sound_file="/tmp/ding.ogg")

    assert delivered is True
    assert calls == [["notify-send", "Sắp tới · 15 phút", "Dạy 12A1\n19:00 - 19:45 · A203"],
                     ["pw-play", "/tmp/ding.ogg"]]


def test_notifier_sound_disabled_by_notification():
    calls = []
    notifier = Notifier(runner=calls.append)
    notification = _plain_notification(sound=False)

    notifier.notify(notification, sound_file="/tmp/ding.ogg")

    assert calls == [["notify-send", "Sắp tới · 15 phút", "Dạy 12A1\n19:00 - 19:45 · A203"]]


def test_notifier_sound_skipped_without_sound_file():
    calls = []
    notifier = Notifier(runner=calls.append)
    notification = _plain_notification(sound=True)

    notifier.notify(notification, sound_file="")

    assert calls == [["notify-send", "Sắp tới · 15 phút", "Dạy 12A1\n19:00 - 19:45 · A203"]]


def test_notifier_missing_notify_send_no_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    notifier = Notifier()
    notification = _plain_notification(sound=True)

    delivered = notifier.notify(notification, sound_file="x.ogg")

    assert delivered is False


def test_notifier_missing_pw_play_keeps_notification(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "marker"
    (bin_dir / "notify-send").write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$MARKER\"\n"
    )
    (bin_dir / "notify-send").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("MARKER", str(marker))
    notifier = Notifier()
    notification = _plain_notification(sound=True)

    delivered = notifier.notify(notification, sound_file="/tmp/ding.ogg")

    assert delivered is True
    assert marker.exists()


def test_process_due_sound_gating_via_config_and_reminder(sched_env, frozen_clock):
    calls = []
    env = sched_env
    env.config.notification.sound_file = str(env.tmp / "ding.ogg")
    scheduler = Scheduler(env.repo, env.config, Notifier(runner=calls.append))

    event_a = add_event(env, start="2026-08-17T19:00:00+07:00", title="A", remind=(15,))
    env.repo.remove_reminder(event_a.id, 15)
    env.repo.add_reminder(event_a.id, 15, sound=True)

    event_b = add_event(
        env, start="2026-08-17T20:00:00+07:00", end="2026-08-17T20:45:00+07:00",
        title="B", remind=(15,),
    )
    env.repo.remove_reminder(event_b.id, 15)
    env.repo.add_reminder(event_b.id, 15, sound=False)

    frozen_clock(dt("2026-08-17T18:45:00+07:00"))
    scheduler.process_due(clock.now())
    assert any(cmd[0] == "pw-play" for cmd in calls)

    calls.clear()
    frozen_clock(dt("2026-08-17T19:45:00+07:00"))
    scheduler.process_due(clock.now())
    assert not any(cmd[0] == "pw-play" for cmd in calls)

    add_event(
        env, start="2026-08-17T21:00:00+07:00", end="2026-08-17T21:45:00+07:00",
        title="C", remind=(15,),
    )
    env.config.notification.sound = False
    calls.clear()
    frozen_clock(dt("2026-08-17T20:45:00+07:00"))
    scheduler.process_due(clock.now())
    assert not any(cmd[0] == "pw-play" for cmd in calls)


# ------------------------------------------------------------- cycle/once


def test_cycle_returns_seconds_until_next_deadline(sched_env, frozen_clock):
    add_event(sched_env)
    frozen_clock(dt("2026-08-17T18:00:00+07:00"))

    wait = make_scheduler(sched_env)._cycle(clock.now())

    assert wait == pytest.approx(45 * 60)


def test_cycle_none_when_nothing_upcoming(sched_env, frozen_clock):
    add_event(sched_env, remind=None)
    frozen_clock(dt("2026-08-17T18:00:00+07:00"))

    wait = make_scheduler(sched_env)._cycle(clock.now())

    assert wait is None


def test_cycle_recomputes_after_edit(sched_env, frozen_clock):
    event = add_event(sched_env)
    scheduler = make_scheduler(sched_env)
    frozen_clock(dt("2026-08-17T18:00:00+07:00"))
    assert scheduler._cycle(clock.now()) == pytest.approx(45 * 60)

    sched_env.repo.update_event(
        event.id, start_at=dt("2026-08-17T20:00:00+07:00"),
        end_at=dt("2026-08-17T20:45:00+07:00"),
    )
    assert scheduler._cycle(clock.now()) == pytest.approx(105 * 60)


def test_request_reload_sets_event(sched_env):
    scheduler = make_scheduler(sched_env)
    assert not scheduler._reload_event.is_set()
    scheduler.request_reload()
    assert scheduler._reload_event.is_set()


def test_request_stop_sets_event(sched_env):
    scheduler = make_scheduler(sched_env)
    scheduler.request_stop()
    assert scheduler._stop_event.is_set()


def test_run_once_mode_fires_and_returns_zero(sched_env, frozen_clock):
    add_event(sched_env)
    frozen_clock(dt("2026-08-17T18:45:00+07:00"))

    code = asyncio.run(make_scheduler(sched_env).run(once=True))

    assert code == 0
    assert len(sched_env.notifier.notifications) == 1
    assert not (paths.runtime_dir() / "scheduled.pid").exists()


# ------------------------------------------------------------ run loop


def test_run_loop_fires_exact_deadline_and_shuts_down_on_sigterm(sched_env):
    repo, config, notifier = sched_env.repo, sched_env.config, sched_env.notifier
    start = datetime.now(timezone.utc) + timedelta(seconds=16.5)
    event = repo.create_event(
        Event(
            title="Loop", start_at=start,
            end_at=start + timedelta(minutes=1), timezone=TZ,
        )
    )
    repo.add_reminder(event.id, 15)
    scheduler = Scheduler(repo, config, notifier)
    pid_path = paths.runtime_dir() / "scheduled.pid"
    seen = {}

    def driver():
        for _ in range(100):
            if pid_path.exists():
                seen["pid"] = pid_path.read_text().strip()
                break
            time.sleep(0.05)
        time.sleep(3.0)
        os.kill(os.getpid(), signal.SIGTERM)

    thread = threading.Thread(target=driver)
    thread.start()
    try:
        code = asyncio.run(scheduler.run())
    finally:
        thread.join()

    assert code == 0
    assert len(notifier.notifications) == 1
    assert notifier.notifications[0].minutes_before == 15
    assert seen.get("pid") == str(os.getpid())
    assert not pid_path.exists()


def test_run_loop_wakes_on_sigusr1_and_reloads(sched_env):
    repo, config = sched_env.repo, sched_env.config
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    repo.create_event(
        Event(
            title="Far", start_at=start,
            end_at=start + timedelta(minutes=1), timezone=TZ,
        )
    )
    repo.add_reminder(1, 15)
    scheduler = CountingScheduler(repo, config, sched_env.notifier)

    def driver():
        time.sleep(0.4)
        os.kill(os.getpid(), signal.SIGUSR1)
        time.sleep(0.3)
        os.kill(os.getpid(), signal.SIGTERM)

    thread = threading.Thread(target=driver)
    thread.start()
    try:
        code = asyncio.run(scheduler.run())
    finally:
        thread.join()

    assert code == 0
    assert scheduler.process_calls >= 2


# ----------------------------------------------------- CLI signal integration


def _signal_trap_child(tmp_path, flag_name: str = "flag", ready_name: str = "ready"):
    """Start a child that traps SIGUSR1 and writes *flag* when received.

    Writes *ready* after installing the handler, so the parent never signals
    before the handler exists (a race that would kill the child with the
    default SIGUSR1 action).
    """
    child = tmp_path / "child.py"
    flag = tmp_path / flag_name
    ready = tmp_path / ready_name
    child.write_text(
        "import signal, sys, time\n"
        "def handler(signum, frame):\n"
        "    open(sys.argv[1], 'w').write('ok')\n"
        "signal.signal(signal.SIGUSR1, handler)\n"
        "open(sys.argv[2], 'w').write('ready')\n"
        "time.sleep(30)\n"
    )
    proc = subprocess.Popen([sys.executable, str(child), str(flag), str(ready)])
    return proc, flag, ready


def _wait_for(path, timeout_loops: int = 100) -> bool:
    for _ in range(timeout_loops):
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def _wait_for_content(path, expected: str, timeout_loops: int = 100) -> bool:
    for _ in range(timeout_loops):
        try:
            if path.read_text().strip() == expected:
                return True
        except FileNotFoundError:
            pass
        time.sleep(0.05)
    return False


def test_signal_running_daemon_delivers_sigusr1(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    proc, flag, ready = _signal_trap_child(tmp_path)
    try:
        assert _wait_for(ready)
        pid_path = paths.runtime_dir() / "scheduled.pid"
        pid_path.parent.mkdir(parents=True)
        pid_path.write_text(f"{proc.pid}\n")

        assert signal_running_daemon() is True
        assert _wait_for_content(flag, "ok")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_signal_running_daemon_without_pid_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    assert signal_running_daemon() is False


def test_signal_running_daemon_stale_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    pid_path = paths.runtime_dir() / "scheduled.pid"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text("999999999\n")
    assert signal_running_daemon() is False


def test_signal_running_daemon_invalid_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    pid_path = paths.runtime_dir() / "scheduled.pid"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text("not-a-pid\n")
    assert signal_running_daemon() is False


def test_cli_add_signals_running_daemon(cli_env, run, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    proc, flag, ready = _signal_trap_child(tmp_path)
    try:
        assert _wait_for(ready)
        pid_path = paths.runtime_dir() / "scheduled.pid"
        pid_path.parent.mkdir(parents=True)
        pid_path.write_text(f"{proc.pid}\n")

        code, _ = run(
            ["add", "--title", "X", "--date", "2026-08-17",
             "--start", "09:00", "--end", "09:30"]
        )

        assert code == 0
        assert _wait_for_content(flag, "ok")
    finally:
        proc.terminate()
        proc.wait(timeout=5)