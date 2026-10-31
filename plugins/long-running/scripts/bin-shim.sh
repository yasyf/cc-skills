#!/bin/bash
# Every bin/<tool> is a symlink to this script, so a session that started on an
# older plugin version still runs the newest installed one after an update.
set -eu

case "$0" in */*) d=${0%/*} ;; *) d=. ;; esac
ROOT="$(cd "$d/.." && pwd)"
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

if is_version "${ROOT##*/}"; then
  newest=$ROOT whole=$ROOT
  for sibling in "${ROOT%/*}"/*/; do
    sibling=${sibling%/}
    is_version "${sibling##*/}" || continue
    [ -x "$sibling/bin/$TOOL" ] && [ -e "$sibling/skills/long-running/scripts/$TOOL" ] || continue
    if [ ! -e "$sibling/.orphaned_at" ] && version_gt "${sibling##*/}" "${newest##*/}"; then
      newest=$sibling
    fi
    if version_gt "${sibling##*/}" "${whole##*/}"; then
      whole=$sibling
    fi
  done
  [ "$newest" != "$ROOT" ] || [ ! -e "$ROOT/.orphaned_at" ] || newest=$whole
  [ "$newest" = "$ROOT" ] || exec "$newest/bin/$TOOL" "$@"
fi

SCRIPT="$ROOT/skills/long-running/scripts/$TOOL"
case "$TOOL" in
  *.py)
    for python in python3.15 python3.14 python3.13 /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 python3; do
      command -v "$python" >/dev/null 2>&1 && break
    done
    exec "$python" "$SCRIPT" "$@"
    ;;
  *) exec "$SCRIPT" "$@" ;;
esac
