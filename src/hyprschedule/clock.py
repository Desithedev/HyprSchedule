"""Testable clock boundary.

Commands that depend on "now" (today, tomorrow, week, next) read the current
time exclusively through :data:`clock`, so tests can pin a fixed instant with
:meth:`Clock.set` instead of freezing ``datetime.now`` everywhere.
"""

from __future__ import annotations

from datetime import datetime, timezone


class Clock:
    def __init__(self) -> None:
        self._frozen: datetime | None = None

    def now(self) -> datetime:
        """Current UTC time (timezone-aware)."""
        if self._frozen is not None:
            return self._frozen
        return datetime.now(timezone.utc)

    def set(self, value: datetime | None) -> None:
        """Pin the clock to an aware datetime; ``None`` restores real time."""
        self._frozen = value


clock = Clock()