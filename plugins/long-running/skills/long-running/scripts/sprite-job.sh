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
input and its environment only, never a file or an argument; a command that prints
nothing stops the job before it reaches the Sprite, with that error as its result. Every git command
the job runs authenticates through `gh auth git-credential`, the partial clone's
on-demand object fetches included. A GH_TOKEN also refreshes git's in-memory
credential cache on the Sprite for an hour, so long-running processes there read
GitHub between passes.

The plugin version holding <script> is copied over whenever a file in it was
added, removed or changed since the Sprite's copy, so a truncated install or a
half-finished copy is replaced on the next job, swapped in whole, with caches
and Claude Code's in-use marks left out; the drive's registry file on every run
with its `checkout` set to the job's checkout, since the Sprite holds that clone and not this machine's, the register
copy kept in the drive's state as a snapshot for that checkout, which rulings.py
on the Sprite reads instead of rebuilding the register cold with ccn, and the plan package the drive's
register names, the directory holding plan/plan.md, whenever a file in it was
added, removed or changed since the Sprite's copy. The job's checkout drops the
empty loose refs a Sprite restart leaves, which every fetch reads as the zero
object, and fetches every remote unless a fetch that began after this job asked for one already
did, then merges its packs into a geometric progression, so the packs fetches and on-demand blob reads leave stay few, and the job runs there. <dir> is then copied
back, result.json included and job.json and pid left as this machine wrote
them, so the runner settles the pass from this process and its files as it does
a local one; a job that ends with no result, as when the Sprite restarts
mid-pass, leaves one naming the error, so the runner fails and requeues the
pass at once instead of waiting out its deadline. The Sprite then pushes the records the job posted to the drive's
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
state=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["state_dir"])' "$registry")
program=$(python3 -c 'import json, sys; from pathlib import Path; entry = json.load(open(sys.argv[1])); print(entry.get("program") or Path(entry["state_dir"]).name)' "$registry")
plan=$(python3 "$(dirname "$script")/rulings.py" plan --repo "$checkout" --drive "$drive")

digest() {
  python3 -c '
import hashlib, os, sys
seen = hashlib.sha256()
for here, dirs, names in os.walk(sys.argv[1]):
    dirs[:] = sorted(name for name in dirs if name not in ("__pycache__", ".in_use"))
    for name in sorted(set(names) - {".orphaned_at"}):
        path = os.path.join(here, name)
        stat = os.stat(path)
        seen.update(f"{os.path.relpath(path, sys.argv[1])} {stat.st_size} {stat.st_mtime_ns}\n".encode())
print(seen.hexdigest())' "$1"
}

ship() {
  sum=$(digest "$1")
  if [ "$(sprite exec -s "$sprite" --no-stdin -- sh -c 'mkdir -p "$(dirname "$1")"; cat "$1.digest" 2>/dev/null' sh "$1")" != "$sum" ]; then
    tar --no-xattrs --exclude=__pycache__ --exclude=.in_use --exclude=.orphaned_at -C "$1" -cz . | sprite exec -s "$sprite" -- flock "$1.lock" sh -c '
      if [ "$(cat "$1.digest" 2>/dev/null)" = "$2" ]; then exec cat >/dev/null; fi
      rm -rf "$1.new" && mkdir -p "$1.new" && tar -xz -m -C "$1.new" && rm -rf "$1" && mv "$1.new" "$1" && printf %s "$2" >"$1.digest"' sh "$1" "$sum"
  fi
}

ship "$plugin"
tar --no-xattrs -C "$dir" -cz . | sprite exec -s "$sprite" -- sh -c 'mkdir -p "$1" && tar -xz -m -C "$1"' sh "$dir"
python3 -c 'import json, sys; entry = json.load(open(sys.argv[1])); entry["checkout"] = sys.argv[2]; print(json.dumps(entry))' "$registry" "$checkout" |
  sprite exec -s "$sprite" -- sh -c 'mkdir -p "$(dirname "$1")" && cat >"$1"' sh "$registry"
python3 -c '
import json, sys
from pathlib import Path
kept = Path(sys.argv[1])
print(json.dumps({kept.name: {**json.loads(kept.read_text()), "repo": sys.argv[2], "snapshot": True}} if kept.is_file() else {}))' "$state/register-$program.json" "$checkout" |
  sprite exec -s "$sprite" -- python3 -c '
import json, os, sys
from pathlib import Path
state = Path(sys.argv[1])
state.mkdir(parents=True, exist_ok=True)
for name, kept in json.load(sys.stdin).items():
    staged = state / f".{name}.{os.getpid()}"
    staged.write_text(json.dumps(kept, ensure_ascii=False))
    staged.replace(state / name)' "$state"
if [ -n "$plan" ]; then
  ship "$(dirname "$(dirname "$plan")")"
fi

keys=$(python3 -c '
import json, os, subprocess, sys
from pathlib import Path
for name, argv in json.loads(os.environ.get("SPRITE_JOB_KEYS") or "{}").items():
    if not (value := subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()):
        error = f"the {name} command printed nothing, so the job would run without {name}"
        (Path(sys.argv[1]) / "result.json").write_text(json.dumps({"error": error}) + "\n")
        sys.exit(f"sprite-job: {error}")
    print(f"{name}={value}")
' "$dir") || exit 1
{ [ -z "$keys" ] || printf "%s\n" "$keys"; } | sprite exec -s "$sprite" -- sh -c '
  while IFS= read -r line; do export "$line"; done
  export GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0= GIT_CONFIG_KEY_1=credential.helper "GIT_CONFIG_VALUE_1=!gh auth git-credential"
  [ -z "${GH_TOKEN:-}" ] || printf "protocol=https\nhost=github.com\nusername=x-access-token\npassword=%s\n\n" "$GH_TOKEN" |
    git credential-cache --timeout=3900 store
  find "$(git -C "$3" rev-parse --path-format=absolute --git-common-dir)/refs" -type f -empty ! -name "*.lock" -delete
  asked=$(date +%s%N)
  exec 9>"$3.fetch.lock"
  flock 9
  if [ "$(cat "$3.fetched" 2>/dev/null || echo 0)" -lt "$asked" ]; then
    began=$(date +%s%N)
    git -C "$3" fetch --quiet --all
    git -C "$3" repack --geometric=2 -d -q
    printf %s "$began" >"$3.fetched"
  fi
  exec 9>&-
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
sprite exec -s "$sprite" --no-stdin -- sh -c 'cd "$1" && find . -mindepth 1 ! -path ./job.json ! -path ./pid | tar -cz --no-recursion -T -' sh "$dir" | tar -xz -m -C "$dir" || true
[ -e "$dir/result.json" ] || printf '{"error": "the job on Sprite %s ended with status %s and no result, as when the Sprite restarts mid-pass"}\n' "$sprite" "$status" >"$dir/result.json"
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
