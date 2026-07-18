#!/bin/sh
# Run `go test` under a per-UID process cap so a runaway spawn — e.g. a holder
# that re-execs the test binary and re-enters the spawn — hits EAGAIN within a
# small headroom instead of fork-bombing the machine and freezing it.
#
# ALWAYS run these repos' tests through this script (CI, local, and any agent or
# workflow). Never invoke `go test` directly on a real machine.
# See the 2026-06-24 mount-holder fork-storm incident (cc-pool cc-notes: ccn doc show ef281ea).
set -eu

headroom="${TEST_NPROC_HEADROOM:-400}"
# Current process count for this real UID. macOS `ps -U <uid>` rejects a numeric
# id, so filter `ps -axo` instead. Best-effort; defaults to 0.
cur="$(ps -axo uid=,pid= 2>/dev/null | awk -v u="$(id -ru)" '$1==u {n++} END{print n+0}')" || cur=0
[ -n "${cur:-}" ] || cur=0
cur=${cur#"${cur%%[!0]*}"}  # strip leading zeros: POSIX $(( )) reads them as octal
[ -n "$cur" ] || cur=0
cap=$(( cur + headroom ))
# RLIMIT_NPROC: bash and macOS `/bin/sh` spell it `-u`, dash spells it `-p` — try both.
hard="$(ulimit -Hu 2>/dev/null || ulimit -Hp 2>/dev/null || echo unlimited)"
if [ "$hard" != "unlimited" ] && [ "$cap" -gt "$hard" ]; then
  cap="$hard"
fi
ulimit -Su "$cap" 2>/dev/null || ulimit -Sp "$cap" 2>/dev/null ||
  echo "scripts/test.sh: warning: shell exposes no RLIMIT_NPROC control; fork-bomb cap NOT applied" >&2

# Apply a default timeout unless the caller already set one, so a wedged test
# can never hang the cap in place indefinitely.
case " $* " in
  *" -timeout"*) ;;
  *) set -- -timeout {{timeout}} "$@" ;;
esac

echo "scripts/test.sh: RLIMIT_NPROC soft cap=$cap (uid procs ~$cur + headroom $headroom); go test $*" >&2
exec go test "$@"
