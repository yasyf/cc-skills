#!/usr/bin/env bash

# Per-UID process cap for `go test`: a daemonkit proc.Spawn path re-execs
# os.Executable(); a test binary there fork-bombs. Always run tests here.
set -euo pipefail

headroom="${TEST_NPROC_HEADROOM:-400}"
# Current process count for this real UID. macOS `ps -U <uid>` rejects a numeric
# id, so filter `ps -axo` instead. Best-effort; defaults to 0.
cur="$(ps -axo uid=,pid= 2>/dev/null | awk -v u="$(id -ru)" '$1==u {n++} END{print n+0}')" || cur=0
[ -n "${cur:-}" ] || cur=0
cap=$(( 10#${cur} + headroom ))
hard="$(ulimit -Hu 2>/dev/null || echo unlimited)"
if [ "$hard" != "unlimited" ] && [ "$cap" -gt "$hard" ]; then
  cap="$hard"
fi
ulimit -Su "$cap"

# Apply a default timeout unless the caller already set one, so a wedged test
# can never hang the cap in place indefinitely.
case " $* " in
  *" -timeout"*) ;;
  *) set -- -timeout 600s "$@" ;;
esac

echo "scripts/test.sh: RLIMIT_NPROC soft cap=$cap (uid procs ~$cur + headroom $headroom); go test $*" >&2
exec go test "$@"
