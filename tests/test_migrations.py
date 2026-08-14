"""Migration compatibility tests.

Phase 0 schema (migration 1) must upgrade cleanly to Phase 1 (migration 2):
existing rows are preserved, duplicate reminders are deduplicated, a UNIQUE
reminder index is added, and ``recurrence_exceptions`` gains a ``location``
column.
"""

import sqlite3

import pytest

from hyprschedule.database import Database
from hyprschedule.migrations import SCHEMA_VERSION, apply_migrations

EXPECTED_TABLES = {
    "events",
    "reminders",
    "recurrence_exceptions",
    "notification_log",
    "schema_version",
}

PHASE0_EVENT_SQL = (
    "INSERT INTO events (title, description, location, start_at, end_at, "
    "timezone, event_type, priority, rrule, recurrence_group_id, privacy, "
    "status, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

PHASE0_EVENT_ROW = (
    "Phase 0 Event",
    "",
    "",
    "2026-08-24T00:00:00+00:00",
    "2026-08-24T01:00:00+00:00",
    "Asia/Ho_Chi_Minh",
    "meeting",
    "normal",
    None,
    None,
    "public",
    "active",
    "2026-08-01T00:00:00+00:00",
    "2026-08-01T00:00:00+00:00",
)


def _phase0_connection(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn, up_to=1)
    return conn


def _insert_phase0_event(conn):
    cur = conn.execute(PHASE0_EVENT_SQL, PHASE0_EVENT_ROW)
    return cur.lastrowid


def test_fresh_database_initializes_to_latest_schema(tmp_path):
    db = Database(tmp_path / "schedule.db")
    db.initialize()
    try:
        assert SCHEMA_VERSION == 2
        assert db.schema_version() == SCHEMA_VERSION
        rows = db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        assert EXPECTED_TABLES <= {row["name"] for row in rows}
    finally:
        db.close()


def test_phase0_schema_upgrades_to_phase1_preserving_data(tmp_path):
    conn = _phase0_connection(tmp_path / "schedule.db")
    try:
        version = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
        assert version == 1

        event_id = _insert_phase0_event(conn)
        conn.execute(
            "INSERT INTO reminders (event_id, minutes_before) VALUES (?, 15)",
            (event_id,),
        )
        conn.execute(
            "INSERT INTO recurrence_exceptions "
            "(event_id, occurrence_start, action, title, start_at, end_at) "
            "VALUES (?, ?, 'cancel', NULL, NULL, NULL)",
            (event_id, "2026-08-24T00:00:00+00:00"),
        )
        conn.commit()

        apply_migrations(conn)

        assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 2
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 1
        assert (
            conn.execute("SELECT COUNT(*) FROM recurrence_exceptions").fetchone()[0] == 1
        )
        event = conn.execute("SELECT title, start_at FROM events").fetchone()
        assert event["title"] == "Phase 0 Event"
        exc_row = conn.execute("SELECT location FROM recurrence_exceptions").fetchone()
        assert exc_row["location"] is None
    finally:
        conn.close()


def test_migration_2_deduplicates_duplicate_reminders(tmp_path):
    conn = _phase0_connection(tmp_path / "schedule.db")
    try:
        event_id = _insert_phase0_event(conn)
        first = conn.execute(
            "INSERT INTO reminders (event_id, minutes_before) VALUES (?, 15)",
            (event_id,),
        ).lastrowid
        duplicate = conn.execute(
            "INSERT INTO reminders (event_id, minutes_before) VALUES (?, 15)",
            (event_id,),
        ).lastrowid
        other = conn.execute(
            "INSERT INTO reminders (event_id, minutes_before) VALUES (?, 30)",
            (event_id,),
        ).lastrowid
        assert duplicate > first
        conn.commit()

        apply_migrations(conn)

        rows = conn.execute(
            "SELECT id, minutes_before FROM reminders WHERE event_id = ? "
            "ORDER BY minutes_before",
            (event_id,),
        ).fetchall()
        assert [(row["id"], row["minutes_before"]) for row in rows] == [
            (first, 15),
            (other, 30),
        ]
    finally:
        conn.close()


def test_migration_2_adds_unique_reminder_index(tmp_path):
    conn = _phase0_connection(tmp_path / "schedule.db")
    try:
        event_id = _insert_phase0_event(conn)
        conn.execute(
            "INSERT INTO reminders (event_id, minutes_before) VALUES (?, 15)",
            (event_id,),
        )
        conn.commit()

        apply_migrations(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO reminders (event_id, minutes_before) VALUES (?, 15)",
                (event_id,),
            )
    finally:
        conn.close()


def test_full_migration_is_idempotent(tmp_path):
    db = Database(tmp_path / "schedule.db")
    db.initialize()
    try:
        assert db.schema_version() == SCHEMA_VERSION
        apply_migrations(db.connection)
        assert db.schema_version() == SCHEMA_VERSION
        row = db.connection.execute("SELECT version FROM schema_version").fetchone()
        assert row["version"] == SCHEMA_VERSION
    finally:
        db.close()
