"""SQLite connection management and initialization.

Uses the standard library ``sqlite3`` module. Foreign keys are enforced and
WAL journal mode is enabled for future concurrent daemon/CLI access.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from hyprschedule import migrations


class Database:
    """Owns a single SQLite connection to *path*."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.isolation_level = None
        return conn

    def initialize(self) -> None:
        """Create the data directory, open the connection, run migrations."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        conn.execute("PRAGMA journal_mode = WAL")
        migrations.apply_migrations(conn)
        self._conn = conn

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the open connection, initializing on first use."""
        if self._conn is None:
            self.initialize()
        assert self._conn is not None
        return self._conn

    def schema_version(self) -> int:
        """Return the current database schema version (0 if uninitialized)."""
        conn = self._connect()
        try:
            if not conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone():
                return 0
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block of statements atomically (BEGIN/COMMIT/ROLLBACK).

        The connection runs in autocommit mode, so multi-statement
        operations must go through this manager to avoid partially saved
        state.
        """
        conn = self.connection
        conn.execute("BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def __enter__(self) -> "Database":
        self.initialize()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()