"""Command implementations for ``schedctl`` (Phase 2).

The CLI is a pure interface layer: every piece of event logic (validation,
recurrence, conflicts, persistence) is delegated to the Phase 1 backend
(``repository`` / ``recurrence`` / ``conflicts``). No ad-hoc SQL here.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from hyprschedule import editor, eww, formatter, lock, recurrence
from hyprschedule.clock import clock
from hyprschedule.conflicts import Conflict
from hyprschedule.config import Config, load_config
from hyprschedule.database import Database
from hyprschedule.errors import EventNotFound, HyprScheduleError
from hyprschedule.models import Event, Occurrence
from hyprschedule.paths import database_path
from hyprschedule.repository import EventRepository, validate_event
from hyprschedule.scheduler import signal_running_daemon

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_CONFLICT = 3
EXIT_NOT_FOUND = 4

NEXT_HORIZON_DAYS = 30
CONFLICT_HORIZON_DAYS = 30

RRULE_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


@dataclass
class Context:
    config: Config
    db: Database
    repo: EventRepository


def build_context() -> Context:
    """Load config and open the database. Raises ConfigError on bad config."""
    config = load_config()
    db = Database(database_path())
    db.initialize()
    return Context(config=config, db=db, repo=EventRepository(db))


# ------------------------------------------------------------------ helpers


def _tz(config: Config) -> ZoneInfo:
    return ZoneInfo(config.timezone)


def _local_today(config: Config) -> date:
    return clock.now().astimezone(_tz(config)).date()


def _day_start(tz: ZoneInfo, day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=tz)


def _print_json(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False))


def _render_occurrences(pairs: list[tuple[Occurrence, Event]]) -> str:
    lines: list[str] = []
    for occ, event in pairs:
        tz = ZoneInfo(event.timezone)
        start = occ.occurrence_start.astimezone(tz)
        end = occ.occurrence_end.astimezone(tz)
        lines.append(f"{start:%H:%M} - {end:%H:%M}  {occ.title}")
        if occ.location:
            lines.append(f"{'':15}{occ.location}")
    return "\n".join(lines)


def _occurrences_with_events(
    ctx: Context, start: datetime, end: datetime
) -> list[tuple[Occurrence, Event]]:
    events = {event.id: event for event in ctx.repo.list_events()}
    return [
        (occ, events[occ.event_id])
        for occ in ctx.repo.get_occurrences(start, end)
        if occ.event_id in events
    ]


def _end_datetime(start_dt: datetime, start_time: time, end_time: time, tz: ZoneInfo) -> datetime:
    """Cross-midnight semantics: an end wall-clock *earlier* than the start
    belongs to the following calendar day (documented in ARCHITECTURE.md).
    An end equal to the start is left on the same day so the backend rejects
    it as a zero-length event."""
    end_date = start_dt.date()
    if end_time < start_time:
        end_date += timedelta(days=1)
    return datetime.combine(end_date, end_time, tzinfo=tz)


def _build_rrule(weekdays: list[int], until: date | None) -> str:
    rrule = "FREQ=WEEKLY;BYDAY=" + ",".join(RRULE_DAYS[i] for i in weekdays)
    if until is not None:
        rrule += f";UNTIL={until:%Y%m%d}"
    return rrule


def _overlaps_window(occ: Occurrence, start: datetime, end: datetime) -> bool:
    return occ.occurrence_start < end and occ.occurrence_end > start


def _proposed_conflicts(
    ctx: Context, event: Event, *, exclude_event_id: int | None = None
) -> list[Conflict]:
    """Existing occurrences a not-yet-saved event would overlap.

    The proposed event is not yet in the database, so conflict detection
    compares each of its occurrences against existing ones. One-time events
    are checked over their own interval; recurring events over the first
    :data:`CONFLICT_HORIZON_DAYS` days (or until the series end, whichever
    is shorter) — a bounded, documented search.
    """
    if event.rrule is None:
        windows = [(event.start_at, event.end_at)]
    else:
        rule = recurrence.parse_weekly_rule(
            event.rrule, start_at=event.start_at, end_at=event.end_at, timezone=event.timezone
        )
        horizon = event.start_at + timedelta(days=CONFLICT_HORIZON_DAYS)
        duration = event.end_at - event.start_at
        windows = [
            (start, start + duration)
            for start in recurrence.expand_weekly(rule, event.start_at, horizon)
        ]

    earliest = min(window[0] for window in windows) - ctx.repo.max_duration()
    latest = max(window[1] for window in windows)
    existing = [
        occ
        for occ in ctx.repo.get_occurrences(earliest, latest)
        if occ.event_id != exclude_event_id
    ]

    groups: list[Conflict] = []
    seen: set[tuple[int, ...]] = set()
    for start, end in windows:
        partners = [occ for occ in existing if _overlaps_window(occ, start, end)]
        key = tuple(sorted(occ.event_id for occ in partners))
        if not key or key in seen:
            continue
        seen.add(key)
        partners.sort(key=lambda occ: (occ.occurrence_start, occ.event_id))
        groups.append(Conflict(tuple(partners)))
    return groups


def _print_conflicts(ctx: Context, groups: list[Conflict]) -> None:
    events = {event.id: event for event in ctx.repo.list_events()}
    print("Conflict detected:", file=sys.stderr)
    print(file=sys.stderr)
    for group in groups:
        for occ in group.occurrences:
            event = events.get(occ.event_id)
            tz_name = event.timezone if event else ctx.config.timezone
            tz = ZoneInfo(tz_name)
            start = occ.occurrence_start.astimezone(tz)
            end = occ.occurrence_end.astimezone(tz)
            print(f"{start:%H:%M} - {end:%H:%M}  {occ.title}", file=sys.stderr)
        print(file=sys.stderr)
    print("Use --force to save anyway.", file=sys.stderr)


def _event_from_args(args: object, config: Config) -> Event:
    """Build an :class:`Event` from ``add`` arguments. Raises UsageError."""
    try:
        tz = ZoneInfo(args.timezone or config.timezone)
    except Exception:
        raise UsageError(
            f"invalid timezone {args.timezone or config.timezone!r} (use an IANA name)"
        ) from None
    weekdays = args.weekday  # list[int] or None
    if args.repeat == "weekly":
        if args.date is not None:
            raise UsageError("--date is for one-time events; use --from with --repeat weekly")
        if args.from_date is None:
            raise UsageError("--from is required with --repeat weekly")
        if args.until is not None and args.until < args.from_date:
            raise UsageError("--until must not be before --from")
        if weekdays is None:
            weekdays = [args.from_date.weekday()]
        start_at = datetime.combine(args.from_date, args.start, tzinfo=tz)
        end_at = _end_datetime(start_at, args.start, args.end, tz)
        rrule = _build_rrule(weekdays, args.until)
    else:
        if args.from_date is not None:
            raise UsageError("--from is only valid with --repeat weekly")
        if args.date is None:
            raise UsageError("--date is required (or use --repeat weekly --from DATE)")
        start_at = datetime.combine(args.date, args.start, tzinfo=tz)
        end_at = _end_datetime(start_at, args.start, args.end, tz)
        rrule = None
    return Event(
        title=args.title,
        description=args.description or "",
        location=args.location or "",
        start_at=start_at,
        end_at=end_at,
        timezone=args.timezone or config.timezone,
        event_type=args.type,
        priority=args.priority,
        privacy=args.privacy,
        rrule=rrule,
    )


class UsageError(Exception):
    """Invalid user input; printed to stderr with exit code EXIT_USAGE."""


# ------------------------------------------------------------------ commands


def cmd_today(ctx: Context, args: object) -> int:
    tz = _tz(ctx.config)
    today = _local_today(ctx.config)
    start = _day_start(tz, today)
    end = _day_start(tz, today + timedelta(days=1))
    pairs = _occurrences_with_events(ctx, start, end)
    if args.json:
        _print_json(
            {
                "range": formatter.range_dict(start, end),
                "events": [formatter.occurrence_to_dict(o, e) for o, e in pairs],
            }
        )
    else:
        print(f"{formatter.weekday_name(today)}, {formatter.format_date(today)}")
        print()
        if not pairs:
            print("Không có lịch hôm nay.")
        else:
            print(_render_occurrences(pairs))
    return EXIT_OK


def cmd_tomorrow(ctx: Context, args: object) -> int:
    tz = _tz(ctx.config)
    today = _local_today(ctx.config)
    tomorrow = today + timedelta(days=1)
    start = _day_start(tz, tomorrow)
    end = _day_start(tz, tomorrow + timedelta(days=1))
    pairs = _occurrences_with_events(ctx, start, end)
    if args.json:
        _print_json(
            {
                "range": formatter.range_dict(start, end),
                "events": [formatter.occurrence_to_dict(o, e) for o, e in pairs],
            }
        )
    else:
        print(f"{formatter.weekday_name(tomorrow)}, {formatter.format_date(tomorrow)}")
        print()
        if not pairs:
            print("Không có lịch ngày mai.")
        else:
            print(_render_occurrences(pairs))
    return EXIT_OK


def cmd_week(ctx: Context, args: object) -> int:
    tz = _tz(ctx.config)
    today = _local_today(ctx.config)
    monday = today - timedelta(days=today.weekday())
    start = _day_start(tz, monday)
    end = _day_start(tz, monday + timedelta(days=7))
    pairs = _occurrences_with_events(ctx, start, end)
    if args.json:
        _print_json(
            {
                "range": formatter.range_dict(start, end),
                "events": [formatter.occurrence_to_dict(o, e) for o, e in pairs],
            }
        )
        return EXIT_OK
    by_day: dict[date, list[tuple[Occurrence, Event]]] = {}
    for occ, event in pairs:
        day = occ.occurrence_start.astimezone(ZoneInfo(event.timezone)).date()
        by_day.setdefault(day, []).append((occ, event))
    if not by_day:
        print("Không có lịch trong tuần này.")
        return EXIT_OK
    blocks: list[str] = []
    for day in sorted(by_day):
        header = f"{formatter.weekday_name(day)} {formatter.format_date_short(day)}"
        blocks.append(header + "\n\n" + _render_occurrences(by_day[day]))
    print("\n\n".join(blocks))
    return EXIT_OK


def cmd_next(ctx: Context, args: object) -> int:
    now = clock.now()
    horizon = now + timedelta(days=NEXT_HORIZON_DAYS)
    pairs = _occurrences_with_events(ctx, now, horizon)
    upcoming = [(o, e) for o, e in pairs if o.occurrence_start >= now]
    if args.json:
        if upcoming:
            _print_json({"event": formatter.occurrence_to_dict(*upcoming[0])})
        else:
            _print_json({"event": None})
        return EXIT_OK
    if not upcoming:
        print("Không có lịch sắp tới.")
        return EXIT_OK
    occ, event = upcoming[0]
    tz = ZoneInfo(event.timezone)
    start = occ.occurrence_start.astimezone(tz)
    end = occ.occurrence_end.astimezone(tz)
    print(f"{formatter.format_date(start.date())} {start:%H:%M} - {end:%H:%M}  {occ.title}")
    if occ.location:
        print(f"{'':24}{occ.location}")
    return EXIT_OK


def cmd_add(ctx: Context, args: object) -> int:
    try:
        event = _event_from_args(args, ctx.config)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        validate_event(event)
        if not args.force:
            conflicts = _proposed_conflicts(ctx, event)
            if conflicts:
                _print_conflicts(ctx, conflicts)
                return EXIT_CONFLICT
        saved = ctx.repo.create_event(event)
        if args.remind is not None:
            ctx.repo.replace_reminders(saved.id, args.remind)
    except HyprScheduleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    signal_running_daemon()
    print(f"Đã thêm lịch #{saved.id}: {saved.title}")
    return EXIT_OK


def cmd_edit(ctx: Context, args: object) -> int:
    event_id = args.id
    try:
        event = ctx.repo.get_event(event_id)
    except EventNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    try:
        return _edit_event(ctx, args, event)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


def _edit_event(ctx: Context, args: object, event: Event) -> int:
    event_id = event.id
    current_tz = ZoneInfo(event.timezone)
    local_start = event.start_at.astimezone(current_tz)
    local_end = event.end_at.astimezone(current_tz)
    target_tz_name = args.timezone or event.timezone
    target_tz = ZoneInfo(target_tz_name)

    kwargs: dict[str, object] = {}
    changed_times = args.date is not None or args.start is not None or args.end is not None
    if changed_times:
        day = args.date or local_start.date()
        start_time = args.start if args.start is not None else local_start.time()
        new_start = datetime.combine(day, start_time, tzinfo=target_tz)
        if args.end is not None:
            new_end = _end_datetime(new_start, start_time, args.end, target_tz)
        else:
            new_end = new_start + (event.end_at - event.start_at)
        kwargs["start_at"] = new_start
        kwargs["end_at"] = new_end
    elif args.timezone is not None:
        kwargs["start_at"] = datetime.combine(local_start.date(), local_start.time(), tzinfo=target_tz)
        kwargs["end_at"] = datetime.combine(local_end.date(), local_end.time(), tzinfo=target_tz)

    if args.repeat is not None:
        if args.repeat == "weekly":
            base = args.weekday
            if base is None and event.rrule is not None:
                rule = recurrence.parse_weekly_rule(
                    event.rrule,
                    start_at=event.start_at,
                    end_at=event.end_at,
                    timezone=event.timezone,
                )
                base = list(rule.byday)
            if base is None:
                base = [local_start.date().weekday()]
            kwargs["rrule"] = _build_rrule(base, args.until)
        else:
            kwargs["clear_rrule"] = True
    elif args.weekday is not None or args.until is not None:
        raise UsageError("--weekday/--until require --repeat weekly")

    for flag, key in (
        ("title", "title"),
        ("description", "description"),
        ("location", "location"),
        ("type", "event_type"),
        ("priority", "priority"),
        ("privacy", "privacy"),
        ("timezone", "timezone"),
    ):
        value = getattr(args, flag)
        if value is not None:
            kwargs[key] = value

    proposed = Event(
        title=kwargs.get("title", event.title),
        description=kwargs.get("description", event.description),
        location=kwargs.get("location", event.location),
        start_at=kwargs.get("start_at", event.start_at),
        end_at=kwargs.get("end_at", event.end_at),
        timezone=kwargs.get("timezone", event.timezone),
        event_type=kwargs.get("event_type", event.event_type),
        priority=kwargs.get("priority", event.priority),
        privacy=kwargs.get("privacy", event.privacy),
        rrule=(
            None
            if kwargs.get("clear_rrule")
            else kwargs.get("rrule", event.rrule)
        ),
    )
    try:
        validate_event(proposed)
        if not args.force:
            conflicts = _proposed_conflicts(ctx, proposed, exclude_event_id=event_id)
            if conflicts:
                _print_conflicts(ctx, conflicts)
                return EXIT_CONFLICT
        ctx.repo.update_event(event_id, **kwargs)
        if args.remind is not None:
            ctx.repo.replace_reminders(event_id, args.remind)
    except HyprScheduleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    signal_running_daemon()
    print(f"Đã cập nhật lịch #{event_id}.")
    return EXIT_OK


def cmd_delete(ctx: Context, args: object) -> int:
    try:
        ctx.repo.delete_event(args.id)
    except EventNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    signal_running_daemon()
    print(f"Đã xóa lịch #{args.id}.")
    return EXIT_OK


def cmd_show(ctx: Context, args: object) -> int:
    try:
        event = ctx.repo.get_event(args.id)
    except EventNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    reminders = [r.minutes_before for r in ctx.repo.list_reminders(args.id)]
    if args.json:
        _print_json(formatter.event_to_dict(event, reminders))
        return EXIT_OK
    tz = ZoneInfo(event.timezone)
    start = event.start_at.astimezone(tz)
    end = event.end_at.astimezone(tz)
    reminder_text = ", ".join(f"{m}m" for m in reminders) if reminders else "không có"
    print(f"ID:          {event.id}")
    print(f"Title:       {event.title}")
    print(f"Type:        {event.event_type.value}")
    print(f"Start:       {formatter.format_date(start.date())} {start:%H:%M}")
    print(f"End:         {formatter.format_date(end.date())} {end:%H:%M}")
    print(f"Timezone:    {event.timezone}")
    print(f"Location:    {event.location or '-'}")
    print(f"Description: {event.description or '-'}")
    print(f"Priority:    {event.priority.value}")
    print(f"Privacy:     {event.privacy.value}")
    print(f"Status:      {event.status.value}")
    print(f"Recurrence:  {formatter.recurrence_text(event)}")
    print(f"Reminders:   {reminder_text}")
    return EXIT_OK


def cmd_eww(ctx: Context, args: object) -> int:
    """Eww read-only widget payload (Phase 4). JSON on stdout only."""
    try:
        payload = eww.build_payload(ctx.config, ctx.repo, clock.now())
    except HyprScheduleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    _print_json(payload)
    return EXIT_OK


def cmd_editor_data(ctx: Context, args: object) -> int:
    """Read-only Add/Edit form payload (Phase 6). JSON on stdout only."""
    try:
        if args.id is None:
            payload = editor.build_form_defaults(ctx.config, clock.now())
        else:
            event = ctx.repo.get_event(args.id)
            reminders = [r.minutes_before for r in ctx.repo.list_reminders(args.id)]
            payload = editor.event_to_editor_dict(event, reminders)
    except EventNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    except HyprScheduleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    _print_json(payload)
    return EXIT_OK


def cmd_editor_save(ctx: Context, args: object) -> int:
    """Mutating entry point for the Eww editor (Phase 6).

    Reads exactly one JSON document from stdin and delegates to
    ``cmd_add``/``cmd_edit`` via :func:`editor.add_args` /
    :func:`editor.edit_args` — so validation, conflict detection, ``force``
    semantics and the daemon reload signal are identical to the CLI paths.
    User values never reach a shell.
    """
    raw = sys.stdin.read()
    if not raw.strip():
        print("error: thiếu dữ liệu form (JSON qua stdin)", file=sys.stderr)
        return EXIT_USAGE
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"error: dữ liệu form không phải JSON: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if not isinstance(doc, dict):
        print("error: dữ liệu form phải là một object JSON", file=sys.stderr)
        return EXIT_USAGE
    mode = doc.get("mode")
    try:
        if mode == "add":
            return cmd_add(ctx, editor.add_args(doc, ctx.config))
        if mode == "edit":
            return cmd_edit(ctx, editor.edit_args(doc, ctx.config))
        raise editor.InvalidForm('"mode" phải là "add" hoặc "edit"')
    except editor.InvalidForm as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


def cmd_lock(ctx: Context, args: object) -> int:
    """Hyprlock label payload (Phase 5). Plain text on stdout only.

    Safe to run repeatedly by hyprlock. Any failure produces a concise
    fallback on stdout and diagnostics on stderr — a lockscreen must never
    show a traceback.
    """
    try:
        lines = lock.build_lines(ctx.config, ctx.repo, clock.now())
    except Exception as exc:
        print("Không thể tải lịch")
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print("\n".join(lines))
    return EXIT_OK


def cmd_search(ctx: Context, args: object) -> int:
    query = args.query
    events = ctx.repo.search_events(query)
    if args.json:
        _print_json(
            {
                "query": query,
                "events": [formatter.event_to_dict(e) for e in events],
            }
        )
        return EXIT_OK
    if not events:
        print(f"Không có kết quả cho \"{query}\".")
        return EXIT_OK
    print(f"{len(events)} kết quả cho \"{query}\":")
    print()
    for event in events:
        tz = ZoneInfo(event.timezone)
        start = event.start_at.astimezone(tz)
        end = event.end_at.astimezone(tz)
        print(f"#{event.id}  {formatter.format_date(start.date())} {start:%H:%M} - {end:%H:%M}  {event.title}")
    return EXIT_OK