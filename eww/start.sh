#!/usr/bin/env bash
# Mở toàn bộ HyprSchedule UI (daemon + widget + editor) bằng một lệnh.
set -u
cd "$(dirname "$0")/.."
export PATH="$PWD/.venv/bin:$PATH"
CONFIG="$PWD/eww"

eww --config "$CONFIG" daemon >/tmp/hyprschedule-eww.log 2>&1 </dev/null &
disown
sleep 1
eww --config "$CONFIG" open hyprschedule
eww --config "$CONFIG" open hyprschedule-editor
echo "Đã mở HyprSchedule (daemon + widget + editor)."