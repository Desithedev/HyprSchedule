"""Phase 5 static validation of the project-owned hyprlock snippet.

``hyprlock`` may not be runnable in CI (no Wayland session), so like the Eww
config tests this verifies statically: the snippet exists, only documented
label keys are used, the dynamic command polls ``schedctl lock`` with a
reasonable interval, and the documentation does not claim an include
mechanism hyprlock does not have.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = PROJECT_ROOT / "hyprlock"
CONF = LOCK_DIR / "schedule.conf"

KNOWN_LABEL_KEYS = {
    "monitor",
    "text",
    "font_size",
    "position",
    "halign",
    "valign",
    "text_align",
}


def test_hyprlock_snippet_exists():
    assert CONF.is_file()
    assert "label {" in CONF.read_text(encoding="utf-8")


def test_hyprlock_poll_uses_schedctl_lock_at_30s():
    src = CONF.read_text(encoding="utf-8")
    assert "cmd[update:30000] schedctl lock" in src


def test_hyprlock_snippet_uses_only_documented_label_keys():
    src = CONF.read_text(encoding="utf-8")
    inside_label = False
    for raw in src.splitlines():
        stripped = raw.split("#", 1)[0].strip()
        if stripped == "label {":
            inside_label = True
            continue
        if inside_label:
            if stripped == "}":
                break
            if "=" not in stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            assert key in KNOWN_LABEL_KEYS, f"unknown hyprlock label key {key!r}"


def test_hyprlock_snippet_braces_balanced():
    src = CONF.read_text(encoding="utf-8")
    assert src.count("{") == src.count("}") == 1


def test_docs_do_not_claim_hyprlock_source_directive():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    bad = [
        line
        for line in readme.splitlines()
        if "source =" in line and "hyprlock" in line
    ]
    assert not bad