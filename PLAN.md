# HyprSchedule — Plan

## Project Goals

HyprSchedule is a local-first personal schedule system for Arch Linux + Hyprland.

The user can manage:

- classes (lịch học)
- timetables (thời khóa biểu)
- teaching schedules (lịch dạy)
- work schedules (lịch làm việc)
- meetings (cuộc họp)
- deadlines
- personal events (lịch cá nhân)
- weekly recurring schedules (lịch lặp theo tuần)
- exact-time reminders (reminder theo thời gian chính xác)

The application must support:

- local storage (lưu lịch local)
- desktop display (hiển thị lịch trên desktop)
- hyprlock display (hiển thị lịch trên hyprlock)
- on-time notifications (báo notification đúng thời gian)
- reminder sound (phát âm thanh reminder)
- current-event display
- next-event display
- countdown
- recurring events
- fully offline operation

## Target Environment

| Item          | Value                  |
| ------------- | ---------------------- |
| OS            | Arch Linux             |
| WM            | Hyprland               |
| Lockscreen    | hyprlock               |
| Widget        | Eww                    |
| Notification  | notify-send            |
| Sound         | PipeWire / pw-play     |
| Service mgr   | systemd --user         |
| Backend       | Python 3               |
| Database      | SQLite                 |
| Config        | TOML                   |
| Testing       | pytest                 |
| Default TZ    | Asia/Ho_Chi_Minh       |

## Phase Roadmap

Checkboxes are only checked when the phase's Definition of Done (below) is
fully satisfied, including passing tests.

### Phase 0 — Foundation

- [x] Phase 0 — Foundation
  - [x] Repository structure created
  - [x] Python packaging (`pyproject.toml`, src/ layout, hatchling)
  - [x] Configuration loading with defaults
  - [x] XDG path handling
  - [x] SQLite initialization
  - [x] Migrations mechanism
  - [x] Base models / enums
  - [x] Logging infrastructure
  - [x] CLI skeleton (`schedctl`)
  - [x] `schedctl doctor`
  - [x] pytest suite
  - [x] Tests pass

### Phase 1 — Event Engine

- [x] Phase 1 — Event Engine
  - [x] Event CRUD
  - [x] One-time events
  - [x] Recurring events
  - [x] Weekly recurrence
  - [x] Multiple weekdays
  - [x] Recurrence exceptions
  - [x] Reminders
  - [x] Conflict detection

### Phase 2 — CLI

- [x] Phase 2 — CLI
  - [x] `schedctl today`
  - [x] `schedctl tomorrow`
  - [x] `schedctl week`
  - [x] `schedctl next`
  - [x] `schedctl add`
  - [x] `schedctl edit`
  - [x] `schedctl delete`
  - [x] `schedctl show`
  - [x] `schedctl search`
  - [x] JSON output (`--json`)

### Phase 3 — Scheduler Daemon

- [x] Phase 3 — Scheduler Daemon
  - [x] asyncio scheduler (`scheduled`)
  - [x] Exact reminders
  - [x] Notification log (dedup)
  - [x] `notify-send` integration
  - [x] `pw-play` sound
  - [x] Signal reload (SIGUSR1)
  - [x] systemd user service
  - [x] Suspend/resume recovery

### Phase 4 — Eww Read-only Widget

- [x] Phase 4 — Eww Read-only Widget
  - [x] Current event (NOW)
  - [x] Next event (NEXT)
  - [x] Today's events (TODAY)
  - [x] Countdown
  - [x] Free time (FREE TIME)
  - [x] Tomorrow count (TOMORROW COUNT)

### Phase 5 — Hyprlock

- [x] Phase 5 — Hyprlock
  - [x] Lock formatter (`schedctl lock`)
  - [x] public/private/hidden privacy behavior
  - [x] Dynamic refresh

### Phase 6 — Add/Edit UI

- [x] Phase 6 — Add/Edit UI
  - [x] Create events
  - [x] Edit events
  - [x] Delete events
  - [x] Reminders
  - [x] Recurrence
  - [x] Privacy
  - [x] Priority

### Phase 7 — UX Polish

- [ ] Phase 7 — UX Polish
  - [ ] Animations
  - [ ] Progress indicators
  - [ ] Icons
  - [ ] Keyboard navigation
  - [ ] Empty/error states

### Phase 8 — Natural Language Input

- [ ] Phase 8 — Natural Language Input
  - [ ] Vietnamese deterministic parser (no LLM API)
  - [ ] Parse dates/times like "mai 7h", "thứ 6 14h"
  - [ ] Parse recurring patterns like "thứ 2 hàng tuần"
  - [ ] Parse reminder offsets like "nhắc trước 30 phút"
  - [ ] Parser output feeds `schedctl add`

### Phase 9 — ICS

