"""XDG Base Directory path resolution.

All functions read the environment at call time (no caching) so tests can
monkeypatch ``XDG_*`` variables freely.
"""

from __future__ import annotations

import os
from pathlib import Path


def config_home() -> Path:
    """Return ``$XDG_CONFIG_HOME`` or ``~/.config``."""
    value = os.environ.get("XDG_CONFIG_HOME")
    if value:
        return Path(value)
    return Path.home() / ".config"


def data_home() -> Path:
    """Return ``$XDG_DATA_HOME`` or ``~/.local/share``."""
    value = os.environ.get("XDG_DATA_HOME")
    if value:
        return Path(value)
    return Path.home() / ".local/share"


def runtime_dir() -> Path:
    """Return ``$XDG_RUNTIME_DIR/hyprschedule`` or ``/tmp/hyprschedule``."""
    value = os.environ.get("XDG_RUNTIME_DIR")
    if value:
        return Path(value) / "hyprschedule"
    return Path("/tmp") / "hyprschedule"


def config_dir() -> Path:
    """Return the HyprSchedule config directory."""
    return config_home() / "hyprschedule"


def data_dir() -> Path:
    """Return the HyprSchedule persistent data directory."""
    return data_home() / "hyprschedule"


def config_file() -> Path:
    """Return the path to ``config.toml``."""
    return config_dir() / "config.toml"


def database_path() -> Path:
    """Return the path to the SQLite database."""
    return data_dir() / "schedule.db"