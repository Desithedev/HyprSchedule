# HyprSchedule

A local-first personal schedule system for **Arch Linux + Hyprland**.

HyprSchedule manages classes, timetables, teaching, work, meetings, deadlines,
personal events, and weekly recurring schedules — all stored locally in SQLite,
with exact-time reminders, desktop display, and hyprlock integration.

## Tech Stack

| Component  | Choice                                                       |
| ---------- | ------------------------------------------------------------ |
| Backend    | Python 3 (>= 3.11), standard library only                    |
| Database   | SQLite (via stdlib `sqlite3`)                                |
| CLI        | `schedctl` (argparse, console script)                        |
| Config     | TOML (`config.toml`, stdlib `tomllib`)                       |
| Desktop UI | Eww widget (`eww/` in this repo, Phase 4)                    |
| Lockscreen | hyprlock (`hyprlock/` snippet, Phase 5)                     |
| Daemon     | `scheduled` asyncio daemon (Phase 3)                          |
| Tests      | pytest                                                        |
| Timezone   | `Asia/Ho_Chi_Minh` (default, configurable)                   |

Zero runtime dependencies — `tomllib`, `zoneinfo`, and `sqlite3` all come from
the Python standard library. Everything works fully offline.

## Quickstart

```bash
# install with dev dependencies
uv sync --extra dev

# run the test suite
uv run pytest

# run diagnostics
uv run schedctl doctor
```

`uv run schedctl doctor` checks the Python runtime, XDG config/data directories,
SQLite database initialization, migration status, timezone availability, and
the optional desktop tools (`notify-send`, `pw-play`, `eww`, `hyprlock`,
`systemctl`, `scheduled`). Missing optional desktop dependencies are reported
as warnings and never break the backend.

## CLI usage

```bash
# daily / weekly views (all support --json)
uv run schedctl today
uv run schedctl tomorrow
uv run schedctl week
uv run schedctl next

# create events
uv run schedctl add --title "Dạy 12A1" --date 2026-08-17 \
  --start 07:00 --end 07:45 --location A203 --remind 15,5,0
uv run schedctl add --title "Dạy 11A3" --start 09:00 --end 09:45 \
  --repeat weekly --weekday mon,wed,fri --from 2026-08-17 --until 2026-12-31

# edit / delete / show / search
uv run schedctl edit 1 --location B203 --force
uv run schedctl delete 1
uv run schedctl show 1
uv run schedctl search "12A1"

# machine-readable output (stable JSON contracts, see ARCHITECTURE.md)
uv run schedctl today --json

# desktop widget payload (polled by Eww, read-only)
uv run schedctl eww | jq .
```

Key behaviors:

- `--start 23:00 --end 01:00` crosses midnight (ends next day 01:00).
- Adding/editing an event that overlaps an existing one prints the conflicts
  and exits with code `3` without saving; pass `--force` to save anyway.
- Exit codes: `0` success, `1` core failure, `2` usage error, `3` conflict,
  `4` event not found.

## Scheduler daemon (`scheduled`)

The asyncio daemon fires exact-time reminders (`notify-send`, optional
`pw-play` sound) and never polls: it computes the next reminder deadline and
sleeps exactly until then. Notifications are deduplicated in the
`notification_log` table, so restarts never double-fire. After a suspend/
resume, events that started during the downtime get one missed notice each
(within `missed_event_window_minutes`, default 30).

```bash
# run it once (test/smoke mode): fire due notifications and exit
uv run scheduled --once

# start the daemon manually (or use systemd below)
uv run scheduled
```

Reminder deadlines use the event's own timezone; a 19:00 event with reminders
`15,5,0` notifies at 18:45, 18:55 and 19:00. Events with status `done` or
`cancelled` never produce reminders or missed-event notices.

systemd user service (unit file: `systemd/hyprschedule.service` — install it
as `~/.config/systemd/user/hyprschedule.service` and adjust `ExecStart` if
`scheduled` is not on your `PATH`):

```bash
systemctl --user daemon-reload
systemctl --user enable --now hyprschedule.service
systemctl --user status hyprschedule.service
systemctl --user reload hyprschedule.service   # SIGUSR1 -> recompute deadlines
journalctl --user -u hyprschedule.service -f   # logs
```

`/usr/bin/kill -USR1 $(pidof scheduled)` also reloads. `schedctl add`, `edit`
and `delete` signal a running daemon automatically (best-effort — an absent
daemon never breaks the CLI; the SQLite database remains the source of
truth).

Notification details:

- Missing `notify-send`/`pw-play` is logged, never fatal.
- Sound plays only when the reminder's `sound` flag, `[notification] sound`
  and `[notification] sound_file` are all set (default: no sound file).
- Critical-priority events use `notify-send -u critical`.

## Eww desktop widget

The read-only widget shows the current event (with progress bar), the next
event (with countdown), today's remaining schedule, the next free period and
tomorrow's occurrence count. Eww renders JSON from `schedctl eww` only — all
recurrence, timezone, progress and free-time computation happens in the
backend. The widget files live in this repository (`eww/eww.yuck`,
`eww/eww.scss`) so your `~/.config/eww` is never touched.

