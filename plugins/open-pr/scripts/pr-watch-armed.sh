#!/usr/bin/env bash
# Waits for pr-poll.sh to write the watch's state file.
#
#   pr-watch-armed.sh <state-file> [timeout-seconds]   wait up to 240 seconds by default
set -euo pipefail

STATE_FILE=$1
DEADLINE=$((SECONDS + ${2:-240}))

while true; do
  if [ -f "$STATE_FILE" ] && jq -e '.watermarks' "$STATE_FILE" >/dev/null 2>&1; then
    printf 'armed %s\n' "$STATE_FILE"
    exit 0
  fi

  REMAINING=$((DEADLINE - SECONDS))
  [ "$REMAINING" -gt 0 ] || break
  sleep "$((REMAINING < 5 ? REMAINING : 5))"
done

printf 'not-armed %s: no pr-poll.sh pass wrote it\n' "$STATE_FILE" >&2
exit 1
