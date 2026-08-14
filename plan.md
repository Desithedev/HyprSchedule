# ROLE

Bạn là senior Linux desktop engineer và Python engineer.

Bạn đang làm việc trong OpenCode trên Arch Linux + Hyprland.

Nhiệm vụ là xây dựng một ứng dụng local-first tên **HyprSchedule**.

Không được tự ý mở rộng scope. Không được implement các phase tương lai nếu chưa được yêu cầu.

---

# PROJECT GOAL

HyprSchedule là một hệ thống lịch cá nhân dành cho Arch Linux + Hyprland.

Người dùng có thể quản lý:

- lịch học
- thời khóa biểu
- lịch dạy
- lịch làm việc
- cuộc họp
- deadline
- lịch cá nhân
- lịch lặp theo tuần
- reminder theo thời gian chính xác

Ứng dụng phải có khả năng:

- lưu lịch local
- hiển thị lịch trên desktop
- hiển thị lịch trên hyprlock
- báo notification đúng thời gian
- phát âm thanh reminder
- hiển thị event đang diễn ra
- hiển thị event tiếp theo
- tính countdown
- hỗ trợ recurring event
- hoạt động offline hoàn toàn

---

# TARGET ENVIRONMENT

OS:

```text
Arch Linux
```

Window manager:

```text
Hyprland
```

Lockscreen:

```text
hyprlock
```

Desktop widget:

```text
Eww
```

Notification:

```text
notify-send
```

Sound:

```text
PipeWire / pw-play
```

Service manager:

```text
systemd --user
```

Backend:

```text
Python 3
```

Database:

```text
SQLite
```

Configuration:

```text
TOML
```

Testing:

```text
pytest
```

Default timezone:

```text
Asia/Ho_Chi_Minh
```

---

# CORE ARCHITECTURE

Kiến trúc mục tiêu:

```text
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

Backend là **single source of truth**.

Eww và hyprlock chỉ là frontend.

---

# CRITICAL ARCHITECTURE RULES

Không được đặt business logic vào Eww.

Không được đặt business logic vào hyprlock.

UI không được tự:

- tính recurrence
- tính reminder
- query SQLite trực tiếp
- detect conflict
- tính countdown phức tạp
- xử lý timezone
- sửa database trực tiếp

Mọi dữ liệu phải đi qua Python backend / `schedctl`.

Ví dụ:

```bash
schedctl today --json
schedctl next --json
schedctl eww
schedctl lock
```

---

# EXPECTED PROJECT STRUCTURE

Thiết kế project theo hướng:

```text
hyprschedule/
├── README.md
├── PLAN.md
├── ARCHITECTURE.md
├── AGENTS.md
├── pyproject.toml
├── install.sh
├── uninstall.sh
│
├── src/
│   └── hyprschedule/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── paths.py
│       ├── database.py
│       ├── migrations.py
│       ├── models.py
│       ├── recurrence.py
│       ├── scheduler.py
│       ├── notifications.py
│       ├── formatter.py
│       ├── conflicts.py
│       └── parser.py
│
├── tests/
│   ├── test_config.py
│   ├── test_database.py
│   ├── test_paths.py
│   └── test_models.py
│
├── eww/
│   ├── eww.yuck
│   └── eww.scss
│
├── hyprlock/
│   └── schedule.conf
│
├── systemd/
│   └── hyprschedule.service
│
└── assets/
    └── sounds/
```

Không nhất thiết phải tạo file chưa dùng ở Phase 0 nếu việc đó tạo code rỗng vô nghĩa.

Có thể điều chỉnh cấu trúc nếu có lý do kỹ thuật rõ ràng.

Nếu điều chỉnh, ghi lý do vào `ARCHITECTURE.md`.

---

# XDG PATHS

Phải tuân theo XDG Base Directory.

Config:

```text
$XDG_CONFIG_HOME/hyprschedule/config.toml
```

fallback:

```text
~/.config/hyprschedule/config.toml
```

Persistent data:

```text
$XDG_DATA_HOME/hyprschedule/
```

fallback:

```text
~/.local/share/hyprschedule/
```

Database:

```text
~/.local/share/hyprschedule/schedule.db
```

Runtime data nếu cần:

```text
$XDG_RUNTIME_DIR/hyprschedule/
```

Không lưu mutable user data bên trong repository.

---

# DATABASE DESIGN

Thiết kế schema đủ để roadmap sau này sử dụng.

## events

Tối thiểu cần cân nhắc:

```text
id
title
description
location

