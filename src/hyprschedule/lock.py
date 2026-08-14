"""``schedctl lock`` — hyprlock label payload (Phase 5).

Plain text for a hyprlock dynamic label. Everything — occurrence expansion,
recurrence exceptions, timezone conversion, status and privacy filtering,
countdown — is computed in Python; hyprlock only executes ``schedctl lock``
and renders the returned lines.

The output is deliberately compact (readable within ~2 seconds): the current
event, the next event today, an optional tomorrow count, and a one-line empty
state. No full daily timetable, no JSON, no ANSI escapes, no markup.

Privacy is the lockscreen's most important property. ``hidden`` events never
appear in any block and never contribute to the tomorrow count; ``private``
events are masked (generic title ``Có lịch cá nhân``, no location) unless
``[lockscreen] show_private = true``. Status semantics are shared with the
Phase 3 scheduler (``_is_remindable``): ``done`` and ``cancelled`` events are
never shown as active/upcoming.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from hyprschedule.config import Config
from hyprschedule.eww import format_duration
from hyprschedule.models import Event, Occurrence, Privacy
from hyprschedule.repository import EventRepository
from hyprschedule.scheduler import _is_remindable

PRIVATE_MASK = "Có lịch cá nhân"


def is_visible(occ: Occurrence) -> bool:
    """Lockscreen visibility: active status (shared scheduler semantics) and
    not ``hidden``. Private events pass here; masking happens at render time."""
    return _is_remindable(occ) and occ.privacy is not Privacy.HIDDEN


def _display_title(occ: Occurrence, config: Config) -> str:
    if occ.privacy is Privacy.PRIVATE and not config.lockscreen.show_private:
        return PRIVATE_MASK
    return occ.title


def _is_masked(occ: Occurrence, config: Config) -> bool:
    return occ.privacy is Privacy.PRIVATE and not config.lockscreen.show_private


def _find_current(
    repo: EventRepository, events_by_id: dict[int, Event], now: datetime
) -> tuple[Occurrence, Event] | None:
    """The visible occurrence active at *now* (half-open ``[start, end)``),
    latest start wins. The window reaches back by the longest stored duration
    so cross-midnight events running at 00:15 are found (same semantics as the
    Phase 4 widget)."""
    window_start = now - max(repo.max_duration(), timedelta(hours=1))
    candidates = [
        (occ, events_by_id[occ.event_id])
        for occ in repo.get_occurrences(window_start, now + timedelta(seconds=1))
        if occ.event_id in events_by_id
        and is_visible(occ)
        and occ.occurrence_start <= now < occ.occurrence_end
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda pair: (pair[0].occurrence_start, pair[0].event_id))
    return candidates[-1]


def _find_next(
    repo: EventRepository,
    events_by_id: dict[int, Event],
    now: datetime,
    day_end: datetime,
) -> tuple[Occurrence, Event] | None:
    """The first visible occurrence today with start >= now (deterministic:
    earliest start, then event id — the engine's own ordering)."""
    for occ in repo.get_occurrences(now, day_end):
        event = events_by_id.get(occ.event_id)
        if event is not None and is_visible(occ) and occ.occurrence_start >= now:
            return occ, event
    return None


def _tomorrow_count(repo: EventRepository, tomorrow_start: datetime) -> int:
    """Visible occurrences overlapping tomorrow. Hidden events never count."""
    return sum(
        1
        for occ in repo.get_occurrences(
            tomorrow_start, tomorrow_start + timedelta(days=1)
        )
        if is_visible(occ)
    )


def _current_lines(
    pair: tuple[Occurrence, Event], config: Config, now: datetime
) -> list[str]:
    occ, event = pair
    tz = ZoneInfo(event.timezone)
    start = occ.occurrence_start.astimezone(tz)
    end = occ.occurrence_end.astimezone(tz)
    lines = [_display_title(occ, config), f"{start:%H:%M} → {end:%H:%M}"]
    if occ.location and not _is_masked(occ, config):
        lines.append(occ.location)
    remaining = max(0, int((occ.occurrence_end - now).total_seconds()))
    lines.append(f"còn {format_duration(remaining)}")
    return lines


def _next_lines(
    pair: tuple[Occurrence, Event], config: Config, now: datetime
) -> list[str]:
    occ, event = pair
    tz = ZoneInfo(event.timezone)
    start = occ.occurrence_start.astimezone(tz)
    lines = [f"{start:%H:%M} · {_display_title(occ, config)}"]
    if occ.location and not _is_masked(occ, config):
        lines.append(occ.location)
    starts_in = max(0, int((occ.occurrence_start - now).total_seconds()))
    lines.append(f"sau {format_duration(starts_in)}")
    return lines


def build_lines(config: Config, repo: EventRepository, now: datetime) -> list[str]:
    """The plain-text lines for ``schedctl lock`` (deterministic for a fixed
    *now*). At most ``lockscreen.max_events`` event blocks are emitted
    (current first, then next)."""
    tz = ZoneInfo(config.timezone)
    now_local = now.astimezone(tz)
    today = now_local.date()
    day_start = datetime.combine(today, time(0), tzinfo=tz)
    tomorrow_start = day_start + timedelta(days=1)

    events_by_id = {event.id: event for event in repo.list_events()}
    current = _find_current(repo, events_by_id, now)
    next_pair = _find_next(repo, events_by_id, now, tomorrow_start)
    tomorrow = _tomorrow_count(repo, tomorrow_start)

    lines = [now_local.strftime("%H:%M")]
    blocks = 0
    if current is not None:
        lines += ["", "ĐANG DIỄN RA", *_current_lines(current, config, now)]
        blocks += 1
    if next_pair is not None and blocks < config.lockscreen.max_events:
        lines += ["", "TIẾP THEO", *_next_lines(next_pair, config, now)]
    if config.lockscreen.show_tomorrow_count and tomorrow:
        lines += ["", f"Ngày mai · {tomorrow} lịch"]
    if current is None and next_pair is None:
        lines += ["", "Không còn lịch hôm nay." if tomorrow else "Không có lịch sắp tới."]
    return lines