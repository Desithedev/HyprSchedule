# HyprSchedule — Architecture

This document records concrete technical decisions for HyprSchedule. It is a
living document: any structural or architectural change must be recorded here
(see [AGENTS.md](AGENTS.md), rule 2).

## System Overview

HyprSchedule is a local-first personal schedule system for Arch Linux +
Hyprland. A single Python backend is the **single source of truth** for all
schedule data, computation, and formatting.

There are exactly two backend executables:

1. **`schedctl`** — the CLI. In Phase 0 it implements only the `doctor`
   subcommand plus `--version` / `--verbose`. Later phases add
   `today`, `tomorrow`, `week`, `next`, `add`, `edit`, `delete`, `show`,
   `search`, and frontend-facing `eww` / `lock` commands.
2. **`scheduled`** — the asyncio scheduler daemon (Phase 3 — implemented).
   Design:
   - asyncio-based, **no polling loop** (never sleep-check-sleep every second);
     it computes the next reminder deadline and sleeps exactly until then
     (bounded by a 60-day recompute horizon).
   - reloads on **SIGUSR1** (e.g. after a CLI edit) by waking an
     `asyncio.Event`.
   - writes to `notification_log` to dedup notifications across restarts.
   - handles suspend/resume by comparing missed occurrence start times against
     `missed_event_window_minutes` (default 30) and emitting delayed
     notifications.
   - writes a pid file at `$XDG_RUNTIME_DIR/hyprschedule/scheduled.pid`;
     `schedctl add/edit/delete` best-effort-send SIGUSR1 to it (a missing or
     stale pid file never fails the CLI command — the DB stays the source of
     truth).
   - `scheduled --once` processes due notifications and exits (test / smoke
     mode; no pid file).

Eww and hyprlock are **presentation-only** layers. They must never compute
business logic: no recurrence expansion, no reminder calculation, no direct
SQLite queries, no conflict detection, no timezone handling, no database
writes, no complex countdowns. They render JSON produced by `schedctl`.

## Component Boundaries and Data Flow

```
                       schedule.db
                          SQLite
                            │
              ┌─────────────┴─────────────┐
              │                           │
          schedctl                     scheduled
             CLI                   asyncio daemon
              │                           │
       ┌──────┼────────┐            ┌─────┴──────┐
       │      │        │            │            │
       ▼      ▼        ▼            ▼            ▼
      Eww   hyprlock   CLI      notify-send    pw-play
```

Data flow (current, Phase 0–2):

```
config.toml ──> config.py ──> schedctl doctor
                                   │
paths (XDG) <──────────────────────┤
                                   ▼
                          schedule.db (SQLite, migrations)

schedctl today/tomorrow/week/next ──> commands.py ──> EventRepository
schedctl add/edit/delete ───────────> commands.py ──> EventRepository ──> schedule.db
schedctl show/search ───────────────> commands.py ──> EventRepository
        │
        └── conflicts / recurrence  (via repository, backend only)

EventRepository (repository.py) ──> schedule.db
        │  create/get/update/delete/list
        │  get_occurrences(range_start, range_end)   <- recurrence.py + exceptions
        │  add/list/remove/replace reminders
        │  add/list/remove recurrence exceptions
        │  search_events(query)
        ▼
conflicts.find_conflicts(start, end, exclude_event_id=)  <- conflicts.py
```

Data flow (current, Phase 0–3):

```
config.toml ──> config.py ──> schedctl doctor
                                   │
paths (XDG) <──────────────────────┤
                                   ▼
                          schedule.db (SQLite, migrations)

schedctl today/tomorrow/week/next ──> commands.py ──> EventRepository
schedctl add/edit/delete ───────────> commands.py ──> EventRepository ──> schedule.db
        │                                     │
        │ SIGUSR1 (best-effort)               ▼ SIGUSR1
        ▼                               scheduled daemon
  scheduled.pid (runtime dir)                 │
                                ┌─────────────┼─────────────┐
                                ▼             ▼             ▼
                          notify-send     pw-play     (recompute)
                                │
Eww <─────────── schedctl eww (JSON) ─────────────┼── schedctl lock (plain text) ──> hyprlock
```

Data flow (current, Phase 6):

```
Eww editor ──> bin/hyprschedule_editor.py ──> schedctl editor-data (read-only JSON)
                                           └─> schedctl editor-save (mutate, JSON on stdin)
                                                    │
                                                    ▼
                              editor.py ──> cmd_add / cmd_edit ──> EventRepository ──> schedule.db
                                                    │
                                                    └─> SIGUSR1 ──> scheduled daemon (reload)
```

Rules:

- All data flows through the Python backend. UIs never touch SQLite directly.
- The daemon is the only writer of `notification_log`.
- The CLI is the only writer of user-visible event data; the daemon only reads.

## XDG Paths

Strictly follows the XDG Base Directory specification. No mutable user data is
ever stored inside the repository.

| Purpose        | Path                                              | Fallback |
| -------------- | ------------------------------------------------- | -------- |
| Config file    | `$XDG_CONFIG_HOME/hyprschedule/config.toml`       | `~/.config/hyprschedule/config.toml` |
| Data directory | `$XDG_DATA_HOME/hyprschedule/`                    | `~/.local/share/hyprschedule/` |
| Database       | `<data_dir>/schedule.db`                          | — |
| Runtime dir    | `$XDG_RUNTIME_DIR/hyprschedule/`                  | — |

