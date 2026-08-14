"""Conflict detection for events and occurrences.

Overlap semantics are half-open intervals: two occurrences conflict iff

    a.start < b.end and b.start < a.end

so adjacent events (``07:00-07:45`` and ``07:45-08:30``) never conflict.

A :class:`Conflict` is a group of occurrences that overlap transitively
(each member overlaps at least one other member of the group); this gives
the future UI clean clusters instead of pairwise noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from hyprschedule.errors import InvalidEvent
from hyprschedule.models import Occurrence
from hyprschedule.timeutil import ensure_aware

if TYPE_CHECKING:
    from hyprschedule.repository import EventRepository


@dataclass(frozen=True)
class Conflict:
    """A set of mutually (transitively) overlapping occurrences."""

    occurrences: tuple[Occurrence, ...]

    def __len__(self) -> int:
        return len(self.occurrences)


def _overlaps(a: Occurrence, b: Occurrence) -> bool:
    return a.occurrence_start < b.occurrence_end and b.occurrence_start < a.occurrence_end


def find_conflicts(
    repo: "EventRepository",
    start: datetime,
    end: datetime,
    *,
    exclude_event_id: int | None = None,
) -> list[Conflict]:
    """Return conflict groups overlapping ``[start, end)``.

    Occurrences of ``exclude_event_id`` (e.g. the event being edited) are
    ignored, so updating an event never reports it as conflicting with
    itself. The search range is widened by the longest stored event duration
    so that events already in progress before ``start`` are still found.
    """
    ensure_aware(start, "start")
    ensure_aware(end, "end")
    if end <= start:
        raise InvalidEvent(f"conflict window end {end.isoformat()} must be after start {start.isoformat()}")

    occurrences = [
        occ
        for occ in repo.get_occurrences(start - repo.max_duration(), end)
        if occ.occurrence_end > start and occ.event_id != exclude_event_id
    ]
    occurrences.sort(key=lambda occ: (occ.occurrence_start, occ.event_id))

    groups: list[Conflict] = []
    if not occurrences:
        return groups

    current = [occurrences[0]]
    group_end = occurrences[0].occurrence_end
    for occ in occurrences[1:]:
        if occ.occurrence_start < group_end:
            current.append(occ)
            if occ.occurrence_end > group_end:
                group_end = occ.occurrence_end
        else:
            if len(current) >= 2:
                groups.append(Conflict(tuple(current)))
            current = [occ]
            group_end = occ.occurrence_end
    if len(current) >= 2:
        groups.append(Conflict(tuple(current)))

    return groups