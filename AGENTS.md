# AGENTS.md — Instructions for Coding Agents

This file governs all work done by coding agents in this repository. Follow it
strictly.

## Mandatory Rules

1. Read `PLAN.md` before coding.
2. Read `ARCHITECTURE.md` before changing architecture.
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

## Practical Conventions

- Project root: `/home/desi/Projects/HyprSchedule`.
- Run tests with `uv run pytest`.
- Package: `src/hyprschedule`.
- Console script: `schedctl`.
- No runtime dependencies are allowed without justification (see
  ARCHITECTURE.md — the project ships with ZERO runtime deps; `tomllib`,
  `zoneinfo`, `sqlite3` come from the standard library).
- Vietnamese UI strings are acceptable (default timezone is
  `Asia/Ho_Chi_Minh`).
- Do not mark a phase checkbox complete in `PLAN.md` until its Definition of
  Done — including passing tests — is fully satisfied.
- Never edit `~/.config/hypr/hyprland.conf` or `~/.config/hypr/hyprlock.conf`.
- Never store user data inside the repository.
- No naive datetime in business logic; DB stores UTC ISO-8601 TEXT with
  explicit offset.
- Missing optional desktop dependencies (eww, hyprlock, notify-send, pw-play)
  must never crash the backend.