Requirements: [eww](https://github.com/elkowar/eww) (>= 0.5) and `schedctl`
on `PATH`.

```bash
# start the eww daemon with this repo's config and open the widget
eww --config ~/Projects/HyprSchedule/eww daemon
eww --config ~/Projects/HyprSchedule/eww open hyprschedule

# reload after editing the config files
eww --config ~/Projects/HyprSchedule/eww reload
```

The widget polls `schedctl eww` every 30 seconds (`refresh_seconds` in
config) and the clock every second. To pin it to the primary monitor:

```bash
eww --config ~/Projects/HyprSchedule/eww open hyprschedule --screen 0
```

Limitations:

- Window properties target Wayland (`:stacking "bg"`, `:exclusive false`,
  `:focusable "none"`). On X11 replace `:exclusive` with `:reserve` and add
  `:wm-ignore true` in `eww/eww.yuck`.
- `schedctl` must be reachable from the eww daemon's environment (same user).
- If `schedctl eww` fails (e.g. config error) the widget shows a small error
  state instead of crashing.
- Free-time detection uses one rule: gaps of at least `min_free_minutes`
  (default 15) inside the `day_start`–`day_end` window. Tune it under
  `[schedule]` in `config.toml`.

## Add/Edit UI (`hyprschedule-editor` window)

The same eww config ships an add/edit window (`+` button in the widget
header; click any event row to edit). Every mutation is written by the
backend — the eww side only formats and prompts:

- reads forms via `schedctl editor-data` (read-only JSON),
- writes via `schedctl editor-save` (one JSON document on stdin) — reusing
  the exact `add`/`edit` validation, conflict detection (exit 3) and daemon
  reload signal,
- deletes via `schedctl delete <id>` after an explicit confirmation
  (deleting a recurring event removes the whole series — the UI labels it
  `Xóa toàn bộ chuỗi lịch lặp?`; editing a recurring event applies to the
  whole series too).

Fields: title, date, start/end (cross-midnight allowed), location,
description, type, priority, privacy, reminders (60/30/15/10/5/0 minutes),
recurrence (`Một lần` or `Hằng tuần` with weekday toggles and an optional
`Lặp đến` end date). Conflicts are never auto-saved: the form shows the
conflicting events and asks explicitly (`Lưu vẫn cứ lưu` = save with
`--force` semantics, `Quay lại` = discard).

Eww has no text-input widget, so free-text fields open a prompt — [rofi](
https://github.com/davatorium/rofi) with [yad](https://github.com/v1cont/yad)
as fallback (the field stays disabled if neither is installed) — and no
keyboard event handling, so the editor is mouse-only. These are documented
stock-eww limitations.

```bash
# the editor is part of the same config; the driver must be on PATH too
eww --config ~/Projects/HyprSchedule/eww daemon   # already running from above
eww --config ~/Projects/HyprSchedule/eww open hyprschedule-editor
```

The driver script (`eww/bin/hyprschedule_editor.py`) never runs user values
through a shell: all subprocess calls use argv lists and forms travel as JSON
over stdin. `rofi`/`yad` are optional; their absence never crashes anything.
After a successful save/delete the window closes and the widget re-polls
immediately.

## Hyprlock lockscreen

The lockscreen shows a compact, privacy-aware schedule summary: current local
time, the current event (`ĐANG DIỄN RA`: title, `start → end`, location,
`còn <duration>`), the next event today (`TIẾP THEO`: `HH:MM · title`,
`sau <duration>`), and an optional `Ngày mai · N lịch` footer. All
computation happens in the backend — hyprlock only executes `schedctl lock`
and renders plain text.

```bash
# try it (read-only, safe to run anytime)
uv run schedctl lock
```

Example output:

```
19:30

ĐANG DIỄN RA
Dạy 12A1
19:00 → 19:45
A203
còn 15 phút

TIẾP THEO
20:00 · Họp tổ
sau 30 phút

Ngày mai · 4 lịch
```

Privacy (backend-enforced, never in the hyprlock config):

| privacy | lockscreen shows |
| ------- | ---------------- |
| `public`  | full title/location/times |
| `private` | `Có lịch cá nhân` + times (no title/location) — full details if `[lockscreen] show_private = true` |
| `hidden`  | nothing — never shown, never counted in `Ngày mai · N` |

Events with status `done`/`cancelled` are never shown. If nothing remains
today the label shows `Không còn lịch hôm nay.` (or `Không có lịch sắp tới.`
when tomorrow is empty too). Output is plain text only — no markup, no JSON,
no ANSI escapes — so event titles always render literally.

Configuration (`[lockscreen]`):

```toml
[lockscreen]
max_events = 2           # at most 2 event blocks (current + next)
show_private = false     # true: show private event details
show_tomorrow_count = true
```

Manual hyprlock integration — hyprlock has **no** include/source directive
(unlike `hyprland.conf`), so copy this `label` block into
`~/.config/hypr/hyprlock.conf` (snippet: `hyprlock/schedule.conf` in this
repo):

```conf
label {
    monitor =
    text = cmd[update:30000] schedctl lock

    font_size = 16
    position = -50, -100
    halign = right
    valign = top
    text_align = left
}
```

The label refreshes every 30 seconds (polling only — no IPC). `schedctl`
must be reachable from the hyprlock process (same user). `hyprlock` never
modifies your configuration or your schedule data; it only reads
`/usr/bin/schedctl lock` output. Runtime validation requires a Wayland
session: `hyprlock -c hyprlock/schedule.conf` parses and runs without config
errors when one exists.

## Roadmap

Phases 0–6 are implemented (foundation, event engine, CLI, scheduler daemon,
eww widget, hyprlock, add/edit UI); UX polish and later phases come after —
see [PLAN.md](PLAN.md) for the full roadmap and
[ARCHITECTURE.md](ARCHITECTURE.md) for technical decisions.

## Documentation

- [PLAN.md](PLAN.md) — project goals, phase roadmap, definitions of done
- [ARCHITECTURE.md](ARCHITECTURE.md) — technical decisions, schema, contracts
- [AGENTS.md](AGENTS.md) — rules for coding agents