Implementation notes:

- `src/hyprschedule/paths.py` resolves these, honoring `XDG_*` env vars and
  falling back per spec. Tests exercise both set and unset env var cases with
  `monkeypatch` — never the real `$HOME`.
- The data directory is created on demand (e.g. at DB initialization).
- A runtime dir is used by the daemon for its pid file
  (`$XDG_RUNTIME_DIR/hyprschedule/scheduled.pid`); it is created on demand,
  never inside the repository.

## SQLite Schema

Database: `<data_dir>/schedule.db`. Managed with stdlib `sqlite3`. No ORM.
Connection settings: `PRAGMA foreign_keys = ON` on every connection.

Full DDL (as of Phase 0):

```sql
CREATE TABLE events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    title                TEXT    NOT NULL,
    description          TEXT    NOT NULL DEFAULT '',
    location             TEXT    NOT NULL DEFAULT '',
    start_at             TEXT    NOT NULL,  -- UTC ISO-8601 with offset, e.g. '2026-08-13T05:30:00+00:00'
    end_at               TEXT    NOT NULL,
    timezone             TEXT    NOT NULL,  -- IANA name, e.g. 'Asia/Ho_Chi_Minh'
    event_type           TEXT    NOT NULL DEFAULT 'other',
    priority             TEXT    NOT NULL DEFAULT 'normal',
    rrule                TEXT,              -- RRULE text, NULL for one-time events
    recurrence_group_id  INTEGER,
    privacy              TEXT    NOT NULL DEFAULT 'public',
    status               TEXT    NOT NULL DEFAULT 'active',
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL
);

CREATE TABLE reminders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    minutes_before INTEGER NOT NULL,
    sound          INTEGER NOT NULL DEFAULT 1,  -- boolean
    critical       INTEGER NOT NULL DEFAULT 0,  -- boolean
    UNIQUE (event_id, minutes_before)
);

CREATE TABLE recurrence_exceptions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    occurrence_start TEXT    NOT NULL,  -- UTC ISO-8601 with offset
    action           TEXT    NOT NULL,  -- 'cancel' | 'modify'
    title            TEXT,
    start_at         TEXT,
    end_at           TEXT,
    location         TEXT,              -- added by migration 002 (Phase 1)
    UNIQUE (event_id, occurrence_start)
);

CREATE TABLE notification_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    occurrence_start TEXT    NOT NULL,  -- UTC ISO-8601 with offset
    reminder_minutes INTEGER NOT NULL,
    notified_at      TEXT    NOT NULL,
    UNIQUE (event_id, occurrence_start, reminder_minutes)
);

CREATE TABLE schema_version (
    version INTEGER NOT NULL
);
```

Enum values (stored as TEXT):

- `event_type`: `class`, `meeting`, `work`, `personal`, `deadline`, `task`, `other`
- `priority`: `low`, `normal`, `high`, `critical`
- `privacy`: `public`, `private`, `hidden`
- `status`: `active`, `done`, `cancelled`
- `recurrence_exceptions.action`: `cancel`, `modify`

Datetime columns (`start_at`, `end_at`, `created_at`, `updated_at`,
`occurrence_start`, `notified_at`) are stored as UTC ISO-8601 TEXT with
explicit offset — never naive local strings.

## Migration Strategy

- A single integer schema version is stored in `schema_version`.
- Ordered SQL migrations live in a list in `src/hyprschedule/migrations.py`.
  Migration `N` upgrades the DB from version `N-1` to `N`.
- `SCHEMA_VERSION = 2` is the current head.
- Each migration runs inside a transaction.
- Re-runs are idempotent: if `schema_version` already equals the head, no
  migrations are applied.
- Initialization creates the DB file, runs `PRAGMA foreign_keys = ON`, then
  applies all pending migrations in order.
- **Migrations are append-only.** Phase 1 added migration 002; migration 001
  is never edited. Existing Phase 0 databases upgrade in place without data
  loss (`apply_migrations` supports an optional `up_to` cap so tests can build
  an intermediate schema). 002 also fixes a Phase 0 inconsistency: the
  architecture documented `UNIQUE (event_id, minutes_before)` on reminders,
  but migration 001 shipped without the index — 002 deduplicates any existing
  rows and adds the unique index, and adds the `location` column to
  `recurrence_exceptions` for modify-exceptions.

## Datetime / Timezone Strategy (DECISION)

1. **All Python datetimes are timezone-aware.** Naive datetimes are forbidden
   in business logic.
2. **The DB stores UTC ISO-8601 TEXT with an explicit offset**, e.g.
   `2026-08-13T05:30:00+00:00`. String form is `YYYY-MM-DDTHH:MM:SS±HH:MM`.
3. **Each event row also stores its IANA timezone name** in the `timezone`
   column (e.g. `Asia/Ho_Chi_Minh`) so display and recurrence expansion can
   reconstruct the user's local time.
4. **All conversions happen in the Python backend only**, using
   `zoneinfo.ZoneInfo` from the standard library. UIs receive pre-formatted
   data and never convert.
5. **Default timezone: `Asia/Ho_Chi_Minh`**, from config (see Configuration
   Model).

Rationale: storing a single canonical UTC text keeps comparisons and range
queries trivial and unambiguous, while the `timezone` column preserves each
event's intended local wall-clock semantics for display and RRULE expansion.

