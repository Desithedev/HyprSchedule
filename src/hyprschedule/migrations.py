"""Database schema migrations.

A simple, extensible integer-versioned migration system: each migration is a
``(version, sql)`` pair applied in order inside a transaction. The current
version is tracked in the ``schema_version`` table.

Migrations must be append-only: never edit an already-released migration, add
a new one instead (ARCHITECTURE.md, migration strategy).
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 2

MIGRATION_001 = """
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    timezone TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'other',
    priority TEXT NOT NULL DEFAULT 'normal',
    rrule TEXT,
    recurrence_group_id INTEGER,
    privacy TEXT NOT NULL DEFAULT 'public',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    minutes_before INTEGER NOT NULL,
    sound INTEGER NOT NULL DEFAULT 1,
    critical INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE recurrence_exceptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    occurrence_start TEXT NOT NULL,
    action TEXT NOT NULL,
    title TEXT,
    start_at TEXT,
    end_at TEXT,
    UNIQUE(event_id, occurrence_start)
);

CREATE TABLE notification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    occurrence_start TEXT NOT NULL,
    reminder_minutes INTEGER NOT NULL,
    notified_at TEXT NOT NULL,
    UNIQUE(event_id, occurrence_start, reminder_minutes)
);
"""

MIGRATION_002 = """
-- Phase 1: enforce unique reminders per (event, offset) as documented in
-- ARCHITECTURE.md (migration 001 shipped without the index). Deduplicate
-- first so existing Phase 0 databases always upgrade cleanly.
DELETE FROM reminders
WHERE id NOT IN (
    SELECT MIN(id) FROM reminders GROUP BY event_id, minutes_before
);

CREATE UNIQUE INDEX idx_reminders_event_minutes
    ON reminders(event_id, minutes_before);

-- Phase 1: allow modify-exceptions to override the occurrence location.
ALTER TABLE recurrence_exceptions ADD COLUMN location TEXT;
"""

MIGRATIONS: list[tuple[int, str]] = [
    (1, MIGRATION_001),
    (2, MIGRATION_002),
]


def _current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return int(row[0]) if row else 0


def apply_migrations(
    conn: sqlite3.Connection, up_to: int | None = None
) -> None:
    """Apply all pending migrations in order (idempotent).

    ``up_to`` caps the highest version applied; used by tests to build an
    intermediate schema (e.g. a Phase 0 database).
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    current = _current_version(conn)
    for version, sql in sorted(MIGRATIONS):
        if version <= current:
            continue
        if up_to is not None and version > up_to:
            break
        conn.executescript(sql)
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        conn.commit()