start_at
end_at
timezone

event_type
priority

rrule
recurrence_group_id

privacy
status

created_at
updated_at
```

Event types dự kiến:

```text
class
meeting
work
personal
deadline
task
other
```

Priority:

```text
low
normal
high
critical
```

Privacy:

```text
public
private
hidden
```

Status:

```text
active
done
cancelled
```

---

## reminders

Thiết kế schema dự kiến:

```text
id
event_id
minutes_before
sound
critical
```

Một event có thể có nhiều reminder.

Ví dụ:

```text
60 phút trước
15 phút trước
5 phút trước
đúng giờ
```

---

## recurrence_exceptions

Recurring event phải hỗ trợ exception ở các phase sau.

Schema cần có khả năng biểu diễn:

```text
cancel one occurrence
modify one occurrence
```

Ví dụ:

Một lịch học lặp thứ Hai 07:00 mỗi tuần.

Riêng ngày 2026-08-24 được nghỉ.

Không được phá toàn bộ recurring series.

---

## notification_log

Hệ thống tương lai phải tránh duplicate notification sau khi daemon restart.

Thiết kế database cần dự trù:

```text
event_id
occurrence_start
reminder_minutes
notified_at
```

---

# DATETIME RULES

Tất cả datetime trong Python phải timezone-aware.

Không dùng naive datetime cho business logic.

Default timezone:

```text
Asia/Ho_Chi_Minh
```

Ưu tiên:

```python
zoneinfo.ZoneInfo
```

từ Python standard library.

Database design phải có quy tắc rõ ràng về cách lưu datetime.

Ví dụ có thể:

```text
UTC ISO-8601 trong DB
+
timezone của event
```

Hãy chọn một strategy nhất quán và document trong `ARCHITECTURE.md`.

---

# FUTURE CLI CONTRACT

Các command tương lai:

```bash
schedctl today
schedctl tomorrow
schedctl week
schedctl next

schedctl add
schedctl edit ID
schedctl delete ID
schedctl show ID

schedctl search QUERY
```

Machine readable:

```bash
schedctl today --json
schedctl week --json
schedctl next --json
```

Frontend-specific:

```bash
schedctl eww
schedctl lock
```

Diagnostics:

```bash
schedctl doctor
```

Không implement toàn bộ CLI này trong Phase 0.

Chỉ thiết kế architecture để sau này implement sạch.

---

# FUTURE DAEMON DESIGN

Daemon tương lai tên:

```text
scheduled
```

hoặc executable tương đương hợp lý.

Sẽ dùng:

```python
asyncio
```

Logic mục tiêu:

```text
load events
    ↓
expand occurrences
    ↓
calculate reminder deadlines
    ↓
find next deadline
    ↓
sleep until deadline
    ↓
send notification
    ↓
save notification log
    ↓
calculate next deadline
```

Không dùng polling loop mỗi giây.

Sau này CLI sửa event sẽ báo daemon reload.

Một hướng có thể dùng:

```text
SIGUSR1
```

và:

```python
asyncio.Event
```

Phase 0 chỉ cần document thiết kế.

**Không implement daemon trong Phase 0.**

---

# FUTURE SUSPEND / RESUME BEHAVIOR

Ví dụ:

```text
18:40 laptop suspend
19:00 event bắt đầu
19:05 laptop resume
```

Daemon tương lai phải có thể detect missed event.

Ví dụ notification:

```text
Lịch đã bắt đầu

