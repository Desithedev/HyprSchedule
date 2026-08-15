"""Phase 4 static validation of the Eww widget configuration.

``eww`` is not a runtime dependency and is not installed in CI, so we cannot
invoke it here. Instead we verify, statically:

1. The project's Eww files exist (``eww/eww.yuck``, ``eww/eww.scss``).
2. Every ``schedule.*`` JSON path referenced by the yuck resolves against the
   real ``schedctl eww`` payload contract (a rich payload is built with a
   frozen clock so all branches are populated).
3. Loop-variable accesses (``event.*``) resolve against a today-list item.
4. Every ``type-*`` / ``priority-*`` class the yuck can emit is styled in SCSS.
5. Parentheses are balanced.
"""

import re
from pathlib import Path

import pytest

from hyprschedule.config import load_config
from hyprschedule.eww import build_payload
from hyprschedule.models import Event, EventType, Priority
from hyprschedule.repository import EventRepository

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EWW_DIR = PROJECT_ROOT / "eww"
YUCK = EWW_DIR / "eww.yuck"
SCSS = EWW_DIR / "eww.scss"


def _now(iso: str):
    from datetime import datetime

    return datetime.fromisoformat(iso)


def _seed_payload(repo: EventRepository) -> dict:
    """A payload with current + next + events + free time + tomorrow, so every
    yuck branch resolves. Seeded into the caller's isolated repository."""
    def add(title, start, end, **kw):
        defaults = dict(
            timezone="Asia/Ho_Chi_Minh",
            event_type=EventType.OTHER,
            priority=Priority.NORMAL,
        )
        defaults.update(kw)
        repo.create_event(
            Event(title=title, start_at=_now(start), end_at=_now(end), **defaults)
        )

    add("Dạy 12A1", "2026-08-13T19:30:00+07:00", "2026-08-13T20:15:00+07:00",
        location="A203", event_type=EventType.CLASS, priority=Priority.HIGH)
    add("Họp tổ", "2026-08-13T21:00:00+07:00", "2026-08-13T22:00:00+07:00",
        location="Phòng họp", event_type=EventType.MEETING, priority=Priority.HIGH)
    add("Chấm bài", "2026-08-13T22:15:00+07:00", "2026-08-13T23:00:00+07:00")
    add("Ngày mai 1", "2026-08-14T08:00:00+07:00", "2026-08-14T08:45:00+07:00")
    return build_payload(load_config(), repo, _now("2026-08-13T19:45:00+07:00"))


def _access(data: dict, segments: list[str]) -> object:
    """Resolve dotted path segments; ``None`` short-circuits (safe access)."""
    current = data
    for seg in segments:
        if current is None:
            return None
        current = current[seg]
    return current


def _yuck_references() -> dict[str, list[str]]:
    """Extract ``schedule.<a>?..`` and ``event.<b>`` dotted accesses from yuck."""
    src = YUCK.read_text(encoding="utf-8")
    schedule = re.findall(r"schedule((?:\??\.[A-Za-z_][A-Za-z0-9_]*)+)", src)
    event = re.findall(r"\bevent\.([A-Za-z_][A-Za-z0-9_]*)", src)
    schedule_paths = [
        [seg for seg in re.split(r"[.?]", path) if seg]
        for path in schedule
    ]
    return {"schedule": schedule_paths, "event": event}


def test_eww_files_exist():
    assert YUCK.is_file()
    assert SCSS.is_file()
    assert "(defwindow hyprschedule" in YUCK.read_text(encoding="utf-8")


def test_eww_yuck_parens_balanced():
    depth = 0
    for i, line in enumerate(YUCK.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.split(";;", 1)[0]
        for ch in stripped:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        assert depth >= 0, f"unbalanced at line {i}"
    assert depth == 0


def test_yuck_schedule_paths_resolve_against_payload(cli_env, frozen_clock):
    frozen_clock(_now("2026-08-13T19:45:00+07:00"))
    payload = _seed_payload(cli_env.repo)
    refs = _yuck_references()
    assert refs["schedule"], "no schedule.* references found in yuck"
    for segments in refs["schedule"]:
        value = _access(payload, segments)
        assert value is not None, f"yuck path schedule.{'.'.join(segments)} not in payload"


def test_yuck_event_loop_fields_resolve_against_item(cli_env, frozen_clock):
    frozen_clock(_now("2026-08-13T19:45:00+07:00"))
    payload = _seed_payload(cli_env.repo)
    assert payload["events"], "seed payload must contain today events"
    item_keys = set(payload["events"][0].keys())
    for field in _yuck_references()["event"]:
        assert field in item_keys, f"event.{field} not in today-list item: {item_keys}"


def test_scss_styles_all_emitted_classes(cli_env, frozen_clock):
    frozen_clock(_now("2026-08-13T19:45:00+07:00"))
    payload = _seed_payload(cli_env.repo)
    scss = SCSS.read_text(encoding="utf-8")

    for item in payload["events"] + ([payload["current"]] if payload["current"] else []):
        assert f".type-{item['event_type']}" in scss, f"missing .type-{item['event_type']}"
        assert f".priority-{item['priority']}" in scss, (
            f"missing .priority-{item['priority']}"
        )

    for cls in ("header", "section", "section-title", "event-block", "event-title",
                "event-time", "event-meta", "event-remaining", "event-countdown",
                "progress-row", "progress-label", "today-list", "event-row",
                "event-row-time", "event-row-title", "event-row-end", "empty-hint",
                "free-time", "free-time-label", "free-time-range", "free-time-duration",
                "tomorrow", "tomorrow-label", "tomorrow-count",
                "error-state", "error-title", "error-hint", "hyprschedule"):
        assert f".{cls}" in scss, f"missing .{cls} in eww.scss"


def test_scss_defines_type_colors_for_all_event_types():
    from hyprschedule.models import EventType

    scss = SCSS.read_text(encoding="utf-8")
    for kind in EventType:
        assert f".type-{kind.value}" in scss, f"missing .type-{kind.value}"
    assert ".event-block.type-class" in scss
    assert ".event-row.type-class" in scss


def test_yuck_uses_documented_eww_features():
    src = YUCK.read_text(encoding="utf-8")
    assert "(defpoll schedule :interval" in src
    assert '`schedctl eww`' in src
    assert "(defwindow hyprschedule" in src
    assert ":stacking" in src
    assert ":focusable" in src
    assert ":visible {" in src
    assert "?: " in src
    assert "?." in src  # safe access somewhere
    assert "for event in {schedule.events ?: []}" in src
    assert "(progress :value" in src