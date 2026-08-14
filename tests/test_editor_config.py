"""Phase 6 static validation of the Eww editor UI and its driver script.

``eww`` is not installed here, so the GUI cannot be smoke-tested at runtime.
Instead we verify, statically:

1. The driver script exists, is executable, and its ``COMMANDS`` dispatch
   table covers every ``bin/hyprschedule_editor.py ...`` ``onclick`` the yuck
   emits (including the exact subcommands used).
2. ``prompt``/``set`` targets in yuck are allowlisted by the script
   (``TEXT_FIELDS`` / ``SETTABLE_VARS``).
3. Editor state values used in the yuck belong to the documented state
   machine.
4. Every ``editor-*`` var the script pushes or reads has a ``defvar``.
5. The script never builds shell commands: no ``shell=True``, no
   ``os.system``, no ``subprocess`` calls outside the argv-list helpers.
6. Parentheses balance and every ``editor-*`` class is styled in SCSS.
"""

import os
import re
from pathlib import Path

from hyprschedule.editor import WEEKDAY_NAMES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EWW_DIR = PROJECT_ROOT / "eww"
YUCK = EWW_DIR / "eww.yuck"
SCSS = EWW_DIR / "eww.scss"
SCRIPT = EWW_DIR / "bin" / "hyprschedule_editor.py"

ONCLICK_RE = re.compile(r':onclick "bin/hyprschedule_editor\.py ([a-z-]+)(?: [^"]*)?"')
PROMPT_VAR_RE = re.compile(r':prompt-var "([a-z-]+)"')
SET_RE = re.compile(r'set (editor-[a-z-]+) ([a-z-]+)')
STATE_VISIBLE_RE = re.compile(r'\(editor-state\) == "([a-z-]+)"')

DOCUMENTED_STATES = {
    "idle", "editing", "saving", "success", "error", "conflict", "delete-confirm",
}


def _script_source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _script_commands() -> set[str]:
    block = _script_source().split("COMMANDS = {", 1)[1].split("}", 1)[0]
    return set(re.findall(r'"([a-z-]+)":', block))


def _script_keys(name: str) -> set[str]:
    """Keys of a dict literal assigned to ``name`` in the script."""
    src = _script_source()
    start = src.index(f"{name} = {{") + len(f"{name} = {{")
    end = src.index("}", start)
    return set(re.findall(r'"([^"]+)"\s*:', src[start:end]))


def _script_tuple_tokens(name: str) -> set[str]:
    """Tokens of a tuple literal assigned to ``name`` in the script."""
    src = _script_source()
    start = src.index(f"{name} = (") + len(f"{name} = (")
    end = src.index(")", start)
    return set(re.findall(r'"([^"]+)"', src[start:end]))


# ---------------------------------------------------------------- basics


def test_editor_files_exist():
    assert YUCK.is_file()
    assert SCSS.is_file()
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK), "driver must be executable"


def test_editor_yuck_parens_balanced():
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


def test_editor_window_and_vars_declared():
    src = YUCK.read_text(encoding="utf-8")
    assert "(defwindow hyprschedule-editor" in src
    assert "(defvar editor-state \"idle\")" in src
    for var in (
        "editor-mode", "editor-id", "editor-title", "editor-date", "editor-start",
        "editor-end", "editor-location", "editor-description", "editor-type",
        "editor-priority", "editor-privacy", "editor-reminders", "editor-recurrence",
        "editor-weekdays", "editor-until", "editor-message", "editor-conflict",
    ):
        assert f"(defvar {var}" in src, f"missing defvar {var}"


def test_editor_state_machine_values_are_documented():
    src = YUCK.read_text(encoding="utf-8")
    used = set(STATE_VISIBLE_RE.findall(src))
    used.update(match.group(2) for match in SET_RE.finditer(src)
                if match.group(1) == "editor-state")
    assert used, "no editor-state values found in yuck"
    assert used <= DOCUMENTED_STATES, f"undocumented states: {used - DOCUMENTED_STATES}"


# ------------------------------------------------------- onclick dispatch


def test_all_onclick_commands_have_dispatch_entries():
    src = YUCK.read_text(encoding="utf-8")
    used = set(ONCLICK_RE.findall(src))
    assert used, "no bin/hyprschedule_editor.py onclick found in yuck"
    available = _script_commands()
    assert used <= available, f"undispatched commands: {used - available}"