Dạy 12A1
Bắt đầu lúc 19:00
Trễ 5 phút
```

Sẽ có config kiểu:

```toml
[notification]
missed_event_window_minutes = 30
```

Phase 0 chỉ document strategy.

---

# FUTURE EWW DESIGN

Eww chỉ render JSON.

Ví dụ:

```bash
schedctl eww
```

có thể trả:

```json
{
  "date": "Thứ Năm · 13/08",
  "now": null,
  "next": null,
  "events": [],
  "free_minutes": 0
}
```

Desktop widget tương lai sẽ có:

```text
NOW
NEXT
TODAY
FREE TIME
TOMORROW COUNT
```

Không implement Eww trong Phase 0.

---

# FUTURE HYPRLOCK DESIGN

Hyprlock sẽ lấy dữ liệu từ:

```bash
schedctl lock
```

Lockscreen chỉ hiện thông tin cần thiết.

Privacy behavior:

```text
public  -> hiện đầy đủ
private -> chỉ hiện "Có lịch cá nhân"
hidden  -> không hiện
```

Phase 0 chỉ document contract.

---

# FUTURE FEATURES

Các feature sau nằm trong roadmap nhưng **KHÔNG ĐƯỢC IMPLEMENT BÂY GIỜ**:

- recurring weekly events
- multiple weekdays
- recurrence exceptions
- reminder daemon
- Eww widget
- Eww add/edit popup
- hyprlock integration
- conflict detection
- free-time calculation
- natural-language input
- ICS import/export
- semester profiles
- holidays
- Google Calendar sync
- snooze
- notification actions
- Waybar integration

---

# PHASE ROADMAP

Tạo `PLAN.md` với roadmap tối thiểu như sau.

## Phase 0 - Foundation

- repository structure
- Python packaging
- configuration
- XDG paths
- SQLite initialization
- migrations
- models
- logging
- CLI skeleton
- `schedctl doctor`
- pytest

## Phase 1 - Event Engine

- CRUD
- one-time events
- recurring events
- weekly recurrence
- multiple weekdays
- recurrence exceptions
- reminders
- conflict detection

## Phase 2 - CLI

- today
- tomorrow
- week
- next
- add
- edit
- delete
- show
- search
- JSON output

## Phase 3 - Scheduler Daemon

- asyncio scheduler
- exact reminders
- notification log
- notify-send
- pw-play
- signal reload
- systemd user service
- suspend/resume recovery

## Phase 4 - Eww Read-only Widget

- current event
- next event
- today's events
- countdown
- free time
- tomorrow count

## Phase 5 - Hyprlock

- lock formatter
- public/private/hidden
- dynamic refresh

## Phase 6 - Add/Edit UI

- create
- edit
- delete
- reminders
- recurrence
- privacy
- priority

## Phase 7 - UX Polish

- animations
- progress
- icons
- keyboard navigation
- empty/error states

## Phase 8 - Natural Language Input

Vietnamese deterministic parser.

Examples:

```text
Dạy 12A1 mai 7h đến 7h45 phòng A203
```

```text
Họp tổ thứ 6 14h nhắc trước 30 phút
```

```text
Dạy 11A3 thứ 2 hàng tuần 8h-8h45
```

Không gọi LLM API cho parser.

## Phase 9 - ICS

- ICS import
- ICS export

Google Calendar chỉ xem xét sau đó.

---

# YOUR CURRENT TASK

## IMPLEMENT PHASE 0 ONLY

Không implement Phase 1 hoặc bất kỳ phase nào phía sau.

### Step 1

Inspect repository hiện tại.

Nếu repository trống, khởi tạo project.

Nếu đã có code, đọc kỹ trước khi thay đổi.

Không xóa hoặc rewrite code đang hoạt động nếu không cần thiết.

---

### Step 2

Tạo:

```text
PLAN.md
ARCHITECTURE.md
AGENTS.md
```

---

# PLAN.md REQUIREMENTS

Bao gồm:

- mục tiêu project
- roadmap các phase
- dependency giữa các phase
- checkbox cho từng milestone
- definition of done cho từng phase

Ví dụ:

```markdown
- [ ] Phase 0 - Foundation
- [ ] Phase 1 - Event Engine
```

Không đánh dấu hoàn thành nếu test chưa pass.

---

# ARCHITECTURE.md REQUIREMENTS

Document rõ:

- system overview
- component boundaries
- data flow
- XDG paths
- SQLite schema
- migration strategy
- datetime/timezone strategy
- recurrence strategy
- recurrence exceptions
- reminders
- notification deduplication
- suspend/resume behavior
- CLI architecture
- JSON contracts
- Eww contract
- hyprlock contract
- configuration model
- logging
- error handling
- testing strategy

Không viết chung chung.

Đưa ra quyết định kỹ thuật cụ thể.

---

# AGENTS.md REQUIREMENTS

Đây là instruction dành cho coding agents tương lai.

Bắt buộc ghi rõ:

1. Read `PLAN.md` trước khi code.
2. Read `ARCHITECTURE.md` trước khi sửa architecture.
3. Implement only the requested phase.
4. Do not silently expand scope.
5. Business logic belongs in Python backend.
6. Eww and hyprlock are presentation layers only.
7. Use timezone-aware datetime.
8. Respect XDG Base Directory.
9. Add tests for new behavior.
10. Run tests before declaring completion.
11. Do not overwrite user configuration.
12. Do not introduce cloud services without explicit request.
13. Prefer simple dependencies.
14. Do not rewrite project in another language.
15. Preserve backward compatibility unless architecture explicitly requires otherwise.

---

# PHASE 0 IMPLEMENTATION

Implement:

## Python package

Use modern Python packaging through:

```text
pyproject.toml
```

Expose CLI command:

```bash
schedctl
```

Use `src/` layout.

---

## Configuration

Implement config loading.

Default config equivalent to:

```toml
timezone = "Asia/Ho_Chi_Minh"