## Recurrence Strategy (Phase 1 — implemented subset)

- RRULE text is stored in the `events.rrule` column. Phase 1 implements a
  minimal well-defined weekly subset in `src/hyprschedule/recurrence.py`:
  - `FREQ=WEEKLY` (required; any other FREQ is rejected)
  - `BYDAY=MO,TU,WE,TH,FR,SA,SU` (comma-separated; omitted → the weekday of
    DTSTART is used; duplicates within BYDAY are tolerated)
  - `UNTIL=YYYYMMDD` or `UNTIL=YYYYMMDDTHHMMSS[Z|±HHMM]` — inclusive last
    date in the event's timezone
  - anything else (`COUNT`, `INTERVAL`, `BYMONTH`, `WKST`, ...) raises
    `InvalidRecurrence` with a clear message — never silently reinterpreted.
- `parse_weekly_rule(rrule, start_at=, end_at=, timezone=)` derives the wall
  clock start time and duration from the first occurrence (DTSTART) and
  returns a `WeeklyRule`; `expand_weekly(rule, range_start, range_end)`
  returns occurrence start datetimes (aware, event timezone) overlapping the
  half-open range `[range_start, range_end)`.
- Expansion is **always bounded**: it only walks the weeks that can overlap
  the requested range (clamped to the series start and
  `min(until, range_end)`). An event without `UNTIL` is safe to query for any
  finite range.
- Occurrence expansion is computed **only in the backend** (`EventRepository`
  in `src/hyprschedule/repository.py`), never in a UI.
- `recurrence_group_id` is reserved for future series grouping (not populated
  in Phase 1); it is carried through CRUD unchanged.

## Recurrence Exceptions (Phase 1 — implemented)

- One row per exception in `recurrence_exceptions`, keyed by
  `(event_id, occurrence_start)`.
- **Exception identity is deterministic: parent event id + original
  occurrence start** (stored as UTC ISO-8601). An exception is never
  identified by weekday alone, and a modified occurrence keeps its original
  identity for matching.
- `action = 'cancel'` hides that single occurrence; `action = 'modify'`
  overrides title / start / end / location for that single occurrence. A
  modify with only `start_at` derives `end_at = start_at + original
  duration`.
- **A series is never mutated in place.** An exception on one occurrence
  never breaks the rest of the recurring series (e.g. cancel only the class
  on `2026-08-24`, keep the weekly series).
- Exceptions are only meaningful for recurring events: `add_exception` on a
  one-time event raises `InvalidException`. Duplicate
  `(event_id, occurrence_start)` is rejected.
- The `Occurrence` dataclass (`models.py`) carries `event_id`,
  `occurrence_start`, `occurrence_end`, `title`, `location`, `event_type`,
  `priority`, `privacy`, `status` — for a modified occurrence the start/end
  are the overridden times.

## Reminders (Phase 1 — implemented)

- A single event may have N reminder rows (`reminders` table):
  `minutes_before` in {60, 15, 5, 0} by default, plus `sound` and `critical`
  flags.
- Reminder deadline = occurrence start minus `minutes_before` (computed by
  the scheduler daemon, Phase 3 — implemented).
- `EventRepository` exposes `add_reminder`, `list_reminders`,
  `remove_reminder`, `replace_reminders`. Validation: `minutes_before` must
  be a non-negative integer.
- **Duplicate behavior (DECISION): duplicate offsets for the same event are
  rejected** with `InvalidReminder` — in the service layer and backed by the
  `UNIQUE (event_id, minutes_before)` index added in migration 002.
- No notifications are sent in Phase 1; this is pure persistence.

## Conflict Detection (Phase 1 — implemented)

- `src/hyprschedule/conflicts.py` exposes
  `find_conflicts(repo, start, end, *, exclude_event_id=None) -> list[Conflict]`.
- **Overlap semantics are half-open intervals** `[start, end)`:
  two occurrences conflict iff `a.start < b.end and b.start < a.end`.
  Adjacent events (`07:00-07:45` and `07:45-08:30`) never conflict; equal
  intervals conflict.
- A `Conflict` is a group of occurrences that overlap **transitively** (each
  member overlaps at least one other member), giving clean clusters for the
  future UI instead of pairwise noise. Groups are deterministically ordered.
- Recurring occurrences are expanded before comparison — only stored parent
  rows are never compared, so a cancelled (exception) occurrence no longer
  conflicts.
- `exclude_event_id` drops one event's occurrences (used when editing, so an
  event never conflicts with itself).
- The search range is widened by the longest stored event duration
  (`repo.max_duration()`) so events already in progress before `start` are
  still detected.

## Delete Semantics (Phase 1 — DECISION)

- **Hard delete.** `delete_event` removes the row; reminders and exceptions
  cascade via `ON DELETE CASCADE`. Phase 0's schema (no soft-delete column)
  already implied this; Phase 1 preserves it. Interactive confirmation is a
  UI concern of later phases.

## Notification Deduplication (Phase 3 — implemented)

