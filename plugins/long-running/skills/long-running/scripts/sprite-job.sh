#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF2'
usage: sprite-job.sh <sprite> <script> <dir>

Runs the desk job <dir> through <script>, finder.py or fidelity.py, on the Sprite
<sprite> at the same absolute paths, so job.json and every path it names read
the same there. The Sprite mirrors this machine's home at its own path, has
codex, codex-ask, ccx, ccn and cci installed, is joined to the drive's cci relay,
and holds a clone at the job's checkout whose remotes fetch the branches and
cc-notes refs the job reads, cc-notes from an unfiltered remote when the clone
is partial.

SPRITE_JOB_KEYS, a JSON object of environment names to the commands that print
their values, such as {"GH_TOKEN": ["gh", "auth", "token"]}, is run here at
start; each value reaches the job's process on the Sprite through its standard
input and its environment only, never a file or an argument.

The plugin version holding <script> is copied over once, when the Sprite lacks
it, and the drive's registry file on every run. The job's checkout fetches every
remote, the job runs there, and <dir> is copied back, result.json included, so the runner settles the
pass from this process and its files as it does a local one. A `cancelled` mark
the runner writes while the job runs is forwarded, and each codex run the job
started there is cancelled. Exits with the script's status.
EOF2
  exit 2
}

[ $# -eq 3 ] || usage
sprite=$1 script=$2 dir=$3
plugin=$(cd "$(dirname "$script")/../../.." && pwd)
checkout=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["checkout"])' "$dir/job.json")
registry=${CLAUDE_CONFIG_DIR:-$HOME/.claude}/long-running/drives/$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["drive"])' "$dir/job.json").json

if ! sprite exec -s "$sprite" --no-stdin -- test -d "$plugin"; then
  tar --no-xattrs -C "$plugin" -cz . | sprite exec -s "$sprite" -- sh -c 'mkdir -p "$1" && tar -xz -m -C "$1"' sh "$plugin"
fi
tar --no-xattrs -C "$dir" -cz . | sprite exec -s "$sprite" -- sh -c 'mkdir -p "$1" && tar -xz -m -C "$1"' sh "$dir"
sprite exec -s "$sprite" -- sh -c 'mkdir -p "$(dirname "$1")" && cat >"$1"' sh "$registry" <"$registry"

python3 -c '
import json, os, subprocess
for name, argv in json.loads(os.environ.get("SPRITE_JOB_KEYS") or "{}").items():
    print(f"{name}={subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()}")
' | sprite exec -s "$sprite" -- sh -c '
  while IFS= read -r line; do export "$line"; done
  flock "$3.fetch.lock" git -C "$3" -c credential.helper= -c "credential.helper=!gh auth git-credential" fetch --quiet --all
  exec python3 "$1" "$2"' sh "$script" "$dir" "$checkout" &
job=$!
forwarded=
while kill -0 "$job" 2>/dev/null; do
  if [ -z "$forwarded" ] && [ -e "$dir/cancelled" ]; then
    sprite exec -s "$sprite" -- sh -c 'cat >"$1/cancelled"; for run in "$1"/codex-*; do [ -e "$run" ] && codex-ask --cancel "$run"; done; true' sh "$dir" <"$dir/cancelled" || true
    forwarded=1
  fi
  sleep 10
done
status=0
wait "$job" || status=$?
sprite exec -s "$sprite" --no-stdin -- tar -C "$dir" -cz . | tar -xz -m -C "$dir"
exit "$status"
