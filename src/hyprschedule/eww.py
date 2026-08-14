"""Eww read-only widget payload (Phase 4).

``schedctl eww`` emits exactly one JSON document describing the current
moment of the user's schedule: current event (with progress), next event
(with countdown), today's remaining occurrences, the next meaningful free
period and tomorrow's occurrence count. All business computation (occurrence
expansion, exceptions, timezone handling, free-time rules, progress,
countdown) happens here — Eww only renders.

The payload contract is stable and documented in ARCHITECTURE.md.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from hyprschedule import formatter
from hyprschedule.config import Config
from hyprschedule.models import Event, Occurrence
from hyprschedule.repository import EventRepository

# Same semantic horizon as ``schedctl next`` (commands.py).
NEXT_HORIZON_DAYS = 30

PROGRESS_BAR_CELLS = 10


def format_duration(seconds: int) -> str:
    """Human-readable duration, deterministic Vietnamese.

    - under a minute: ``N giây``
    - under an hour: ``N phút``
    - otherwise: ``H giờ`` or ``H giờ M phút``

    Values are floored so output never drifts between refreshes.
    """
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} giây"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} phút"
    hours, rest = divmod(minutes, 60)
    if rest:
        return f"{hours} giờ {rest} phút"
    return f"{hours} giờ"


def progress_bar_text(progress: float) -> str:
    """10-cell block bar for the fixed-width Eww progress row."""
    raw = progress * PROGRESS_BAR_CELLS
    filled = max(0, min(PROGRESS_BAR_CELLS, int(raw + 0.5)))
    return "█" * filled + "░" * (PROGRESS_BAR_CELLS - filled)


def build_payload(config: Config, repo: EventRepository, now: datetime) -> dict[str, Any]:
    """Build the ``schedctl eww`` JSON document for instant *now*.

    Queries are bounded to the current day, the next-event horizon and a
    max-duration window around *now*; the repository pre-filters one-time
    events in SQL (see ``repository._events_overlapping``).
    """
    tz = ZoneInfo(config.timezone)
    now_local = now.astimezone(tz)
    today = now_local.date()

    day_start = _day_start(tz, today, config.schedule.day_start)
    next_day_start = day_start + timedelta(days=1)
    day_end = _day_start(tz, today, config.schedule.day_end)

    events_by_id = {event.id: event for event in repo.list_events()}

    current_pair = _find_current(repo, events_by_id, now)
    today_occurrences = repo.get_occurrences(day_start, next_day_start)
    next_pair = _find_next(repo, events_by_id, now)
    tomorrow_count = len(
        repo.get_occurrences(next_day_start, next_day_start + timedelta(days=1))
    )

    return {
        "generated_at": now_local.isoformat(),
        "timezone": config.timezone,
        "date": {
            "iso": today.isoformat(),
            "weekday": formatter.weekday_name(today),
            "display": f"{formatter.weekday_name(today)} · {formatter.format_date_short(today)}",
        },
        "now": {"time": now_local.strftime("%H:%M")},
        "current": _current_item(*current_pair, now) if current_pair else None,
        "next": _next_item(*next_pair, now) if next_pair else None,
        "events": _today_list(
            today_occurrences,
            events_by_id,
            now,
            config.widget.max_events,
            current_pair,
        ),
        "free_time": _free_time(
            config,
            tz,
            now,
            today_occurrences,
            day_start,
            day_end,
        ),
        "tomorrow": {"count": tomorrow_count, "display": f"{tomorrow_count} lịch"},
        "widget": {
            "show_free_time": config.widget.show_free_time,
            "show_tomorrow_count": config.widget.show_tomorrow_count,
        },
    }


# ------------------------------------------------------------------ helpers


def _day_start(tz: ZoneInfo, day: date, wall_clock: str) -> datetime:
    parsed = datetime.strptime(wall_clock, "%H:%M").time()
    return datetime.combine(day, parsed, tzinfo=tz)


def _find_current(
    repo: EventRepository, events_by_id: dict[int, Event], now: datetime
) -> tuple[Occurrence, Event] | None:
    """The occurrence active at *now* (start <= now < end), latest start wins.

    The window reaches back by the longest stored duration so events that
    started before it (including cross-midnight events running at 00:15) are
    found; the ``+1s`` endpoint catches an event starting exactly at *now*.
    """
    window_start = now - max(repo.max_duration(), timedelta(hours=1))
    candidates: list[tuple[Occurrence, Event]] = []
    for occ in repo.get_occurrences(window_start, now + timedelta(seconds=1)):
        event = events_by_id.get(occ.event_id)
        if event is not None and occ.occurrence_start <= now < occ.occurrence_end:
            candidates.append((occ, event))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: (pair[0].occurrence_start, pair[0].event_id))
    return candidates[-1]


def _find_next(
    repo: EventRepository, events_by_id: dict[int, Event], now: datetime
) -> tuple[Occurrence, Event] | None:
    """Nearest occurrence with start >= now within the documented horizon."""
    for occ in repo.get_occurrences(now, now + timedelta(days=NEXT_HORIZON_DAYS)):
        event = events_by_id.get(occ.event_id)
        if event is not None and occ.occurrence_start >= now:
            return occ, event
    return None


def _today_list(
    today_occurrences: list[Occurrence],
    events_by_id: dict[int, Event],
    now: datetime,
    max_events: int,
    current_pair: tuple[Occurrence, Event] | None,
) -> list[dict[str, Any]]:
    """Today's relevant occurrences, deterministic chronological order.

    Past occurrences are omitted (the widget is upcoming-focused); the
    current occurrence has its own section and is not repeated here. States
    are the documented ``next`` / ``future`` (the vocabulary also defines
    ``past`` and ``current`` for other consumers).
    """
    current_key = (
        (current_pair[0].event_id, current_pair[0].occurrence_start)
        if current_pair
        else None
    )
    items: list[dict[str, Any]] = []
    next_marked = False
    for occ in today_occurrences:
        if occ.occurrence_end <= now:
            continue
        key = (occ.event_id, occ.occurrence_start)
        if key == current_key:
            continue
        event = events_by_id.get(occ.event_id)
        if event is None:
            continue
        state = "next" if not next_marked else "future"
        next_marked = True
        items.append({**_base_item(occ, event), "state": state})
        if len(items) >= max_events:
            break
    return items


def _base_item(occ: Occurrence, event: Event) -> dict[str, Any]:
    tz = ZoneInfo(event.timezone)
    start = occ.occurrence_start.astimezone(tz)
    end = occ.occurrence_end.astimezone(tz)
    return {
        "event_id": occ.event_id,
        "title": occ.title,
        "location": occ.location,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "start_display": start.strftime("%H:%M"),
        "end_display": end.strftime("%H:%M"),
        "event_type": occ.event_type.value,
        "priority": occ.priority.value,
    }


def _current_item(occ: Occurrence, event: Event, now: datetime) -> dict[str, Any]:
    item = _base_item(occ, event)
    total = (occ.occurrence_end - occ.occurrence_start).total_seconds()
    progress = 0.0
    if total > 0:
        progress = max(0.0, min(1.0, (now - occ.occurrence_start).total_seconds() / total))
    remaining = max(0, int((occ.occurrence_end - now).total_seconds()))
    item["remaining_seconds"] = remaining
    item["remaining_display"] = format_duration(remaining)
    item["progress"] = round(progress, 3)
    item["progress_pct"] = int(round(progress * 100))
    item["progress_bar"] = progress_bar_text(progress)
    return item


def _next_item(occ: Occurrence, event: Event, now: datetime) -> dict[str, Any]:
    item = _base_item(occ, event)
    starts_in = max(0, int((occ.occurrence_start - now).total_seconds()))
    item["starts_in_seconds"] = starts_in
    item["starts_in_display"] = format_duration(starts_in)
    return item


def _free_time(
    config: Config,
    tz: ZoneInfo,
    now: datetime,
    today_occurrences: list[Occurrence],
    day_start: datetime,
    day_end: datetime,
) -> dict[str, Any] | None:
    """The next free period of at least ``min_free_minutes`` within today's
    schedule window ``[day_start, day_end)``, starting at or after *now*.

    A free period is the gap between the end of one occupied interval and the
    start of the next (or the day end). If we are currently free the gap
    starts at *now*. No productivity heuristics: exactly one rule —
    ``schedule.min_free_minutes`` (config, default 15). All timestamps are
    expressed in the configured schedule timezone.
    """
    minimum = timedelta(minutes=config.schedule.min_free_minutes)
    now_local = now.astimezone(tz)
    if now_local >= day_end:
        return None

    intervals: list[tuple[datetime, datetime]] = []
    for occ in today_occurrences:
        start = max(occ.occurrence_start.astimezone(tz), day_start)
        end = min(occ.occurrence_end.astimezone(tz), day_end)
        if end > start:
            intervals.append((start, end))
    intervals.sort()

    cursor = max(now_local, day_start)
    for start, end in intervals:
        if start > cursor and start - cursor >= minimum:
            return _free_item(cursor, start)
        if end > cursor:
            cursor = end
    if day_end > cursor and day_end - cursor >= minimum:
        return _free_item(cursor, day_end)
    return None


def _free_item(start: datetime, end: datetime) -> dict[str, Any]:
    minutes = int((end - start).total_seconds() // 60)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "start_display": start.strftime("%H:%M"),
        "end_display": end.strftime("%H:%M"),
        "duration_minutes": minutes,
        "duration_display": format_duration(minutes * 60),
    }