- The `notification_log` table's `UNIQUE (event_id, occurrence_start,
  reminder_minutes)` constraint makes each (event, occurrence, offset)
  notifiable exactly once.
- The daemon is the only writer of `notification_log`
  (`repo.record_notification`, `INSERT OR IGNORE` — safe even under a race
  between two processes). `repo.notification_exists` is the read-side dedup
  boundary used on every fire.
- After a daemon restart, already-notified entries are skipped — no duplicate
  notifications.
- A missed-event notice is logged under `reminder_minutes = -1` (sentinel);
  `repo.any_notification(event_id, occurrence_start)` suppresses it when the
  occurrence's regular reminders already fired.

## Suspend / Resume (Phase 3 — implemented)

- Example: suspend at 18:40, event starts 19:00, resume at 19:05.
- On wake, the daemon scans for occurrences that started while asleep and were
  missed.
- An occurrence is reported if its start is within
  `missed_event_window_minutes` (default 30) of "now"; older misses are
  silently dropped — even if the event is still ongoing.
- **One notice per missed occurrence, never a reminder replay.** Occurrences
  that started in the past never have their individual reminders replayed;
  they are folded into the single missed notice (title = event title, body
  `Bắt đầu lúc HH:MM · location`).
- Missed notices are deduplicated across restarts via the `-1` sentinel log
  key.

## Scheduler Daemon (Phase 3 — implemented)

- Console script `scheduled` → entry point `hyprschedule.scheduler:main`
  (`--once` test mode, `-v/--verbose`; exit 0 on clean shutdown, 1 on error).
- **No polling.** Each loop cycle: fire due notifications, then compute the
  next deadline — `occurrence_start - minutes_before` from
  `repo.get_occurrences(now, now + 60 days)` — and sleep exactly until it
  (recompute horizon bounds the sleep). The sleep is interrupted by SIGUSR1
  (reload) or SIGTERM/SIGINT (graceful stop) via `asyncio.Event`.
- **Deadline semantics**: a reminder whose deadline has already passed fires
  at the next wake (catch-up) as long as the occurrence has not started yet;
  occurrences that already started are handled by the missed-event path, so
  a reminder is never fired after the event began.
- **Event status**: occurrences of events with status `done` or `cancelled`
  are excluded from deadline collection, due-reminder firing and missed-event
  notices (`_is_remindable`); only `active` events can ever notify.
- Range membership for reminder discovery is bounded by the largest stored
  reminder offset (`repo.max_reminder_offset()`); recurrence-exception
  expansion semantics are the engine's own (modified occurrences are found by
  their original start, and their deadline is computed from the modified
  start).
- **Notifications** (`hyprschedule.scheduler.Notifier`): `notify-send` always
  best-effort (`-u critical` when the event priority is `critical`); sound
  via `pw-play <sound_file>` only when the reminder `sound` flag AND
  `[notification] sound` AND `[notification] sound_file` (non-empty) all
  hold. A missing binary or failed subprocess is logged, never fatal.
  Title/body format (Vietnamese): `Sắp tới · 15 phút` /
  `Dạy 12A1\n19:00 - 19:45 · A203`; at 0 minutes `Bắt đầu ngay`.
- **Pid file**: `$XDG_RUNTIME_DIR/hyprschedule/scheduled.pid` written while
  the daemon runs, removed on exit. `signal_running_daemon()` reads it and
  sends SIGUSR1; `schedctl add/edit/delete` call it best-effort after a
  successful write, so a running daemon reloads immediately while an absent
  daemon never breaks the CLI.
- **systemd**: user unit shipped in `systemd/hyprschedule.service`
  (`Type=simple`, `ExecStart=scheduled`, `ExecReload=/bin/kill -USR1
  $MAINPID`, `Restart=on-failure`). The repository never installs or enables
  it; the README documents the `systemctl --user` commands.
- The daemon uses only sync `sqlite3` calls inside the event loop (local DB,
  bounded work per cycle; zero runtime dependencies, no async driver).

## CLI Architecture

- `argparse`-based, no third-party CLI framework.
- Console script `schedctl` → entry point `hyprschedule.cli:main`.
- `main` returns an `int` exit code:
  - `0` — success
  - `1` — core failure (config, DB, internal errors)
  - `2` — usage error (invalid input; argparse-level errors raise
    `SystemExit(2)`)
  - `3` — conflict detected and `--force` not given (nothing saved)
  - `4` — referenced event does not exist (`show`/`edit`/`delete`)
- Command handlers live in `src/hyprschedule/commands.py` (thin wrappers over
  the Phase 1 engine — no ad-hoc SQL, no duplicated validation/recurrence
  logic). Formatting lives in `src/hyprschedule/formatter.py` (single place
  for both human text and JSON contracts).
- **Testable clock**: `src/hyprschedule/clock.py` exposes a module-level
  `clock` singleton (`clock.now()` → aware UTC). Tests pin it with
  `clock.set(aware_dt)` and restore with `clock.set(None)` so "today",
  "tomorrow", "week" and "next" are deterministic.
- Implemented subcommands: `doctor` (Phase 0), `today`, `tomorrow`, `week`,
  `next`, `add`, `edit`, `delete`, `show`, `search` (Phase 2), `eww`
  (Phase 4), `lock` (Phase 5).
- `add` flags: `--title` (required), `--description`, `--location`, `--type`,
  `--priority`, `--privacy`, `--timezone`, `--date` (one-time), `--start`,
  `--end`, `--repeat weekly|none`, `--weekday mon,tue,...`,
  `--from DATE`, `--until DATE`, `--remind MIN,MIN,...`, `--force`.
  `--weekday`/`--until` require `--repeat weekly`.
- **Cross-midnight semantics (DECISION)**: an `--end` wall-clock time
  *earlier* than `--start` belongs to the following calendar day
  (`--start 23:00 --end 01:00` → ends next day 01:00). An end equal to the
  start is invalid (zero-length event rejected by the backend).
- **Conflict behavior**: `add`/`edit` check the *proposed* event against
  existing occurrences (recurring series are expanded, bounded by
  `CONFLICT_HORIZON_DAYS = 30`); on conflict the overlapping occurrences are
  printed to stderr and the command exits `3` without saving. `--force`
  saves anyway. `edit` excludes the event itself (`exclude_event_id`), so an
  event never conflicts with itself.
- **`next`**: first occurrence with `start >= now`, bounded by
  `NEXT_HORIZON_DAYS = 30`; returns the event with its occurrence times.
- **`search`**: case-insensitive (ASCII) substring match over
  title/description/location via `repository.search_events` (SQL `LIKE` with
  `ESCAPE '\'`).
