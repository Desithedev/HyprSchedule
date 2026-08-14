#!/usr/bin/env python3
"""HyprSchedule — thin Eww-side driver for the Add/Edit UI (Phase 6).

Presentation glue only. Every read goes through ``schedctl editor-data``
(JSON), every mutation through ``schedctl editor-save`` (one JSON document on
stdin) and ``schedctl delete`` — user values NEVER reach a shell (no shell
invocation anywhere; all subprocess calls use argv lists).

Subcommands (``COMMANDS`` dispatch table below):

  open-add                 fresh form -> editor window
  open-edit <id>           preload form for event <id>
  prompt <var> <label>     text input via rofi (fallback: yad); None = cancel
  set <var> <value>        fixed option value (allowlisted vars only)
  toggle-reminder <min>    toggle a reminder offset in editor-reminders
  toggle-weekday <mon..sun> toggle a weekday in editor-weekdays
  save [force]             build the form document and run editor-save
  delete                   delete the loaded event (after confirmation)
  close                    close the editor window

Eww has no text-input widget, so ``prompt`` fields are edited externally;
rofi/yad are optional desktop tools and their absence simply disables the
field (it never crashes anything).

The eww binary and schedctl console script are resolved from $PATH unless
HYPRSCHEDULE_EWW / HYPRSCHEDULE_SCHEDCTL override them (used by tests).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Sequence

EWW = os.environ.get("HYPRSCHEDULE_EWW", "eww")
SCHEDCTL = os.environ.get("HYPRSCHEDULE_SCHEDCTL", "schedctl")

EDITOR_WINDOW = "hyprschedule-editor"

TEXT_FIELDS = {
    "title": "Tiêu đề",
    "date": "Ngày (YYYY-MM-DD)",
    "start": "Giờ bắt đầu (HH:MM)",
    "end": "Giờ kết thúc (HH:MM)",
    "location": "Địa điểm",
    "description": "Mô tả",
    "until": "Lặp đến (YYYY-MM-DD, để trống = vô hạn)",
}

# Vars the fixed option buttons may write. Defense in depth: yuck ``onclick``
# values are trusted, but arbitrary var writes must still be impossible.
SETTABLE_VARS = ("editor-state", "editor-type", "editor-priority",
                 "editor-privacy", "editor-recurrence")


class DriverError(Exception):
    """Fatal driver failure; printed to stderr, exit 1."""


# ------------------------------------------------------------- eww plumbing


def _run(argv: Sequence[str], *, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv), input=input_text, capture_output=True, text=True, check=False
    )


def _eww_update(variables: dict[str, str]) -> None:
    if not variables:
        return
    _run([EWW, "update", *(f"{name}:{value}" for name, value in variables.items())])


def _eww_get(name: str) -> str:
    result = _run([EWW, "get", name])
    if result.returncode != 0:
        raise DriverError(f"eww không phản hồi ({result.stderr.strip() or 'eww get thất bại'})")
    return result.stdout.rstrip("\n")


def _refresh_widget() -> None:
    """Re-run the schedule defpoll so the widget reflects the mutation."""
    _run([EWW, "reload"])


def _schedctl(*argv: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    return _run([SCHEDCTL, *argv], input_text=input_text)


def _open_window() -> None:
    _run([EWW, "open", EDITOR_WINDOW])


def _close_window() -> None:
    _run([EWW, "close", EDITOR_WINDOW])


# ------------------------------------------------------------- form plumbing


def _push_form(form: dict) -> None:
    recurrence = form.get("recurrence") or {}
    _eww_update({
        "editor-id": str(form["id"]) if form.get("id") is not None else "",
        "editor-mode": form.get("mode", "add"),
        "editor-title": form.get("title", ""),
        "editor-date": form.get("date", ""),
        "editor-start": form.get("start", ""),
        "editor-end": form.get("end", ""),
        "editor-location": form.get("location", ""),
        "editor-description": form.get("description", ""),
        "editor-type": form.get("event_type", "class"),
        "editor-priority": form.get("priority", "normal"),
        "editor-privacy": form.get("privacy", "public"),
        "editor-reminders": ",".join(str(r) for r in form.get("reminders", [])),
        "editor-recurrence": recurrence.get("type", "none"),
        "editor-weekdays": ",".join(recurrence.get("weekdays", [])),
        "editor-until": recurrence.get("until") or "",
        "editor-state": "idle",
        "editor-message": "",
        "editor-conflict": "",
    })


def _collect_form(force: bool) -> dict:
    def get(name: str) -> str:
        return _eww_get(name)

    return {
        "mode": get("editor-mode"),
        "id": int(get("editor-id")) if get("editor-id") else None,
        "title": get("editor-title"),
        "date": get("editor-date"),
        "start": get("editor-start"),
        "end": get("editor-end"),
        "location": get("editor-location"),
        "description": get("editor-description"),
        "event_type": get("editor-type"),
        "priority": get("editor-priority"),
        "privacy": get("editor-privacy"),
        "reminders": [int(x) for x in get("editor-reminders").split(",") if x],
        "recurrence": {
            "type": get("editor-recurrence"),
            "weekdays": [w for w in get("editor-weekdays").split(",") if w],
            "until": get("editor-until") or None,
        },
        "force": force,
    }


# ---------------------------------------------------------------- commands


def _open_add() -> int:
    result = _schedctl("editor-data")
    if result.returncode != 0:
        raise DriverError(result.stderr.strip() or "không lấy được form mới")
    _push_form(json.loads(result.stdout))
    _open_window()
    return 0


def _open_edit(event_id: str) -> int:
    result = _schedctl("editor-data", event_id)
    if result.returncode != 0:
        raise DriverError(result.stderr.strip() or f"không tìm thấy lịch #{event_id}")
    _push_form(json.loads(result.stdout))
    _open_window()
    return 0


def _prompt(var: str, label: str) -> int:
    if var not in TEXT_FIELDS:
        raise DriverError(f"không hỗ trợ trường nhập liệu {var!r}")
    current = _eww_get(f"editor-{var}")
    value = _prompt_text(label, current)
    if value is None:
        return 0  # cancelled
    _eww_update({f"editor-{var}": value})
    return 0


def _prompt_text(label: str, current: str) -> str | None:
    rofi = shutil.which("rofi")
    if rofi:
        result = _run([rofi, "-dmenu", "-p", label], input_text=current)
        if result.returncode == 0:
            return result.stdout.rstrip("\n")
        return None
    yad = shutil.which("yad")
    if yad:
        result = _run(
            [yad, "--entry", "--title", label, "--entry-text", current]
        )
        if result.returncode == 0:
            return result.stdout.rstrip("\n")
        return None
    return None


def _set(var: str, value: str) -> int:
    if var not in SETTABLE_VARS:
        raise DriverError(f"không được phép gán {var!r}")
    _eww_update({var: value})
    return 0


def _toggle_reminder(minutes: str) -> int:
    current = _eww_get("editor-reminders")
    parts = [x for x in current.split(",") if x]
    if minutes in parts:
        parts = [x for x in parts if x != minutes]
    else:
        parts.append(minutes)
    _eww_update({"editor-reminders": ",".join(sorted(parts, key=int))})
    return 0


def _toggle_weekday(name: str) -> int:
    current = _eww_get("editor-weekdays")
    parts = [x for x in current.split(",") if x]
    if name in parts:
        parts = [x for x in parts if x != name]
    else:
        parts.append(name)
    _eww_update({"editor-weekdays": ",".join(parts)})
    return 0


def _save(force: str = "") -> int:
    form = _collect_form(force == "force")
    result = _schedctl("editor-save", input_text=json.dumps(form, ensure_ascii=False))
    if result.returncode == 0:
        _close_window()
        _refresh_widget()
        return 0
    message = result.stderr.strip() or "không lưu được lịch"
    if result.returncode == 3:
        _eww_update({"editor-state": "conflict", "editor-conflict": message})
    else:
        _eww_update({"editor-state": "error", "editor-message": message})
    return result.returncode


def _delete() -> int:
    event_id = _eww_get("editor-id")
    if not event_id:
        raise DriverError("không có lịch nào để xóa")
    result = _schedctl("delete", event_id)
    if result.returncode == 0:
        _close_window()
        _refresh_widget()
        return 0
    _eww_update({
        "editor-state": "error",
        "editor-message": result.stderr.strip() or "không xóa được lịch",
    })
    return result.returncode


def _close() -> int:
    _close_window()
    _eww_update({"editor-state": "idle", "editor-message": "", "editor-conflict": ""})
    return 0


COMMANDS = {
    "open-add": _open_add,
    "open-edit": _open_edit,
    "prompt": _prompt,
    "set": _set,
    "toggle-reminder": _toggle_reminder,
    "toggle-weekday": _toggle_weekday,
    "save": _save,
    "delete": _delete,
    "close": _close,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args or args[0] not in COMMANDS:
        print("usage: hyprschedule_editor.py <command> [args...]", file=sys.stderr)
        return 2
    try:
        return int(COMMANDS[args[0]](*args[1:]) or 0)
    except (DriverError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())