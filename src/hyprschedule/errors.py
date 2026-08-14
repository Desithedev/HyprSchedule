"""Domain errors for the event engine.

A small hierarchy so that normal user input mistakes produce helpful
messages instead of raw SQLite tracebacks.
"""

from __future__ import annotations

from hyprschedule.config import ConfigError

__all__ = [
    "HyprScheduleError",
    "EventNotFound",
    "InvalidEvent",
    "InvalidRecurrence",
    "InvalidReminder",
    "InvalidException",
    "ConfigError",
]


class HyprScheduleError(Exception):
    """Base class for all HyprSchedule domain errors."""


class EventNotFound(HyprScheduleError):
    """An event with the requested id does not exist."""


class InvalidEvent(HyprScheduleError):
    """An event violates domain validation rules."""


class InvalidRecurrence(HyprScheduleError):
    """A recurrence rule is outside the supported weekly subset."""


class InvalidReminder(HyprScheduleError):
    """A reminder violates validation rules (e.g. negative offset, duplicate)."""


class InvalidException(HyprScheduleError):
    """A recurrence exception violates validation rules."""