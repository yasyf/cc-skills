#!/bin/bash
# Every bin/<tool> is a symlink to this script, so a session that started on an
# older plugin version still runs the version the install link names after an
# update, except under LONG_RUNNING_PIN naming this copy's version, which a
# pinned desk runner sets so it and its children keep the version runner.json pins.
set -eu

case "$0" in */*) d=${0%/*} ;; *) d=. ;; esac
ROOT="$(cd "$d/.." && pwd -P)"
TOOL=${0##*/}

is_version() {
  case "$1" in "" | *[!0-9.]* | .* | *. | *..*) return 1 ;; esac
}

version_gt() {
  va=$1 vb=$2
  while [ -n "$va$vb" ]; do
    ca=${va%%.*} cb=${vb%%.*}
    [ "${ca:-0}" -eq "${cb:-0}" ] || { [ "${ca:-0}" -gt "${cb:-0}" ]; return; }
    case "$va" in *.*) va=${va#*.} ;; *) va= ;; esac
    case "$vb" in *.*) vb=${vb#*.} ;; *) vb= ;; esac
  done
  return 1
}

if is_version "${ROOT##*/}" && [ "${LONG_RUNNING_PIN:-}" != "${ROOT##*/}" ]; then
  current=$(readlink "${CLAUDE_CONFIG_DIR:-${HOME:-}/.claude}/long-running/current" 2>/dev/null || true)
  if [ -n "$current" ] && is_version "${current##*/}" && version_gt "${current##*/}" "${ROOT##*/}" && [ -x "$current/bin/$TOOL" ]; then
    exec "$current/bin/$TOOL" "$@"
  fi
fi

SCRIPT="$ROOT/skills/long-running/scripts/$TOOL"
case "$TOOL" in
  lr)
    export BINRUN_PLUGIN_ROOT="$ROOT"
    exec "$ROOT/scripts/binrun.sh" "$ROOT/bin/lr.binrun" "$@"
    ;;
  cc-remote) exec "$ROOT/scripts/binrun.sh" "$ROOT/bin/cc-remote.binrun" "$@" ;;
  incident.py) exec "$ROOT/bin/lr" incident "$@" ;;
  replay.py) exec "$ROOT/bin/lr" replay "$@" ;;
  remote-restack.py) exec "$ROOT/bin/lr" landing restack "$@" ;;
  desk-creds.py) exec "$ROOT/bin/lr" desk-creds "$@" ;;
  owner_waits.py) exec "$ROOT/bin/lr" board waits "$@" ;;
  owner_update.py) exec "$ROOT/bin/lr" update "$@" ;;
  orca-launch.sh) exec "$ROOT/bin/lr" launch start "$@" ;;
  desk-launch.py) exec "$ROOT/bin/lr" launch "$@" ;;
  worker-launch.sh) exec "$ROOT/bin/lr" launch worker "$@" ;;
  orca-gc) exec "$ROOT/bin/lr" gc "$@" ;;
  *.py)
    for python in python3.15 python3.14 python3.13 /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 python3; do
      command -v "$python" >/dev/null 2>&1 && break
    done
    PYTHONPATH="${SCRIPT%/*}${PYTHONPATH:+:$PYTHONPATH}" exec "$python" "$SCRIPT" "$@"
    ;;
  *) exec "$SCRIPT" "$@" ;;
esac