[schedule]
day_start = "06:00"
day_end = "23:00"

[widget]
max_events = 6
show_free_time = true
show_tomorrow_count = true
refresh_seconds = 30

[lockscreen]
max_events = 2
show_private = false

[notification]
default_reminders = [15, 5, 0]
sound = true
missed_event_window_minutes = 30

[recurrence]
skip_classes_on_holidays = true
```

Config file không tồn tại phải dùng defaults hợp lý.

Invalid config phải báo lỗi rõ ràng.

Không crash bằng traceback vô nghĩa cho lỗi user configuration thông thường.

---

## XDG PATH HANDLING

Implement helper để xác định:

- config directory
- data directory
- database path
- runtime directory

Có test.

Tests không được ghi dữ liệu vào real home directory của user.

Dùng temporary directories / monkeypatch environment.

---

## DATABASE

Implement:

- SQLite connection management
- initialization
- migration mechanism
- schema version tracking

Migration system phải đủ đơn giản nhưng có thể mở rộng.

Không sử dụng ORM nặng nếu không thật sự cần.

Ưu tiên Python standard library:

```python
sqlite3
```

unless có lý do mạnh để dùng dependency khác.

Database connection nên bật các setting phù hợp như foreign keys.

Document quyết định trong architecture.

---

## MODELS

Implement base models / enums cần thiết cho event domain.

Bao gồm ít nhất:

- EventType
- Priority
- Privacy
- EventStatus

Và một base event data model phù hợp.

Không cần implement CRUD đầy đủ.

Model phải dùng typing rõ ràng.

---

## LOGGING

Implement logging infrastructure.

CLI bình thường không được spam debug logs.

Có cách enable verbose/debug mode nếu phù hợp.

---

# SCHEDCTL DOCTOR

Implement:

```bash
schedctl doctor
```

Doctor phải kiểm tra ít nhất:

- Python runtime/version
- config directory
- data directory
- SQLite database initialization
- database migration status
- timezone availability
- `notify-send`
- `pw-play`
- `eww`
- `hyprlock`
- systemd user availability nếu kiểm tra được an toàn

Output phải dễ đọc.

Ví dụ:

```text
HyprSchedule doctor

✓ Python 3.x
✓ Config directory
✓ Data directory
✓ SQLite database
✓ Database schema
✓ Timezone Asia/Ho_Chi_Minh
✓ notify-send
✓ pw-play
✓ Eww
✓ hyprlock
```

Nếu Eww chưa cài:

```text
✗ Eww not found
  Desktop widget will not work.
  Backend functionality remains available.
