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
input and its environment only, never a file or an argument. A GH_TOKEN also
refreshes git's in-memory credential cache on the Sprite for an hour, so
long-running processes there read GitHub between passes.

The plugin version holding <script> is copied over once, when the Sprite lacks
it, the drive's registry file on every run with its `checkout` set to the job's
checkout, since the Sprite holds that clone and not this machine's, and the plan package the drive's
register names, the directory holding plan/plan.md, whenever a file in it was
added, removed or changed since the Sprite's copy. The job's checkout drops the
empty loose refs a Sprite restart leaves, which every fetch reads as the zero
object, and fetches every remote, and the job runs there. <dir> is then copied
back, result.json included and job.json and pid left as this machine wrote
them, so the runner settles the pass from this process and its files as it does
a local one. The Sprite then pushes the records the job posted to the drive's
cci relay, retried five times over about 40 seconds; a push that still fails
loses nothing, since the runner reposts the job's records when it settles the
pass. A `cancelled` mark
the runner writes while the job runs is forwarded, and each codex run the job
started there is cancelled. Exits with the script's status.
EOF2
  exit 2
}

[ $# -eq 3 ] || usage
export COPYFILE_DISABLE=1
sprite=$1 script=$2 dir=$3
plugin=$(cd "$(dirname "$script")/../../.." && pwd)
checkout=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["checkout"])' "$dir/job.json")
drive=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["drive"])' "$dir/job.json")
registry=${CLAUDE_CONFIG_DIR:-$HOME/.claude}/long-running/drives/$drive.json
plan=$(python3 "$(dirname "$script")/rulings.py" plan --repo "$checkout" --drive "$drive")

if ! sprite exec -s "$sprite" --no-stdin -- test -d "$plugin"; then
  tar --no-xattrs -C "$plugin" -cz . | sprite exec -s "$sprite" -- sh -c 'mkdir -p "$1" && tar -xz -m -C "$1"' sh "$plugin"
fi
tar --no-xattrs -C "$dir" -cz . | sprite exec -s "$sprite" -- sh -c 'mkdir -p "$1" && tar -xz -m -C "$1"' sh "$dir"
python3 -c 'import json, sys; entry = json.load(open(sys.argv[1])); entry["checkout"] = sys.argv[2]; print(json.dumps(entry))' "$registry" "$checkout" |
  sprite exec -s "$sprite" -- sh -c 'mkdir -p "$(dirname "$1")" && cat >"$1"' sh "$registry"
if [ -n "$plan" ]; then
  package=$(dirname "$(dirname "$plan")")
  digest=$(python3 -c '
import hashlib, os, sys
seen = hashlib.sha256()
for here, _, names in sorted(os.walk(sys.argv[1])):
    for name in sorted(names):
        path = os.path.join(here, name)
        stat = os.stat(path)
        seen.update(f"{os.path.relpath(path, sys.argv[1])} {stat.st_size} {stat.st_mtime_ns}\n".encode())
print(seen.hexdigest())' "$package")
  if [ "$(sprite exec -s "$sprite" --no-stdin -- sh -c 'mkdir -p "$(dirname "$1")"; cat "$1.digest" 2>/dev/null' sh "$package")" != "$digest" ]; then
    tar --no-xattrs -C "$package" -cz . | sprite exec -s "$sprite" -- flock "$package.lock" sh -c '
      if [ "$(cat "$1.digest" 2>/dev/null)" = "$2" ]; then exec cat >/dev/null; fi
      rm -rf "$1.new" && mkdir -p "$1.new" && tar -xz -m -C "$1.new" && rm -rf "$1" && mv "$1.new" "$1" && printf %s "$2" >"$1.digest"' sh "$package" "$digest"
  fi
fi

python3 -c '
import json, os, subprocess
for name, argv in json.loads(os.environ.get("SPRITE_JOB_KEYS") or "{}").items():
    print(f"{name}={subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()}")
' | sprite exec -s "$sprite" -- sh -c '
  while IFS= read -r line; do export "$line"; done
  [ -z "${GH_TOKEN:-}" ] || printf "protocol=https\nhost=github.com\nusername=x-access-token\npassword=%s\n\n" "$GH_TOKEN" |
    git credential-cache --timeout=3900 store
  find "$(git -C "$3" rev-parse --path-format=absolute --git-common-dir)/refs" -type f -empty ! -name "*.lock" -delete
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
sprite exec -s "$sprite" --no-stdin -- sh -c 'cd "$1" && find . -mindepth 1 ! -path ./job.json ! -path ./pid | tar -cz --no-recursion -T -' sh "$dir" | tar -xz -m -C "$dir"
for pause in 2 5 10 20 ""; do
  if sprite exec -s "$sprite" --no-stdin -- cci relay push >/dev/null; then
    break
  fi
  if [ -z "$pause" ]; then
    echo "sprite-job: cci relay push failed 5 times; the runner reposts this job's records from posts.jsonl when it settles" >&2
    break
  fi
  sleep "$pause"
done
exit "$status"
