#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF2'
usage: sprite-job.sh <sprite> <script> <dir>

Runs the desk job <dir> through <script>, finder.py or fidelity.py, on the Sprite
<sprite> at the same absolute paths, so job.json and every path it names read
the same there. The Sprite mirrors this machine's home at its own path and is
signed in to GitHub and codex and joined to the drive's cci relay.

The plugin version holding <script> is copied over once, when the Sprite lacks
it. The job's checkout fetches origin's branches and cc-notes refs, the job runs
there, and <dir> is copied back, result.json included, so the runner settles the
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

if ! sprite exec -s "$sprite" --no-stdin -- test -d "$plugin"; then
  tar -C "$plugin" -cz . | sprite exec -s "$sprite" -- sh -c 'mkdir -p "$1" && tar -xz -m -C "$1"' sh "$plugin"
fi
tar -C "$dir" -cz . | sprite exec -s "$sprite" -- sh -c 'mkdir -p "$1" && tar -xz -m -C "$1"' sh "$dir"
sprite exec -s "$sprite" --no-stdin -- flock "$checkout.fetch.lock" git -C "$checkout" fetch --quiet origin '+refs/heads/*:refs/remotes/origin/*' '+refs/cc-notes/*:refs/cc-notes/*'

sprite exec -s "$sprite" --no-stdin -- python3 "$script" "$dir" &
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
