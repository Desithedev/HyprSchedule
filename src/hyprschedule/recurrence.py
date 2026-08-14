"""Weekly recurrence: RRULE subset parsing and bounded occurrence expansion.

Supported subset (Phase 1):

    FREQ=WEEKLY;BYDAY=MO,WE,FR;UNTIL=YYYYMMDD

- ``FREQ=WEEKLY`` — required; any other FREQ is rejected.
- ``BYDAY`` — optional comma-separated weekday tokens MO..SU. When omitted the
  weekday of the first occurrence (DTSTART) is used.
- ``UNTIL`` — optional inclusive last date in the event's timezone, either
  ``YYYYMMDD`` or ``YYYYMMDDTHHMMSS`` (with optional ``Z``/``±HHMM`` offset,
  converted to the event timezone date).

Everything else (COUNT, INTERVAL, BYMONTH, WKST, ...) is rejected with a
clear :class:`InvalidRecurrence` error — never silently reinterpreted.

Expansion is always bounded by the requested range: an event without an end
date is safe to query because the loop only walks the weeks that can overlap
``[range_start, range_end)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from hyprschedule.errors import InvalidRecurrence

# Monday == 0, matching date.weekday().
WEEKDAYS: dict[str, int] = {
    "MO": 0,
    "TU": 1,
    "WE": 2,
    "TH": 3,
    "FR": 4,
    "SA": 5,
    "SU": 6,
}

_UNTIL_DATE_RE = re.compile(r"^\d{8}$")
_UNTIL_DATETIME_RE = re.compile(r"^\d{8}T\d{6}(Z|[+-]\d{4})?$")


@dataclass(frozen=True)
class WeeklyRule:
    """A parsed weekly recurrence rule."""

    byday: tuple[int, ...]
    until: date | None
    dtstart: date
    start_time: time
    duration: timedelta
    timezone: str


def parse_weekly_rule(
    rrule: str,
    *,
    start_at: datetime,
    end_at: datetime,
    timezone: str,
) -> WeeklyRule:
    """Parse and validate an RRULE string against the supported subset."""
    if not rrule or not rrule.strip():
        raise InvalidRecurrence("recurrence rule must not be empty")
    if start_at.tzinfo is None or end_at.tzinfo is None:
        raise InvalidRecurrence("recurring event start/end must be timezone-aware")
    if end_at <= start_at:
        raise InvalidRecurrence("recurring event end_at must be after start_at")

    tz = ZoneInfo(timezone)
    dtstart_local = start_at.astimezone(tz)

    properties: dict[str, str] = {}
    for part in rrule.split(";"):
        if not part:
            continue
        if "=" not in part:
            raise InvalidRecurrence(f"invalid RRULE property {part!r}")
        key, _, value = part.partition("=")
        properties[key.upper()] = value

    freq = properties.pop("FREQ", None)
    if freq != "WEEKLY":
        raise InvalidRecurrence(
            f"unsupported FREQ={freq!r}; only FREQ=WEEKLY is supported"
        )

    byday = _parse_byday(properties.pop("BYDAY", None), dtstart_local.date())
    until = _parse_until(properties.pop("UNTIL", None), dtstart_local, tz)

    unsupported = ", ".join(sorted(properties))
    if unsupported:
        raise InvalidRecurrence(
            f"unsupported RRULE properties: {unsupported}; "
            "supported subset is FREQ=WEEKLY, BYDAY, UNTIL"
        )

    return WeeklyRule(
        byday=byday,
        until=until,
        dtstart=dtstart_local.date(),
        start_time=dtstart_local.time(),
        duration=end_at - start_at,
        timezone=timezone,
    )


def _parse_byday(value: str | None, dtstart: date) -> tuple[int, ...]:
    if value is None:
        return (dtstart.weekday(),)
    tokens = [token.strip().upper() for token in value.split(",") if token.strip()]
    if not tokens:
        raise InvalidRecurrence("BYDAY must contain at least one weekday (MO..SU)")
    seen: list[int] = []
    for token in tokens:
        if token not in WEEKDAYS:
            raise InvalidRecurrence(
                f"invalid BYDAY weekday {token!r}; expected MO TU WE TH FR SA SU"
            )
        if WEEKDAYS[token] not in seen:
            seen.append(WEEKDAYS[token])
    if not seen:
        raise InvalidRecurrence("BYDAY must contain at least one weekday (MO..SU)")
    return tuple(sorted(seen))


def _parse_until(value: str | None, dtstart_local: datetime, tz: ZoneInfo) -> date | None:
    if value is None:
        return None
    value = value.strip().upper()
    if _UNTIL_DATE_RE.match(value):
        return datetime.strptime(value, "%Y%m%d").date()
    if _UNTIL_DATETIME_RE.match(value):
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
        if value.endswith("Z"):
            parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
        else:
            offset = value[-5:]
            if offset[0] in "+-":
                hours, minutes = int(offset[1:3]), int(offset[3:5])
                sign = 1 if offset[0] == "+" else -1
                parsed = parsed.replace(
                    tzinfo=timezone(timedelta(hours=sign * hours, minutes=sign * minutes))
                )
            else:
                parsed = parsed.replace(tzinfo=tz)
        return parsed.astimezone(tz).date()
    raise InvalidRecurrence(
        f"invalid UNTIL {value!r}; expected YYYYMMDD or YYYYMMDDTHHMMSS"
    )


def expand_weekly(
    rule: WeeklyRule,
    range_start: datetime,
    range_end: datetime,
) -> list[datetime]:
    """Return occurrence start datetimes overlapping ``[range_start, range_end)``.

    Results are in the event's timezone, sorted ascending. Iteration is
    bounded: only weeks between the series start and
    ``min(until, range_end + duration)`` are visited.
    """
    if range_start.tzinfo is None or range_end.tzinfo is None:
        raise InvalidRecurrence("range bounds must be timezone-aware")
    if range_end <= range_start:
        return []

    tz = ZoneInfo(rule.timezone)
    range_start_local = range_start.astimezone(tz)

    # The earliest occurrence that can still overlap the range starts at most
    # one duration before range_start; clamp to the series start date.
    earliest_possible = range_start_local - rule.duration
    week_start = earliest_possible.date() - timedelta(
        days=earliest_possible.date().weekday()
    )
    dtstart_week = rule.dtstart - timedelta(days=rule.dtstart.weekday())
    if week_start < dtstart_week:
        week_start = dtstart_week

    last_date = range_end.astimezone(tz).date()
    if rule.until is not None and rule.until < last_date:
        last_date = rule.until

    if week_start > last_date:
        return []

    occurrences: list[datetime] = []
    while week_start <= last_date:
        for weekday in rule.byday:
            day = week_start + timedelta(days=weekday)
            if day < rule.dtstart:
                continue
            if day > last_date:
                continue
            occurrence_start = datetime.combine(day, rule.start_time, tzinfo=tz)
            occurrence_end = occurrence_start + rule.duration
            if occurrence_start < range_end and occurrence_end > range_start:
                occurrences.append(occurrence_start)
        week_start += timedelta(days=7)

    occurrences.sort()
    return occurrences