- [ ] Phase 9 — ICS
  - [ ] ICS import
  - [ ] ICS export

Google Calendar sync is considered only after ICS support exists.

## Dependency Graph

```
Phase 0 (Foundation)
  ├───> Phase 1 (Event Engine)      depends on 0
  ├───> Phase 2 (CLI)               depends on 1
  ├───> Phase 3 (Scheduler Daemon)  depends on 1 + 2
  ├───> Phase 4 (Eww widget)        depends on 2
  └───> Phase 5 (Hyprlock)          depends on 2

Phase 1 ──> Phase 8 (Natural Language Input)   depends on 1
Phase 1 ──> Phase 9 (ICS)                      depends on 1
Phase 2 ──> Phase 6 (Add/Edit UI)              depends on 2 + 1
Phase 6 ──> Phase 7 (UX Polish)                depends on 6
```

Summary:

| Phase | Depends on |
| ----- | ---------- |
| 0 | — |
| 1 | 0 |
| 2 | 1 |
| 3 | 1 + 2 |
| 4 | 2 |
| 5 | 2 |
| 6 | 1 + 2 |
| 7 | 6 |
| 8 | 1 |
| 9 | 1 |

A phase must not be started until every dependency's Definition of Done is
satisfied.

## Definition of Done

### Phase 0

- [ ] Python package installs/runs correctly
- [ ] `schedctl` executable works
- [ ] XDG path handling works
- [ ] Config loads correctly
- [ ] SQLite initializes correctly
- [ ] Migration mechanism works
- [ ] Base models exist
- [ ] Logging exists
- [ ] `schedctl doctor` runs
- [ ] Tests pass
- [ ] Docs reflect actual implementation
- [ ] No later-phase features were accidentally implemented

### Phase 1

- [x] Event CRUD is complete and tested
- [x] One-time and recurring events stored/expanded correctly
- [x] Weekly recurrence and multiple weekdays work
- [x] Recurrence exceptions (cancel/modify one occurrence) work
- [x] Reminders can be attached to events
- [x] Conflict detection reports overlapping events
- [x] No later-phase features were accidentally implemented

### Phase 2

- [x] All `schedctl` commands (`today`, `tomorrow`, `week`, `next`, `add`, `edit`, `delete`, `show`, `search`) work
- [x] `--json` output follows the documented contract
- [x] Human-readable output is clear and localized (Vietnamese OK)
- [x] Exit codes follow the CLI contract
- [x] Tests pass

### Phase 3

- [x] `scheduled` daemon runs as systemd user service
- [x] Exact reminders fire at the right time without polling
- [x] Notification log prevents duplicates after restart
- [x] SIGUSR1 reload works
- [x] Suspend/resume emits delayed notifications within `missed_event_window_minutes`
- [x] `notify-send` and `pw-play` invoked correctly
- [x] Done/cancelled events never produce reminders (status filter)
- [x] Tests pass (no real desktop session needed)

### Phase 4

- [x] Eww widget renders data from `schedctl eww` JSON only
- [x] NOW, NEXT, TODAY, FREE TIME, TOMORROW COUNT sections work
- [x] Refresh honors `refresh_seconds` (defpoll interval)
- [x] No business logic in Eww (yuck/scss do not compute recurrence/reminders)
- [x] Tests pass

### Phase 5

- [x] hyprlock shows data from `schedctl lock` plain text only
- [x] Privacy behavior: public → full details, private → "Có lịch cá nhân" (or full when `show_private = true`), hidden → nothing (never counted)
- [x] Dynamic refresh works (`cmd[update:30000]`, polling only)
- [x] No business logic in hyprlock config
- [x] Project-owned snippet (`hyprlock/schedule.conf`); user config never modified
- [x] Tests pass

### Phase 6

- [x] Create/edit/delete via UI works and writes through backend only
- [x] Reminders, recurrence, privacy, priority editable
- [x] Changes trigger daemon reload
- [x] Tests pass

### Phase 7

- [ ] Animations, progress, icons, keyboard navigation, empty/error states implemented
- [ ] No regressions in phases 4–6
- [ ] Tests pass

### Phase 8

- [ ] Vietnamese parser handles the documented example sentences deterministically
- [ ] Parser never calls an LLM/network API
- [ ] Parsed results feed `schedctl add`
- [ ] Tests pass

### Phase 9

- [ ] ICS import and export round-trip correctly
- [ ] Recurring events preserved in ICS
- [ ] Tests pass

## Out of Scope (Do Not Implement Now)

- Google Calendar sync
- ICS (until Phase 9)
- AI API calls / LLM parsing
- Snooze / notification actions
- Semester profiles / holidays logic
- Waybar integration
- Web server / REST API / cloud storage
- Electron / React / Node.js backend
- Docker / Kubernetes
- Authentication / user accounts / telemetry / analytics