```

Missing optional desktop dependency không được làm CLI crash.

Doctor phải return exit code hợp lý.

Phân biệt:

- core dependency failure
- optional dependency warning

nếu kiến trúc cho phép.

---

# TESTING

Dùng:

```text
pytest
```

Viết tests tối thiểu cho:

- XDG path resolution
- default configuration
- custom configuration
- invalid configuration
- database creation
- migrations
- schema version
- models/enums
- doctor core checks nếu thực tế

Tests phải:

- deterministic
- không cần network
- không sửa real `$HOME`
- không phụ thuộc desktop session thật
- dùng temp directory khi cần

---

# CODE QUALITY

Yêu cầu:

- typed Python
- functions nhỏ
- module boundaries rõ
- descriptive names
- minimal global state
- no unnecessary abstractions
- no premature framework
- helpful error messages

Ưu tiên standard library khi hợp lý.

Dependencies bên ngoài phải có lý do.

---

# ABSOLUTE CONSTRAINTS

DO NOT:

- rewrite the project in Rust
- rewrite the project in Go
- use Electron
- use React
- use Node.js for backend
- add a web server
- create a REST API
- use Docker
- use Kubernetes
- add authentication
- add user accounts
- add telemetry
- add analytics
- add cloud database
- add Google Calendar yet
- add ICS yet
- add AI API calls
- implement natural-language parsing yet
- implement Eww yet
- implement hyprlock integration yet
- implement scheduler daemon yet
- implement recurring events yet
- implement notifications yet
- directly edit existing user's Hyprland configuration
- directly edit existing user's hyprlock configuration
- store user data inside the repository
- use naive datetime in business logic
- put business logic into presentation layers
- continue to Phase 1

Không thêm feature chỉ vì "it might be useful".

---

# INSTALLATION SAFETY

Không tự động sửa:

```text
~/.config/hypr/hyprland.conf
~/.config/hypr/hyprlock.conf
```

Installer tương lai sẽ dùng config snippets và hướng dẫn user source chúng.

Phase 0 không cần installer hoàn chỉnh nếu chưa cần thiết.

Nếu tạo `install.sh`, nó phải an toàn và idempotent.

---

# WORKFLOW

Thực hiện theo đúng thứ tự:

1. Inspect repository.
2. Read existing files.
3. Decide minimal Phase 0 changes.
4. Create/update `PLAN.md`.
5. Create/update `ARCHITECTURE.md`.
6. Create/update `AGENTS.md`.
7. Implement Phase 0.
8. Add tests.
9. Run tests.
10. Fix failures.
11. Run tests again.
12. Run `schedctl doctor`.
13. Inspect final git diff.
14. Check for accidental Phase 1 implementation.
15. Update Phase 0 checkbox only if Definition of Done is satisfied.
16. Stop.

---

# DEFINITION OF DONE FOR PHASE 0

Phase 0 chỉ được coi là hoàn thành khi:

- Python package installs/runs correctly
- `schedctl` executable works
- XDG path handling works
- config loads correctly
- SQLite initializes correctly
- migration mechanism works
- base models exist
- logging exists
- `schedctl doctor` runs
- tests pass
- docs reflect actual implementation
- no later-phase features were accidentally implemented

---

# FINAL RESPONSE FORMAT

Sau khi hoàn thành, trả về báo cáo ngắn theo format:

```text
PHASE 0 RESULT

Implemented:
- ...

Tests:
- command:
- result:

Doctor:
- command:
- result:

Files created:
- ...

Files modified:
- ...

Architecture decisions:
- ...

Known issues:
- ...

Next phase:
Phase 1 - Event Engine
NOT IMPLEMENTED
```

Nếu test fail, không được giả vờ Phase 0 hoàn thành.

Nếu có unresolved issue, ghi rõ.

---

# IMPORTANT FINAL INSTRUCTION

Implement **PHASE 0 ONLY**.

Do not begin Phase 1.

Do not proactively implement future features.

When Phase 0 tests and validation are complete, **STOP**.
