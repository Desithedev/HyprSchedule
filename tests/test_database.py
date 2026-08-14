import sqlite3

import pytest

from hyprschedule.database import Database
from hyprschedule.migrations import SCHEMA_VERSION

EXPECTED_TABLES = {
    "events",
    "reminders",
    "recurrence_exceptions",
    "notification_log",
    "schema_version",
}

INSERT_EVENT_SQL = (
    "INSERT INTO events (title, start_at, end_at, timezone, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)


def test_initialize_creates_file_and_schema(tmp_path):
    db_path = tmp_path / "schedule.db"
    db = Database(db_path)
    db.initialize()
    try:
        assert db_path.exists()
        assert db.schema_version() == SCHEMA_VERSION
        assert db.schema_version() == SCHEMA_VERSION
    finally:
        db.close()


def test_expected_tables_exist(tmp_path):
    db = Database(tmp_path / "schedule.db")
    db.initialize()
    try:
        rows = db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        table_names = {row["name"] for row in rows}
        assert EXPECTED_TABLES <= table_names
    finally:
        db.close()


def test_initialize_is_idempotent(tmp_path):
    db = Database(tmp_path / "schedule.db")
    db.initialize()
    db.initialize()
    try:
        assert db.schema_version() == SCHEMA_VERSION
    finally:
        db.close()


def test_foreign_keys_pragma_enabled(tmp_path):
    db = Database(tmp_path / "schedule.db")
    db.initialize()
    try:
        row = db.connection.execute("PRAGMA foreign_keys").fetchone()
        assert row["foreign_keys"] == 1
    finally:
        db.close()


def test_rows_use_sqlite_row_factory(tmp_path):
    db = Database(tmp_path / "schedule.db")
    db.initialize()
    try:
        row = db.connection.execute("SELECT version FROM schema_version").fetchone()
        assert isinstance(row, sqlite3.Row)
        assert row["version"] == SCHEMA_VERSION
    finally:
        db.close()


def test_foreign_key_enforced_on_reminders(tmp_path):
    db = Database(tmp_path / "schedule.db")
    db.initialize()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            db.connection.execute(
                "INSERT INTO reminders (event_id, minutes_before) VALUES (999, 5)"
            )
    finally:
        db.close()


def test_delete_event_cascades_to_reminders(tmp_path):
    db = Database(tmp_path / "schedule.db")
    db.initialize()
    try:
        cur = db.connection.execute(
            INSERT_EVENT_SQL,
            (
                "Meeting",
                "2026-08-13T09:00:00",
                "2026-08-13T10:00:00",
                "Asia/Ho_Chi_Minh",
                "2026-08-01T00:00:00",
                "2026-08-01T00:00:00",
            ),
        )
        event_id = cur.lastrowid
        db.connection.execute(
            "INSERT INTO reminders (event_id, minutes_before) VALUES (?, 15)",
            (event_id,),
        )
        db.connection.commit()

        db.connection.execute("DELETE FROM events WHERE id = ?", (event_id,))
        db.connection.commit()

        count = db.connection.execute(
            "SELECT COUNT(*) FROM reminders WHERE event_id = ?", (event_id,)
        ).fetchone()[0]
        assert count == 0
    finally:
        db.close()


def test_context_manager_usage(tmp_path):
    db_path = tmp_path / "schedule.db"
    with Database(db_path) as db:
        assert db.schema_version() == SCHEMA_VERSION
        db.connection.execute(
            INSERT_EVENT_SQL,
            (
                "Standup",
                "2026-08-13T09:00:00",
                "2026-08-13T09:30:00",
                "UTC",
                "2026-08-01T00:00:00",
                "2026-08-01T00:00:00",
            ),
        )
    assert db_path.exists()