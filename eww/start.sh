#!/usr/bin/env bash
# Mở toàn bộ HyprSchedule UI (daemon + widget + editor) bằng một lệnh.
set -u
SCRIPT=$(readlink -f "$0")
cd "$(dirname "$SCRIPT")/.."
export PATH="$PWD/.venv/bin:$PATH"
CONFIG="$PWD/eww"

if ! eww --config "$CONFIG" get time >/dev/null 2>&1; then
  mkdir -p "$HOME/.local/eww"
  eww --config "$CONFIG" daemon >/tmp/hyprschedule-eww.log 2>&1 </dev/null &
  disown
  sleep 1
fi
eww --config "$CONFIG" open hyprschedule
eww --config "$CONFIG" open hyprschedule-editor
echo "Đã mở HyprSchedule (daemon + widget + editor)."