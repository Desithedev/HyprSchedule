"""Command line interface for ``schedctl``.

argparse-based (Phase 0 choice, kept). Phase 2 adds the full command set:
today, tomorrow, week, next, add, edit, delete, show, search — all thin
wrappers over the Phase 1 event engine (see ``commands.py``).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from enum import Enum
from typing import Callable, Sequence

from hyprschedule import __version__
from hyprschedule.commands import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    Context,
    build_context,
    cmd_add,
    cmd_delete,
    cmd_edit,
    cmd_editor_data,
    cmd_editor_save,
    cmd_eww,
    cmd_lock,
    cmd_next,
    cmd_search,
    cmd_show,
    cmd_today,
    cmd_tomorrow,
    cmd_week,
)
from hyprschedule.config import ConfigError
from hyprschedule.doctor import exit_code as doctor_exit_code
from hyprschedule.doctor import format_report as doctor_format_report
from hyprschedule.doctor import run_checks as doctor_run_checks
from hyprschedule.logging import setup_logging
from hyprschedule.models import EventType, Priority, Privacy

WEEKDAY_ALIASES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _date_arg(value: str) -> datetime.date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}, expected YYYY-MM-DD"
        ) from None


def _time_arg(value: str) -> datetime.time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid time {value!r}, expected HH:MM"
        ) from None


def _remind_arg(value: str) -> list[int]:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    try:
        offsets = [int(part) for part in parts]
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid reminder list {value!r}, expected comma-separated minutes like 15,5,0"
        ) from None
    if any(offset < 0 for offset in offsets):
        raise argparse.ArgumentTypeError("reminder minutes must be >= 0")
    return offsets


def _weekday_arg(value: str) -> list[int]:
    tokens = [token.strip().lower() for token in value.split(",") if token.strip()]
    if not tokens:
        raise argparse.ArgumentTypeError("--weekday requires at least one day (mon..sun)")
    result: list[int] = []
    for token in tokens:
        if token not in WEEKDAY_ALIASES:
            raise argparse.ArgumentTypeError(
                f"invalid weekday {token!r}, expected mon tue wed thu fri sat sun"
            )
        if WEEKDAY_ALIASES[token] not in result:
            result.append(WEEKDAY_ALIASES[token])
    return sorted(result)


def _enum_arg(enum_cls: type[Enum], name: str) -> Callable[[str], Enum]:
    def parse(value: str) -> Enum:
        try:
            return enum_cls(value)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"invalid {name} {value!r}, expected one of: "
                + ", ".join(member.value for member in enum_cls)
            ) from None

    return parse


def _add_id_argument(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("id", type=int, metavar="ID", help="event id")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schedctl",
        description="HyprSchedule - local-first personal schedule system.",
    )
    parser.add_argument("--version", action="version", version=f"schedctl {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    doctor = subparsers.add_parser("doctor", help="run environment diagnostics")
    doctor.set_defaults(func=_cmd_doctor)

    for name, handler, help_text in (
        ("today", cmd_today, "show today's schedule"),
        ("tomorrow", cmd_tomorrow, "show tomorrow's schedule"),
        ("week", cmd_week, "show this week's schedule (Monday-Sunday)"),
        ("next", cmd_next, "show the next upcoming event"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--json", action="store_true", help="machine-readable output")
        sub.set_defaults(func=handler)

    add = subparsers.add_parser("add", help="add an event")
    add.add_argument("--title", required=True, help="event title")
    add.add_argument("--description", default="", help="event description")
    add.add_argument("--location", default="", help="event location")
    add.add_argument(
        "--type", type=_enum_arg(EventType, "type"), default=EventType.OTHER,
        help="event type: class|meeting|work|personal|deadline|task|other",
    )
    add.add_argument(
        "--priority", type=_enum_arg(Priority, "priority"), default=Priority.NORMAL,
        help="priority: low|normal|high|critical",
    )
    add.add_argument(
        "--privacy", type=_enum_arg(Privacy, "privacy"), default=Privacy.PUBLIC,
        help="privacy: public|private|hidden",
    )
    add.add_argument("--timezone", help="IANA timezone (default: from config)")
    add.add_argument("--date", type=_date_arg, help="event date YYYY-MM-DD (one-time events)")
    add.add_argument("--start", type=_time_arg, required=True, help="start time HH:MM")
    add.add_argument("--end", type=_time_arg, required=True, help="end time HH:MM")
    add.add_argument(
        "--repeat", choices=["weekly", "none"], default="none",
        help="recurrence: weekly (default: none)",
    )
    add.add_argument("--weekday", type=_weekday_arg, help="weekday(s) mon,tue,wed,...")
    add.add_argument("--from", dest="from_date", type=_date_arg, metavar="DATE",
                     help="recurring series start date YYYY-MM-DD")
    add.add_argument("--until", type=_date_arg, help="recurring series end date YYYY-MM-DD")
    add.add_argument("--remind", type=_remind_arg, metavar="MINUTES",
                     help="comma-separated reminder offsets, e.g. 15,5,0")
    add.add_argument("--force", action="store_true", help="save even if conflicts exist")
    add.set_defaults(func=cmd_add)

    edit = subparsers.add_parser("edit", help="edit an event")
    _add_id_argument(edit)
    edit.add_argument("--title")
    edit.add_argument("--description")
    edit.add_argument("--location")
    edit.add_argument("--type", type=_enum_arg(EventType, "type"))
    edit.add_argument("--priority", type=_enum_arg(Priority, "priority"))
    edit.add_argument("--privacy", type=_enum_arg(Privacy, "privacy"))
    edit.add_argument("--timezone")
    edit.add_argument("--date", type=_date_arg)
    edit.add_argument("--start", type=_time_arg)
    edit.add_argument("--end", type=_time_arg)
    edit.add_argument("--repeat", choices=["weekly", "none"])
    edit.add_argument("--weekday", type=_weekday_arg)
    edit.add_argument("--until", type=_date_arg)
    edit.add_argument("--remind", type=_remind_arg, metavar="MINUTES")
    edit.add_argument("--force", action="store_true")
    edit.set_defaults(func=cmd_edit)

    delete = subparsers.add_parser("delete", help="delete an event")
    _add_id_argument(delete)
    delete.set_defaults(func=cmd_delete)

    show = subparsers.add_parser("show", help="show event details")
    _add_id_argument(show)
    show.add_argument("--json", action="store_true", help="machine-readable output")
    show.set_defaults(func=cmd_show)

    search = subparsers.add_parser("search", help="search events by title/description/location")
    search.add_argument("query", metavar="QUERY", help="search text")
    search.add_argument("--json", action="store_true", help="machine-readable output")
    search.set_defaults(func=cmd_search)

    eww = subparsers.add_parser("eww", help="Eww read-only widget JSON payload")
    eww.set_defaults(func=cmd_eww)

    lock = subparsers.add_parser("lock", help="hyprlock label payload (plain text)")
    lock.set_defaults(func=cmd_lock)

    editor_data = subparsers.add_parser(
        "editor-data", help="Eww editor form payload (read-only JSON)"
    )
    editor_data.add_argument(
        "id", nargs="?", type=int, metavar="ID",
        help="event id to preload (omit for a fresh add form)",
    )
    editor_data.set_defaults(func=cmd_editor_data)

    editor_save = subparsers.add_parser(
        "editor-save", help="save an editor form from JSON on stdin (mutating)"
    )
    editor_save.set_defaults(func=cmd_editor_save)

    return parser


def _cmd_doctor(ctx: Context, args: argparse.Namespace) -> int:
    results = doctor_run_checks(ctx.config)
    print(doctor_format_report(results))
    return doctor_exit_code(results)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(verbose=args.verbose)

    if not hasattr(args, "func"):
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    try:
        ctx = build_context()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if args.command == "lock":
            print("Không thể tải lịch")
        return EXIT_ERROR
    return int(args.func(ctx, args))


if __name__ == "__main__":
    sys.exit(main())