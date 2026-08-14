"""Configuration loading and validation.

Uses only the standard library (``tomllib``) and follows the defaults
documented in ARCHITECTURE.md. A missing config file yields sane defaults;
an invalid config raises :class:`ConfigError` with a helpful message.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from hyprschedule import paths

DEFAULT_CONFIG = """\
timezone = "Asia/Ho_Chi_Minh"

[schedule]
day_start = "06:00"
day_end = "23:00"
min_free_minutes = 15

[widget]
max_events = 6
show_free_time = true
show_tomorrow_count = true
refresh_seconds = 30

[lockscreen]
max_events = 2
show_private = false
show_tomorrow_count = true

[notification]
default_reminders = [15, 5, 0]
sound = true
sound_file = ""
missed_event_window_minutes = 30

[recurrence]
skip_classes_on_holidays = true
"""

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


@dataclass
class ScheduleConfig:
    day_start: str
    day_end: str
    min_free_minutes: int


@dataclass
class WidgetConfig:
    max_events: int
    show_free_time: bool
    show_tomorrow_count: bool
    refresh_seconds: int


@dataclass
class LockscreenConfig:
    max_events: int
    show_private: bool
    show_tomorrow_count: bool


@dataclass
class NotificationConfig:
    default_reminders: list[int]
    sound: bool
    sound_file: str
    missed_event_window_minutes: int


@dataclass
class RecurrenceConfig:
    skip_classes_on_holidays: bool


@dataclass
class Config:
    timezone: str
    schedule: ScheduleConfig
    widget: WidgetConfig
    lockscreen: LockscreenConfig
    notification: NotificationConfig
    recurrence: RecurrenceConfig


def _defaults() -> dict[str, Any]:
    return tomllib.loads(DEFAULT_CONFIG)


def _fail(path: Path, message: str) -> ConfigError:
    return ConfigError(f"Invalid config file {path}: {message}")


def _validate_time(value: Any, path: Path, key: str) -> str:
    if not isinstance(value, str) or not _TIME_RE.match(value):
        raise _fail(path, f'"{key}" must be HH:MM with hours 00-23 and minutes 00-59, got {value!r}')
    return value


def _validate_positive_int(value: Any, path: Path, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _fail(path, f'"{key}" must be a positive integer, got {value!r}')
    return value


def _validate_non_negative_int(value: Any, path: Path, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(path, f'"{key}" must be a non-negative integer, got {value!r}')
    return value


def _validate_bool(value: Any, path: Path, key: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(path, f'"{key}" must be a boolean, got {value!r}')
    return value


def _validate_sound_file(value: Any, path: Path, key: str) -> str:
    if not isinstance(value, str):
        raise _fail(path, f'"{key}" must be a string path, got {value!r}')
    return value


def _validate_reminders(value: Any, path: Path) -> list[int]:
    if not isinstance(value, list):
        raise _fail(path, f'"default_reminders" must be a list of non-negative integers, got {value!r}')
    result: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise _fail(path, f'"default_reminders" entries must be non-negative integers, got {item!r}')
        result.append(item)
    return result


def _validate_timezone(value: Any, path: Path) -> str:
    if not isinstance(value, str):
        raise _fail(path, f'"timezone" must be a string, got {value!r}')
    try:
        ZoneInfo(value)
    except Exception:
        raise _fail(path, f'unknown timezone {value!r} (use an IANA name like "Asia/Ho_Chi_Minh")')
    return value


def load_config(path: Path | None = None) -> Config:
    """Load configuration from *path* (default: the XDG config file).

    A missing file yields defaults. Invalid content raises :class:`ConfigError`.
    Unknown keys and sections are ignored for forward compatibility.
    """
    if path is None:
        path = paths.config_file()
    data = _defaults()

    if path.exists():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc
        for key, value in raw.items():
            if isinstance(value, dict) and isinstance(data.get(key), dict):
                data[key].update(value)
            else:
                data[key] = value

    schedule_raw = data.get("schedule", {})
    widget_raw = data.get("widget", {})
    lockscreen_raw = data.get("lockscreen", {})
    notification_raw = data.get("notification", {})
    recurrence_raw = data.get("recurrence", {})

    timezone = _validate_timezone(data.get("timezone"), path)
    schedule = ScheduleConfig(
        day_start=_validate_time(schedule_raw.get("day_start"), path, "schedule.day_start"),
        day_end=_validate_time(schedule_raw.get("day_end"), path, "schedule.day_end"),
        min_free_minutes=_validate_non_negative_int(
            schedule_raw.get("min_free_minutes"),
            path,
            "schedule.min_free_minutes",
        ),
    )
    widget = WidgetConfig(
        max_events=_validate_positive_int(widget_raw.get("max_events"), path, "widget.max_events"),
        show_free_time=_validate_bool(widget_raw.get("show_free_time"), path, "widget.show_free_time"),
        show_tomorrow_count=_validate_bool(widget_raw.get("show_tomorrow_count"), path, "widget.show_tomorrow_count"),
        refresh_seconds=_validate_positive_int(widget_raw.get("refresh_seconds"), path, "widget.refresh_seconds"),
    )
    lockscreen = LockscreenConfig(
        max_events=_validate_positive_int(lockscreen_raw.get("max_events"), path, "lockscreen.max_events"),
        show_private=_validate_bool(lockscreen_raw.get("show_private"), path, "lockscreen.show_private"),
        show_tomorrow_count=_validate_bool(
            lockscreen_raw.get("show_tomorrow_count"), path, "lockscreen.show_tomorrow_count"
        ),
    )
    notification = NotificationConfig(
        default_reminders=_validate_reminders(notification_raw.get("default_reminders"), path),
        sound=_validate_bool(notification_raw.get("sound"), path, "notification.sound"),
        sound_file=_validate_sound_file(
            notification_raw.get("sound_file"), path, "notification.sound_file"
        ),
        missed_event_window_minutes=_validate_positive_int(
            notification_raw.get("missed_event_window_minutes"),
            path,
            "notification.missed_event_window_minutes",
        ),
    )
    recurrence = RecurrenceConfig(
        skip_classes_on_holidays=_validate_bool(
            recurrence_raw.get("skip_classes_on_holidays"),
            path,
            "recurrence.skip_classes_on_holidays",
        ),
    )

    return Config(
        timezone=timezone,
        schedule=schedule,
        widget=widget,
        lockscreen=lockscreen,
        notification=notification,
        recurrence=recurrence,
    )