def test_all_prompt_vars_are_text_fields():
    src = YUCK.read_text(encoding="utf-8")
    used = set(PROMPT_VAR_RE.findall(src))
    assert used == {"title", "date", "start", "end", "location", "description", "until"}
    assert used <= _script_keys("TEXT_FIELDS")


def test_all_set_targets_are_allowlisted():
    src = YUCK.read_text(encoding="utf-8")
    targets = {match.group(1) for match in SET_RE.finditer(src)}
    assert targets <= _script_tuple_tokens("SETTABLE_VARS")


def test_weekday_toggles_match_backend_names():
    src = YUCK.read_text(encoding="utf-8")
    toggled = set(re.findall(r"toggle-weekday ([a-z]+)", src))
    assert toggled == set(WEEKDAY_NAMES)


def test_reminder_toggles_are_offsets():
    src = YUCK.read_text(encoding="utf-8")
    offsets = {int(m) for m in re.findall(r"toggle-reminder (\d+)", src)}
    assert offsets == {0, 5, 10, 15, 30, 60}


# ------------------------------------------------- script var round trip


def test_script_pushes_only_declared_vars():
    src = _script_source()
    known = {
        "editor-state", "editor-mode", "editor-id", "editor-title", "editor-date",
        "editor-start", "editor-end", "editor-location", "editor-description",
        "editor-type", "editor-priority", "editor-privacy", "editor-reminders",
        "editor-recurrence", "editor-weekdays", "editor-until", "editor-message",
        "editor-conflict",
    }
    used = set(re.findall(r"editor-[a-z-]+", src)) & known
    assert used, "no editor-* vars used by the script"
    yuck = YUCK.read_text(encoding="utf-8")
    for base in used:
        assert f"(defvar {base}" in yuck, f"undecleared var used: {base}"


# -------------------------------------------------------------- shell safety


def test_driver_never_uses_shell():
    src = _script_source()
    assert "shell=True" not in src
    assert "shell =" not in src
    assert "os.system" not in src
    assert "os.popen" not in src
    assert "subprocess.Popen" not in src
    assert "eval(" not in src
    assert "exec(" not in src


def test_all_subprocess_calls_use_argv_lists():
    src = _script_source()
    call_sites = re.findall(r"(subprocess\.run\(|_run\(\[EWW|_run\(\[SCHEDCTL)", src)
    assert call_sites, "expected argv-list subprocess calls"
    assert "subprocess.run(" in src
    lines = [line.strip() for line in src.splitlines()]
    assert any("subprocess.run(" in line for line in lines)


def test_schedctl_arguments_are_list_built():
    src = _script_source()
    assert "def _schedctl(*argv" in src
    assert "def _run(argv" in src


# ------------------------------------------------------------------- styling


def test_editor_classes_styled_in_scss():
    scss = SCSS.read_text(encoding="utf-8")
    for cls in (
        "header-add", "hyprschedule-editor", "editor-header", "editor-heading",
        "editor-close", "editor-body", "editor-field", "editor-label",
        "editor-value", "editor-value-text", "editor-picker-row", "editor-options",
        "editor-option", "editor-recurring-hint", "editor-error-box",
        "editor-error", "editor-conflict-box", "editor-conflict-title",
        "editor-conflict", "editor-confirm-box", "editor-confirm",
        "editor-danger", "editor-plain", "editor-delete-row", "editor-delete",
        "editor-footer", "editor-save", "editor-cancel",
    ):
        assert f".{cls}" in scss, f"missing .{cls} in eww.scss"


def test_event_row_button_styling_present():
    scss = SCSS.read_text(encoding="utf-8")
    assert ".event-row:hover" in scss


def test_delete_confirmation_flow_present():
    src = YUCK.read_text(encoding="utf-8")
    assert "delete-confirm" in src
    assert "bin/hyprschedule_editor.py delete" in src
    assert "Xóa toàn bộ chuỗi lịch lặp?" in src


def test_conflict_flow_has_explicit_force():
    src = YUCK.read_text(encoding="utf-8")
    assert "bin/hyprschedule_editor.py save force" in src
    assert "Lưu vẫn cứ lưu" in src
    assert "Quay lại" in src


def test_recurring_edit_hint_present():
    src = YUCK.read_text(encoding="utf-8")
    assert "Thay đổi này áp dụng cho toàn bộ lịch lặp." in src


def test_widget_integration_buttons_present():
    src = YUCK.read_text(encoding="utf-8")
    assert "bin/hyprschedule_editor.py open-add" in src
    assert "bin/hyprschedule_editor.py open-edit ${event.event_id}" in src