"""Shared fixtures for the HyprSchedule test suite."""

from datetime import datetime

import pytest

from hyprschedule.clock import clock
from hyprschedule.database import Database
from hyprschedule.paths import database_path
from hyprschedule.repository import EventRepository


class _CliEnv:
    """Isolated CLI environment with a repository bound to the XDG data dir.

    ``database_path()`` resolves from the monkeypatched ``XDG_*`` variables at
    call time, so the repository always targets the per-test temp data dir and
    never the real ``~/.local/share``.
    """

    def __init__(self) -> None:
        self.db = Database(database_path())
        self.db.initialize()
        self.repo = EventRepository(self.db)

    def close(self) -> None:
        self.db.close()


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Redirect XDG homes to a temp dir and expose a ``repo`` for DB checks.

    Must be applied BEFORE any ``main()`` call so the CLI's ``build_context()``
    reads the isolated paths.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    env = _CliEnv()
    try:
        yield env
    finally:
        env.close()


@pytest.fixture
def repo(cli_env):
    """Repository bound to the isolated CLI database."""
    return cli_env.repo


@pytest.fixture
def frozen_clock():
    """Pin the CLI clock; call ``frozen_clock(aware_dt)`` inside the test.

    Real time is restored automatically after the test.
    """

    def _freeze(value: datetime) -> None:
        clock.set(value)

    clock.set(None)
    yield _freeze
    clock.set(None)


@pytest.fixture
def run(capsys):
    """Invoke ``schedctl`` through the CLI entry point.

    Returns ``(exit_code, captured)`` where ``captured`` is the capsys result
    (``captured.out`` / ``captured.err``).
    """

    def _run(args: list[str]):
        from hyprschedule.cli import main

        code = main(args)
        return code, capsys.readouterr()

    return _run
