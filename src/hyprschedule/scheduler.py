"""The ``scheduled`` daemon (Phase 3).

An asyncio scheduler that fires exact-time reminders and handles suspend /
resume misses. Design (ARCHITECTURE.md, Phase 3):

- **no polling loop**: it computes the next reminder deadline and sleeps
  exactly until then (bounded by a recompute horizon), waking early only on
  SIGUSR1 (schedule changed) or SIGTERM/SIGINT (shutdown).
- **notification dedup**: every emitted notification is recorded in
  ``notification_log`` (UNIQUE(event_id, occurrence_start, reminder_minutes));
  on restart the daemon skips anything already logged, so reminders never
  double-fire.
- **suspend/resume**: after a wake, occurrences that started during the
  downtime (within ``missed_event_window_minutes``) get ONE missed-event
  notice each — reminders are never replayed individually.
- **best-effort desktop integration**: ``notify-send`` / ``pw-play`` are
  optional; a missing binary or a failed subprocess is logged, never fatal.

The daemon only reads schedule data (the CLI is the sole writer of user data)
but is the only writer of ``notification_log``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import signal
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence
from zoneinfo import ZoneInfo

from hyprschedule import paths
from hyprschedule.clock import clock
from hyprschedule.config import Config, ConfigError, load_config
from hyprschedule.database import Database
from hyprschedule.logging import get_logger, setup_logging
from hyprschedule.models import EventStatus, Occurrence, Priority, Reminder
from hyprschedule.repository import EventRepository
from hyprschedule.timeutil import ensure_aware

RELOAD_SIGNAL = signal.SIGUSR1
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)
HORIZON_DAYS = 60
PID_FILE_NAME = "scheduled.pid"
MISSED_REMINDER_MINUTES = -1
INACTIVE_STATUSES = (EventStatus.DONE, EventStatus.CANCELLED)


def _is_remindable(occ: Occurrence) -> bool:
    """Occurrences of done/cancelled events never produce notifications."""
    return occ.status not in INACTIVE_STATUSES


@dataclass
class Notification:
    """A concrete notification for one reminder instance.

    ``minutes_before`` is the reminder offset, or :data:`MISSED_REMINDER_MINUTES`
    for a missed-event notice; together with ``occurrence_start`` it is the
    dedup key stored in ``notification_log``.
    """

    event_id: int
    occurrence_start: datetime
    minutes_before: int
    title: str
    body: str
    urgency: str = "normal"
    sound: bool = False


@dataclass(frozen=True)
class Deadline:
    """A reminder deadline: the UTC instant a reminder should fire."""

    at: datetime
    event_id: int
    occurrence_start: datetime
    minutes_before: int


def _reminders_map(repo: EventRepository, event_ids: set[int]) -> dict[int, list[Reminder]]:
    return {event_id: repo.list_reminders(event_id) for event_id in event_ids}


def collect_deadlines(
    repo: EventRepository, range_start: datetime, range_end: datetime
) -> list[Deadline]:
    """All reminder deadlines for occurrences overlapping ``[range_start, range_end)``.

    Deterministic ordering by ``(at, event_id, minutes_before)`` so equal
    deadlines (e.g. two events at the same instant) fire in a stable order.
    """
    occurrences = repo.get_occurrences(range_start, range_end)
    reminders = _reminders_map(repo, {occ.event_id for occ in occurrences})
    deadlines: list[Deadline] = []
    for occ in occurrences:
        if not _is_remindable(occ):
            continue
        for reminder in reminders.get(occ.event_id, []):
            deadlines.append(
                Deadline(
                    at=occ.occurrence_start - timedelta(minutes=reminder.minutes_before),
                    event_id=occ.event_id,
                    occurrence_start=occ.occurrence_start,
                    minutes_before=reminder.minutes_before,
                )
            )
    deadlines.sort(key=lambda d: (d.at, d.event_id, d.minutes_before))
    return deadlines


def _format_time(value: datetime, timezone_name: str) -> str:
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%H:%M")


def build_notification(occ: Occurrence, event: object, minutes_before: int) -> Notification:
    """Build the user-facing notification for one reminder/missed instance.

    Upcoming:  title "Sắp tới · 15 phút" (or "Bắt đầu ngay" at 0 minutes),
               body "Dạy 12A1\\n19:00 - 19:45 · A203".
    Missed:    title = event title, body "Bắt đầu lúc 19:00 · A203".
    """
    tz = event.timezone
    if minutes_before < 0:
        title = occ.title
        body = f"Bắt đầu lúc {_format_time(occ.occurrence_start, tz)}"
        if occ.location:
            body += f" · {occ.location}"
    else:
        if minutes_before == 0:
            title = "Bắt đầu ngay"
        else:
            title = f"Sắp tới · {minutes_before} phút"
        body = (
            f"{occ.title}\n{_format_time(occ.occurrence_start, tz)}"
            f" - {_format_time(occ.occurrence_end, tz)}"
        )
        if occ.location:
            body += f" · {occ.location}"
    urgency = "critical" if event.priority is Priority.CRITICAL else "normal"
    return Notification(
        event_id=occ.event_id,
        occurrence_start=occ.occurrence_start,
        minutes_before=minutes_before,
        title=title,
        body=body,
        urgency=urgency,
    )


def notify_send_command(notification: Notification, binary: str = "notify-send") -> list[str]:
    """The exact ``notify-send`` argv for a notification (pure, testable)."""
    args = [binary]
    if notification.urgency == "critical":
        args += ["-u", "critical"]
    args += [notification.title, notification.body]
    return args


class Notifier:
    """Sends desktop notifications via ``notify-send`` (and ``pw-play`` for sound).

    External tools are best-effort: a missing binary or a failed subprocess is
    logged and swallowed — the backend must never crash because a desktop tool
    is absent. Tests inject ``runner`` to observe commands without a desktop
    session.
    """

    def __init__(
        self,
        *,
        notify_bin: str = "notify-send",
        play_bin: str = "pw-play",
        runner: Callable[[list[str]], None] | None = None,
    ) -> None:
        self._notify_bin = notify_bin
        self._play_bin = play_bin
        self._runner = runner
        self._log = get_logger("notifier")

    def _available(self, binary: str) -> bool:
        if self._runner is not None:
            return True
        return shutil.which(binary) is not None

    def _run_command(self, args: list[str]) -> None:
        if self._runner is not None:
            self._runner(args)
            return
        try:
            subprocess.run(args, capture_output=True, check=False, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._log.warning("%s failed: %s", args[0], exc)

    def notify(self, notification: Notification, *, sound_file: str = "") -> bool:
        """Emit one notification; returns True when it was delivered.

        Never raises. Sound is only attempted when the notification requests
        it *and* a sound file is configured.
        """
        if not self._available(self._notify_bin):
            self._log.warning("notify-send not found; notification skipped")
            return False
        self._run_command(notify_send_command(notification, self._notify_bin))
        if notification.sound:
            if not sound_file:
                self._log.debug("sound requested but no notification.sound_file configured")
            elif not self._available(self._play_bin):
                self._log.warning("pw-play not found; sound skipped")
            else:
                self._run_command([self._play_bin, sound_file])
        return True


class Scheduler:
    """Fires exact reminders and handles suspend/resume misses.

    The synchronous surface (:meth:`process_due`, :meth:`next_deadline`,
    :meth:`_cycle`) is fully testable with a frozen clock and an injected
    notifier; the async loop (:meth:`run`) wires signals and sleeps.
    """

    def __init__(
        self,
        repo: EventRepository,
        config: Config,
        notifier: Notifier | None = None,
        *,
        logger: object | None = None,
    ) -> None:
        self._repo = repo
        self._config = config
        self._notifier = notifier or Notifier()
        self._log = logger or get_logger("scheduler")
        self._reload_event = asyncio.Event()
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------- public control

    def request_reload(self) -> None:
        """Wake the loop and recompute deadlines (SIGUSR1 handler)."""
        self._log.info("reload requested (SIGUSR1)")
        self._reload_event.set()

    def request_stop(self) -> None:
        """Request a graceful shutdown (SIGTERM/SIGINT handler)."""
        self._log.info("stop requested")
        self._stop_event.set()

    # ------------------------------------------------------ core processing

    def process_due(self, now: datetime) -> list[Notification]:
        """Fire every due reminder once and emit one missed-event notice per
        missed occurrence; returns the emitted notifications (for tests).

        Reminders for occurrences that started during a suspension are never
        replayed individually — only the single missed notice is produced.
        """
        now = ensure_aware(now)
        events = {event.id: event for event in self._repo.list_events()}
        emitted: list[Notification] = []
        sound_file = self._config.notification.sound_file

        for occ, reminder in self._due_reminders(now):
            event = events.get(occ.event_id)
            if event is None:
                continue
            if self._repo.notification_exists(
                occ.event_id, occ.occurrence_start, reminder.minutes_before
            ):
                continue
            notification = build_notification(occ, event, reminder.minutes_before)
            notification.sound = reminder.sound and self._config.notification.sound
            self._notifier.notify(notification, sound_file=sound_file)
            self._repo.record_notification(
                occ.event_id, occ.occurrence_start, reminder.minutes_before,
                notified_at=now,
            )
            emitted.append(notification)

        window = timedelta(minutes=self._config.notification.missed_event_window_minutes)
        for occ in self._missed_occurrences(now, window):
            event = events.get(occ.event_id)
            if event is None:
                continue
            if self._repo.any_notification(occ.event_id, occ.occurrence_start):
                continue
            notification = build_notification(occ, event, MISSED_REMINDER_MINUTES)
            self._notifier.notify(notification, sound_file=sound_file)
            self._repo.record_notification(
                occ.event_id, occ.occurrence_start, MISSED_REMINDER_MINUTES,
                notified_at=now,
            )
            emitted.append(notification)

        return emitted

    def next_deadline(self, now: datetime) -> datetime | None:
        """The next strictly-future reminder deadline after *now* (or None)."""
        now = ensure_aware(now)
        horizon = now + timedelta(days=HORIZON_DAYS)
        deadlines = [
            deadline
            for deadline in collect_deadlines(self._repo, now, horizon)
            if deadline.at > now
        ]
        return deadlines[0].at if deadlines else None

    def _cycle(self, now: datetime) -> float | None:
        """One loop iteration: fire due notifications and return seconds until
        the next deadline (None when nothing is upcoming)."""
        now = ensure_aware(now)
        self.process_due(now)
        deadline = self.next_deadline(now)
        if deadline is None:
            return None
        return max((deadline - now).total_seconds(), 0.0)

    # --------------------------------------------------------- due reminders

    def _due_reminders(self, now: datetime) -> list[tuple[Occurrence, Reminder]]:
        """(occurrence, reminder) pairs whose deadline has already arrived.

        Occurrences whose start is at or after *now* are scanned over a window
        bounded by the largest reminder offset, so an occurrence that started
        during a suspension (deadline in the past) is deliberately never
        replayed here — that is handled by the single missed notice.
        """
        max_offset = self._repo.max_reminder_offset()
        if max_offset is None:
            return []
        range_end = now + timedelta(minutes=max_offset) + timedelta(seconds=1)
        result: list[tuple[Occurrence, Reminder]] = []
        for occ in self._repo.get_occurrences(now, range_end):
            if occ.occurrence_start < now or not _is_remindable(occ):
                continue
            for reminder in self._repo.list_reminders(occ.event_id):
                if occ.occurrence_start - timedelta(minutes=reminder.minutes_before) <= now:
                    result.append((occ, reminder))
        result.sort(
            key=lambda pair: (
                pair[0].occurrence_start - timedelta(minutes=pair[1].minutes_before),
                pair[0].event_id,
                pair[1].minutes_before,
            )
        )
        return result

    # --------------------------------------------------------- missed events

    def _missed_occurrences(self, now: datetime, window: timedelta) -> list[Occurrence]:
        """Occurrences that started in ``[now - window, now)`` — i.e. during a
        suspension. Occurrences that started before the window (stale) are
        ignored, even if still ongoing."""
        if window <= timedelta(0):
            return []
        start = now - window
        return [
            occ
            for occ in self._repo.get_occurrences(start, now)
            if occ.occurrence_start >= start and _is_remindable(occ)
        ]

    # ------------------------------------------------------------- run loop

    def _install_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        for sig, callback in (
            (RELOAD_SIGNAL, self.request_reload),
            (signal.SIGTERM, self.request_stop),
            (signal.SIGINT, self.request_stop),
        ):
            try:
                loop.add_signal_handler(sig, callback)
            except (NotImplementedError, RuntimeError, ValueError) as exc:
                self._log.warning("cannot install signal handler for %s: %s", sig, exc)

    def _remove_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        for sig in (RELOAD_SIGNAL, *STOP_SIGNALS):
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                pass

    async def _wait_until_event_or_stop(self, seconds: float | None) -> None:
        """Sleep up to *seconds*, waking early on a reload or stop request."""
        reload_task = asyncio.create_task(self._reload_event.wait())
        stop_task = asyncio.create_task(self._stop_event.wait())
        if seconds is not None and seconds > 0:
            sleep_task = asyncio.create_task(asyncio.sleep(seconds))
            tasks = {reload_task, stop_task, sleep_task}
        else:
            sleep_task = None
            tasks = {reload_task, stop_task}
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except asyncio.CancelledError:
                pass
        if sleep_task is not None:
            sleep_task.cancel()
            try:
                await sleep_task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> int:
        loop = asyncio.get_running_loop()
        self._install_signal_handlers(loop)
        try:
            while True:
                self._reload_event.clear()
                if self._stop_event.is_set():
                    return 0
                wait = self._cycle(clock.now())
                if self._stop_event.is_set():
                    return 0
                if wait is None:
                    self._log.info("no upcoming reminders; sleeping until reload")
                else:
                    self._log.info("next reminder in %.0fs", wait)
                await self._wait_until_event_or_stop(wait)
        finally:
            self._remove_signal_handlers(loop)

    def _write_pid_file(self, pid_path: Path) -> None:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

    @staticmethod
    def _remove_pid_file(pid_path: Path) -> None:
        try:
            pid_path.unlink()
        except FileNotFoundError:
            pass

    async def run(self, *, once: bool = False) -> int:
        """Run the scheduler. With ``once=True`` process due notifications and
        exit (test / smoke mode); otherwise run the daemon loop, writing a pid
        file that the CLI can signal with SIGUSR1."""
        pid_path = paths.runtime_dir() / PID_FILE_NAME
        if not once:
            self._write_pid_file(pid_path)
        try:
            if once:
                self.process_due(clock.now())
                return 0
            return await self._loop()
        finally:
            if not once:
                self._remove_pid_file(pid_path)


def signal_running_daemon(sig: int = RELOAD_SIGNAL) -> bool:
    """Best-effort: send *sig* to a running ``scheduled`` daemon (SIGUSR1).

    Reads the daemon pid file from the runtime dir. Returns True when a signal
    was delivered. Never raises: a missing or stale pid file, or a dead
    process, is silently ignored so ``schedctl add/edit/delete`` never fail
    because a daemon is not running (the DB remains the source of truth).
    """
    pid_path = paths.runtime_dir() / PID_FILE_NAME
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scheduled",
        description="HyprSchedule scheduler daemon (Phase 3): exact reminders, "
        "notification dedup, suspend/resume recovery.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="process due notifications and exit (test/smoke mode)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(verbose=args.verbose)
    logger = get_logger("scheduler")
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db = Database(paths.database_path())
    db.initialize()
    scheduler = Scheduler(EventRepository(db), config, logger=logger)
    try:
        return asyncio.run(scheduler.run(once=args.once))
    except KeyboardInterrupt:
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