- `delete` is direct and immediate (Phase 1 hard-delete semantics); `show`
  prints full details or the event JSON.

## Phase 2 JSON contracts (implemented)

Stable machine-readable output. All timestamps are ISO-8601 **with an
explicit offset**, expressed in the event's own timezone. JSON is written to
stdout only; errors and diagnostics go to stderr.

Occurrence object (used in `today`/`tomorrow`/`week`/`next`):

```json
{
  "event_id": 1, "title": "Dạy 12A1", "description": "", "location": "A203",
  "start": "2026-08-17T07:00:00+07:00", "end": "2026-08-17T07:45:00+07:00",
  "timezone": "Asia/Ho_Chi_Minh", "event_type": "other",
  "priority": "normal", "privacy": "public", "status": "active",
  "recurring": false
}
```

- `schedctl today --json` / `tomorrow --json` / `week --json`:
  `{"range": {"start": ..., "end": ...}, "events": [occurrence, ...]}`
  (range = local day for today/tomorrow, Monday 00:00 → next Monday 00:00
  for week).
- `schedctl next --json`: `{"event": occurrence | null}`.
- `schedctl show --json`: event object with `id`, `rrule`,
  `recurring`, `reminders` (sorted offsets, minutes), `created_at`,
  `updated_at` added.
- `schedctl search --json`: `{"query": "...", "events": [event, ...]}`.
- `schedctl doctor`: unchanged Phase 0 report.

## Eww widget — `schedctl eww` (Phase 4, implemented)

`src/hyprschedule/eww.py` builds one JSON document describing the current
moment of the schedule. **Eww renders this JSON only** — no recurrence
expansion, no countdown math, no timezone handling, no SQLite access in yuck
or scss. The payload contract is stable:

```json
{
  "generated_at": "2026-08-13T19:45:00+07:00",
  "timezone": "Asia/Ho_Chi_Minh",
  "date": {"iso": "2026-08-13", "weekday": "Thứ Năm", "display": "Thứ Năm · 13/08"},
  "now": {"time": "19:45"},
  "current": {
    "event_id": 1, "title": "Dạy 12A1", "location": "A203",
    "start": "2026-08-13T19:30:00+07:00", "end": "2026-08-13T20:15:00+07:00",
    "start_display": "19:30", "end_display": "20:15",
    "event_type": "class", "priority": "high",
    "remaining_seconds": 1800, "remaining_display": "30 phút",
    "progress": 0.333, "progress_pct": 33, "progress_bar": "███░░░░░░░"
  },
  "next": {
    "event_id": 2, "title": "Họp tổ", "location": "Phòng họp",
    "start": "2026-08-13T21:00:00+07:00", "end": "2026-08-13T22:00:00+07:00",
    "start_display": "21:00", "end_display": "22:00",
    "event_type": "meeting", "priority": "high",
    "starts_in_seconds": 4500, "starts_in_display": "1 giờ 15 phút"
  },
  "events": [
    {"event_id": 2, "title": "Họp tổ", "location": "Phòng họp",
     "start": "...", "end": "...", "start_display": "21:00", "end_display": "22:00",
     "event_type": "meeting", "priority": "high", "state": "next"}
  ],
  "free_time": {
    "start": "2026-08-13T20:15:00+07:00", "end": "2026-08-13T21:00:00+07:00",
    "start_display": "20:15", "end_display": "21:00",
    "duration_minutes": 45, "duration_display": "45 phút"
  },
  "tomorrow": {"count": 2, "display": "2 lịch"},
  "widget": {"show_free_time": true, "show_tomorrow_count": true}
}
```

Decisions:

- **States vocabulary** is `past | current | next | future`. The widget
  payload emits only `next` / `future` in `events`: the current occurrence has
  its own `current` section and is not repeated, past occurrences are omitted.
- **Current event**: the occurrence active at `now` (half-open `[start,
  end)`), latest start wins. The lookup window reaches back by the longest
  stored duration so cross-midnight events running at 00:15 are found.
- **Next event**: first occurrence with `start >= now` within
  `NEXT_HORIZON_DAYS = 30` (same horizon as `schedctl next`).
- **Progress**: `progress` is clamped to `[0, 1]` and rounded to 3 decimals;
  `progress_pct` is the integer 0–100; `progress_bar` is a fixed 10-cell
  `█`/`░` string so the widget never concatenates numeric widths.
