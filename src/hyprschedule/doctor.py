"""``schedctl doctor`` checks.

Core checks are required for the backend to function; optional checks cover
desktop dependencies (eww, hyprlock, notify-send, pw-play, systemctl) whose
absence must never break the backend.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from hyprschedule import paths
from hyprschedule.config import Config, ConfigError, load_config
from hyprschedule.database import Database
from hyprschedule.migrations import SCHEMA_VERSION

MIN_PYTHON = (3, 11)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    critical: bool = True


def _python_check() -> CheckResult:
    ok = sys.version_info >= MIN_PYTHON
    return CheckResult(
        name="Python",
        ok=ok,
        detail=f"{sys.version.split()[0]} (requires {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+)",
    )


def _config_check(config: Config) -> CheckResult:
    path = paths.config_file()
    if not path.exists():
        return CheckResult(name="Config", ok=True, detail="defaults (no config file)")
    return CheckResult(
        name="Config",
        ok=True,
        detail=str(path),
    )


def _data_dir_check() -> CheckResult:
    return CheckResult(name="Data directory", ok=True, detail=str(paths.data_dir()))


def _database_check() -> CheckResult:
    path = paths.database_path()
    try:
        db = Database(path)
        db.initialize()
        db.close()
    except Exception as exc:
        return CheckResult(name="SQLite database", ok=False, detail=str(exc))
    return CheckResult(name="SQLite database", ok=True, detail=f"{path} (schema v{SCHEMA_VERSION})")


def _schema_check(db_path: Path | None = None) -> CheckResult:
    path = db_path or paths.database_path()
    try:
        version = Database(path).schema_version()
    except Exception as exc:
        return CheckResult(name="Database schema", ok=False, detail=str(exc))
    ok = version == SCHEMA_VERSION
    detail = f"v{version}" if ok else f"v{version}, expected v{SCHEMA_VERSION}"
    return CheckResult(name="Database schema", ok=ok, detail=detail)


def _timezone_check(config: Config) -> CheckResult:
    try:
        ZoneInfo(config.timezone)
    except Exception:
        return CheckResult(name="Timezone", ok=False, detail=config.timezone)
    return CheckResult(name="Timezone", ok=True, detail=config.timezone)


def _which_check(name: str, binary: str, note: str, critical: bool = False) -> CheckResult:
    found = shutil.which(binary) is not None
    detail = f"{binary} found" if found else f"{binary} not found"
    return CheckResult(name=name, ok=found, detail=detail, critical=critical)


def run_checks(config: Config | None = None) -> list[CheckResult]:
    """Run all doctor checks. Never raises on missing/invalid environment."""
    if config is None:
        try:
            config = load_config()
        except ConfigError as exc:
            config = None
            config_error = exc

    results: list[CheckResult] = [
        _python_check(),
        _data_dir_check(),
    ]

    if config is None:
        results.append(
            CheckResult(name="Config", ok=False, detail=str(config_error), critical=True)
        )
        results.append(
            CheckResult(name="Timezone", ok=False, detail="config unavailable", critical=True)
        )
    else:
        results.append(_config_check(config))
        results.append(_timezone_check(config))

    results.append(_database_check())
    results.append(_schema_check())

    results.append(
        _which_check("notify-send", "notify-send",
                     "Desktop notifications will not work.", critical=False)
    )
    results.append(
        _which_check("pw-play", "pw-play",
                     "Sound reminders will not work.", critical=False)
    )
    results.append(
        _which_check("eww", "eww",
                     "Desktop widget will not work. Backend functionality remains available.",
                     critical=False)
    )
    results.append(
        _which_check("hyprlock", "hyprlock",
                     "Lockscreen integration will not work.", critical=False)
    )
    results.append(
        _which_check("systemctl", "systemctl",
                     "User services will not be manageable.", critical=False)
    )
    results.append(
        _which_check("scheduled", "scheduled",
                     "Scheduler daemon is not installed; reminders will not fire.",
                     critical=False)
    )

    return results


def exit_code(results: list[CheckResult]) -> int:
    """Return 0 when all critical checks pass, 1 otherwise."""
    for result in results:
        if result.critical and not result.ok:
            return 1
    return 0


def format_report(results: list[CheckResult]) -> str:
    """Render the doctor report as human-readable text."""
    lines = ["HyprSchedule doctor", ""]
    for result in results:
        mark = "\u2713" if result.ok else "\u2717"
        if result.ok:
            lines.append(f"{mark} {result.name} {result.detail}".rstrip())
        else:
            lines.append(f"{mark} {result.name}")
            if result.detail:
                lines.append(f"  {result.detail}")
            if not result.critical:
                lines.append("  Backend functionality remains available.")
    lines.append("")
    return "\n".join(lines)