- **Countdowns** use `format_duration`: `<60s` → "N giây", `<1h` → "N phút",
  else "H giờ" or "H giờ M phút", floored so text never drifts between
  refreshes.
- **Free time**: exactly one rule — the next gap of at least
  `schedule.min_free_minutes` (default 15) inside the `[day_start, day_end)`
  window, starting at or after `now`; `null` when none (or after day end).
  Timestamps are expressed in the configured schedule timezone. No
  productivity heuristics.
- **`events`** holds today's remaining occurrences in chronological order,
  truncated to `widget.max_events` (default 6) **backend-side**.
- **`tomorrow.count`** counts tomorrow's expanded occurrences (recurrence
  exceptions respected).
- **Performance**: queries are bounded — today's window, the 30-day horizon
  and a max-duration window around `now`. `get_occurrences` pre-filters
  one-time events in SQL (`_events_overlapping`) and `max_duration()` is
  computed in SQL (`strftime('%s')` integer seconds), so a 30-second poll
  touches only relevant rows.
- **Command contract**: `schedctl eww` writes the JSON document to stdout
  only; errors go to stderr; fatal failures exit `1`. The command never
  modifies the database.

### Eww configuration (project-owned)

- `eww/eww.yuck` + `eww/eww.scss` ship in the repository and are used via
  `eww --config <repo>/eww ...` — the user's `~/.config/eww` is never
  modified.
- `defpoll schedule` runs `schedctl eww` every `refresh_seconds` (30s default,
  hardcoded `:interval "30s"` in yuck — the config key documents the
  intended value; the widget polls, no daemon is involved). A separate
  `defpoll time` shows `date +%H:%M` every second.
- Window `hyprschedule`: top-right, 380px wide, `:stacking "bg"`,
  `:exclusive false`, `:focusable "none"` (Wayland; X11 users swap
  `:exclusive` for `:reserve` + `:wm-ignore true` — documented in README).
- Null-safe rendering uses documented yuck features only: safe access `?.`
  and elvis `?:` (`(schedule.current?.title ?: "")`), `arraylength`,
  `for event in schedule.events`, and the `:visible` attribute on built-in
  widgets. Custom widgets declare their arguments (`[?show]`); eww silently
  ignores undeclared attributes, so `:visible` is always forwarded as a
  declared argument.
- `eww` is **not** a test dependency: `tests/test_eww_config.py` statically
  verifies the files exist, parentheses balance, every `schedule.*` yuck path
  resolves against a real payload, and every emitted `type-*` /
  `priority-*` class is styled in SCSS.

## Hyprlock lockscreen — `schedctl lock` (Phase 5, implemented)

`src/hyprschedule/lock.py` builds the plain-text lines for a hyprlock dynamic
label. hyprlock only executes `schedctl lock` (every 30s) and renders the
returned text — no SQLite, no recurrence, no timezone, no privacy logic in the
hyprlock config.

- **Output**: plain text only, to stdout. No JSON, no ANSI escapes, no
  markup (event titles are emitted literally, so a title like
  `<span size="999999">test</span>` renders as text), no debug logging.
  Diagnostics go to stderr; a fatal failure prints the safe fallback
  `Không thể tải lịch` on stdout and returns exit code 1. The command never
  mutates the database and is safe to run repeatedly.
- **Layout** (compact, readable within ~2 seconds): current local time
  (`HH:MM`), then at most `lockscreen.max_events` (default 2) event blocks —
  `ĐANG DIỄN RA` (title, `start → end`, location, `còn <duration>`) and
  `TIẾP THEO` (today's next occurrence: `HH:MM · title`, location,
  `sau <duration>`). Countdowns reuse the Phase 4 `eww.format_duration`, so
  wording is shared project-wide.
- **Empty states**: when nothing is current and nothing remains today,
  `Không còn lịch hôm nay.` when tomorrow still has visible events, else
  `Không có lịch sắp tới.` Placeholder tokens (`None`, `null`, `[]`, `{}`,
  tracebacks) never reach the label.
- **Tomorrow count**: footer `Ngày mai · N lịch` when
  `[lockscreen] show_tomorrow_count` (default true) and N > 0. Counts actual
  expanded occurrences (recurrence exceptions respected); hidden events never
  count.
- **Privacy behavior applied by the backend (never by hyprlock)**:

  | privacy | lock screen shows |
  | ------- | ----------------- |
  | `public`  | full details (title, times, location) |
  | `private` | `Có lịch cá nhân` + times, no title/location — unless `[lockscreen] show_private = true`, then full details |
  | `hidden`  | nothing — absent from current/next and never counted in the tomorrow footer |

  The lockscreen is treated as publicly visible: hidden details, private
  descriptions, paths, SQLite errors and tracebacks are never displayed.
- **Status**: `done`/`cancelled` events are excluded with the shared Phase 3
  semantics (`scheduler._is_remindable`) — one status interpretation for the
  whole project.
- **Current event**: the occurrence active at `now` (half-open `[start,
  end)`), latest start wins, window reaches back by the longest stored
  duration so cross-midnight events running at 00:15 stay current. **Next
  event**: first visible occurrence today with `start >= now` (no unbounded
  expansion; the occurrence engine bounds every query).
- **Config**: `[lockscreen] max_events = 2`, `show_private = false`,
  `show_tomorrow_count = true` (defaults; see Configuration Model).

### Hyprlock snippet (project-owned)

- `hyprlock/schedule.conf` ships in the repository: one `label` block running
  `text = cmd[update:30000] schedctl lock` (30-second refresh, polling only —
  no IPC). hyprlock has **no** include/source directive (unlike
  hyprland.conf), so the README documents copying the block into
  `~/.config/hypr/hyprlock.conf` manually. The user's config is never
  modified by the project.
- `tests/test_hyprlock_config.py` statically validates the snippet (known
  label keys only, balanced braces, poll interval); runtime validation is
  possible when a Wayland session exists (`hyprlock -c hyprlock/schedule.conf`
  parses and runs without config errors).

## Add/Edit UI — `schedctl editor-data` / `editor-save` (Phase 6, implemented)

The Eww editor is a presentation layer only. `src/hyprschedule/editor.py`
owns the JSON form contract; the eww side (`eww/bin/hyprschedule_editor.py`)
is a thin driver that never interprets user data.

- **`schedctl editor-data [ID]`** — read-only, JSON on stdout only. With no
  ID: a fresh *add* form (today in the config timezone, start = next
  half-hour — or 07:00 the next day when the half-hour falls after midnight —
  default duration 45 minutes, type `class`, priority `normal`, privacy
  `public`, reminder offsets from `[notification] default_reminders`).
  With an ID: the *edit* form for that event (exit 4 when missing). Both
  never mutate the database and are safe to run repeatedly.
- **`schedctl editor-save`** — mutating entry point. Reads exactly **one**
  JSON document from stdin (`mode: "add" | "edit"`), translates it through
  `editor.add_args` / `editor.edit_args` into the exact argument shapes of
  `cmd_add` / `cmd_edit`, then delegates — validation, conflict detection
  (exit 3), `--force` semantics and the SIGUSR1 daemon-reload signal are
  therefore *identical* to the CLI paths. Nothing is bypassed.
- **Form document** (both directions): `id`, `mode`, `title`, `date`
  (YYYY-MM-DD), `start`/`end` (HH:MM, cross-midnight via the Phase 1
  `_end_datetime` rule), `location`, `description`, `event_type`
  (`class|meeting|work|personal|deadline|task|other`), `priority`
  (`low|normal|high|critical`), `privacy` (`public|private|hidden`),
  `reminders` (non-negative minutes; sorted, duplicates merged — the backend
  `replace_reminders` stays authoritative), `recurrence` (`{type:
  "none"|"weekly", weekdays: ["mon".."sun"], until: "YYYY-MM-DD"|null}`),
  `force` (bool, only ever set by the explicit conflict button). Recurrence
  is deliberately restricted to `none|weekly` — the implemented backend
  subset (Phase 1); nothing else is exposed.
- **Edit semantics**: editing a recurring event edits the whole series
  (backend semantics); converting `weekly → none` clears the rrule,
  `none → weekly` builds it from the form's weekdays. Delete reuses
  `schedctl delete <id>` (numeric id) and removes the entire series — the UI
  labels this explicitly (`Xóa toàn bộ chuỗi lịch lặp?`).
- **Eww driver** (`eww/bin/hyprschedule_editor.py`): subcommands
  `open-add`, `open-edit <id>`, `prompt <var> <label>` (rofi, falling back to
  yad — stock eww has no text-input widget), `set` (option buttons;
  allowlisted vars only), `toggle-reminder`/`toggle-weekday`, `save [force]`,
  `delete`, `close`. All subprocess calls use argv lists (no `shell=True`);
  `schedctl` and `eww` resolve from `$PATH` unless `HYPRSCHEDULE_EWW` /
  `HYPRSCHEDULE_SCHEDCTL` override them. On success the driver closes the
  editor window and runs `eww reload` so the widget's `schedule` defpoll
  re-polls immediately; the daemon already reloaded via the editor-save path.
- **State machine** (eww vars, documented in `eww/eww.yuck`): `idle`,
  `editing`, `saving`, `success`, `error`, `conflict`, `delete-confirm`.
  Conflicts never auto-save: the `conflict` state offers `Lưu vẫn cứ lưu`
  (sets `force`) and `Quay lại` (dismiss). Errors are inline labels, never
  tracebacks.
- **Security posture**: user values are data end-to-end — JSON over stdin,
  argv lists, no shell interpolation, no `eval`. Tests cover hostile titles
  (`$(...)`, backticks, quotes, `;`, `&`, markup, Unicode) round-tripping
  verbatim. `tests/test_editor_config.py` statically verifies every yuck
  `onclick` command has a dispatch entry and every `prompt`/`set` target is
  allowlisted.
- **Validation**: `tests/test_editor.py` (56 tests: contract, mutations,
  conflicts, invalid input, security, daemon-reload signals) +
  `tests/test_editor_config.py` (19 static tests). Runtime GUI validation
  requires eww itself, which is an optional desktop dependency (doctor
  reports it missing without breaking anything).

## Configuration Model

- File: `$XDG_CONFIG_HOME/hyprschedule/config.toml`
  (fallback `~/.config/hyprschedule/config.toml`).
- Parsed with stdlib `tomllib`.
- **Missing file → defaults are used** (file is never created by the app).
- **Invalid values → `ConfigError`** raised with a clear message; the CLI
  prints it to stderr and exits 1. Never a raw traceback for user config
  errors.
- **Unknown keys are ignored** for forward compatibility.

Defaults (exact):

```toml
timezone = "Asia/Ho_Chi_Minh"

[schedule]
day_start = "06:00"
day_end = "23:00"
min_free_minutes = 15

[widget]
max_events = 6
show_free_time = true
show_tomorrow_count = true
refresh_seconds = 30

[lockscreen]
max_events = 2
show_private = false
show_tomorrow_count = true

[notification]
default_reminders = [15, 5, 0]
sound = true
sound_file = ""   # path to a sound played by pw-play; empty disables sound
missed_event_window_minutes = 30

[recurrence]
skip_classes_on_holidays = true
```

## Logging

- stdlib `logging`.
- Logger name: `hyprschedule` (all modules log under it).
- Default level: `WARNING` (no debug spam in normal CLI use).
- `--verbose` sets level `DEBUG`.
- Handler: stderr.

## Error Handling

- User-facing configuration errors raise `ConfigError`; the CLI catches it,
  prints the message to **stderr**, and exits **1**.
- **Missing optional desktop dependencies must never crash the backend.**
  `schedctl doctor` reports them as warnings (`✗`), explains the consequence,
  and still exits `0` for core health unless a core check failed (e.g. DB
  cannot initialize).
- Core failures exit `1`; optional warnings do not.

## Testing Strategy

- pytest.
- `tmp_path` + `monkeypatch` of `XDG_*` env vars for all path/config/db tests.
- Deterministic; **no network**.
- Tests never touch the real `$HOME`.
- No real desktop session needed — doctor tests run core checks only and
  assert graceful handling of missing optional tools.
- Migration tests build an intermediate Phase 0 schema
  (`apply_migrations(conn, up_to=1)`), seed data, then upgrade to the head
  and assert data preservation — the real `schedule.db` is never touched.

## Packaging

- `pyproject.toml`, `src/` layout, hatchling backend.
- `requires-python >= 3.11`.
- **ZERO runtime dependencies** — `tomllib`, `zoneinfo`, `sqlite3` all from the
  standard library. Any future runtime dependency needs explicit justification.
- Dev extra: `pytest`.
- Console script: `schedctl = hyprschedule.cli:main`.
- Install/test workflow: `uv sync --extra dev`, `uv run pytest`,
  `uv run schedctl doctor`.

## Structural Deviations from plan.md

plan.md's expected structure lists every module up front. We deviate
**deliberately** to avoid empty dead files:

1. **Added `src/hyprschedule/doctor.py`** — doctor logic is substantial
   (runtime/version checks, path checks, DB init, migration status, timezone
   check, optional tool detection, exit-code logic). Keeping it out of `cli.py`
   preserves module boundaries.
2. **Phase 1 added modules beyond plan.md's list** (with technical reasons):
   - `errors.py` — domain error hierarchy (`EventNotFound`, `InvalidEvent`,
     `InvalidRecurrence`, `InvalidReminder`, `InvalidException`); also the
     home of `ConfigError` per plan.md's structure. Note: `ConfigError`
     currently lives in `config.py` (Phase 0); it is re-exported by name in
     `errors.py` for documentation consistency — CLI and tests keep importing
     from `config`.
   - `timeutil.py` — shared aware-datetime helpers (`ensure_aware`,
     `to_utc_iso`, `from_utc_iso`) used by repository, recurrence and
     conflicts so the UTC ISO-8601 storage format lives in exactly one place.
   - `repository.py` — `EventRepository`, the persistence boundary: all CRUD,
     occurrence expansion and exception/reminder access. No raw SQLite
     escapes to callers.
   - `conflicts.py` — `find_conflicts` (overlap grouping, half-open
     intervals, `exclude_event_id`).
3. **Phase 2 added modules beyond plan.md's list**:
   - `commands.py` — one handler per CLI subcommand (plus `Context`,
     `build_context`, exit codes, cross-midnight/proposed-conflict logic).
     Kept out of `cli.py` so the parser stays declarative.
   - `formatter.py` — single place for human text and the JSON contracts.
   - `clock.py` — injectable time source so date-relative commands are
     testable.
4. **Phase 0 does NOT create** `scheduler.py`, `notifications.py`,
   `parser.py`, `hyprlock/`, `systemd/`, `assets/`, `install.sh`,
   `uninstall.sh`. These are deferred to their own phases (per plan.md:
   "Không nhất thiết phải tạo file chưa dùng ở Phase 0 nếu việc đó tạo code
   rỗng vô nghĩa"). Phase 4 **added** `eww.py` and the `eww/` widget
   directory; Phase 5 **added** `lock.py` and the `hyprlock/` snippet
   directory.

Current shipped modules (Phase 0 + 1 + 2 + 4 + 5): `__init__.py`, `cli.py`,
`commands.py`, `formatter.py`, `clock.py`, `config.py`, `paths.py`,
`database.py`, `migrations.py`, `models.py`, `doctor.py`, `errors.py`,
`timeutil.py`, `recurrence.py`, `repository.py`, `conflicts.py`, `eww.py`,
`lock.py`, `scheduler.py`.
Widget config: `eww/eww.yuck`, `eww/eww.scss`; lockscreen snippet:
`hyprlock/schedule.conf`. Tests: config, paths, database, migrations, models,
doctor, recurrence, repository, conflicts, cli (queries + crud), scheduler,
eww (payload), eww_config (static widget validation), lock, hyprlock_config
(static snippet validation).
