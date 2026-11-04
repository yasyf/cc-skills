#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF2'
usage: sprite-job.sh <sprite> <job> <dir>

Runs the desk job <dir> on the Sprite <sprite> through <job>, either a grader
name for `lr grade`, finder or fidelity, or the absolute path of a Python script
in this plugin, at the same absolute paths, so job.json and every path it names read
the same there. The Sprite mirrors this machine's home at its own path, has
codex, codex-ask, ccx, ccn and cci installed, is joined to the drive's cci relay,
and holds a clone at the job's checkout whose remotes fetch the branches and
cc-notes refs the job reads, cc-notes from an unfiltered remote when the clone
is partial.

SPRITE_JOB_KEYS, a JSON object of environment names to the commands that print
their values, such as {"GH_TOKEN": ["gh", "auth", "token"]}, is run here at
start; each value reaches the job's process on the Sprite through a named pipe
the job reads once and its environment only, never a stored file or an argument,
written by a short exec's standard input, so the long exec that runs the job
forwards no stdin and the Sprite CLI reconnects it when the connection drops; a command that prints
nothing stops the job before it reaches the Sprite, with that error as its result. Every git command
the job runs authenticates through `gh auth git-credential`, the partial clone's
on-demand object fetches included. A GH_TOKEN also refreshes git's in-memory
credential cache on the Sprite for an hour, so long-running processes there read
GitHub between passes. An installation token dies within the hour and may reach
the job with five minutes left, while a pass can run longer than an hour, so its
command runs again for each Sprite every four minutes while a job runs there,
and the job's gh and ccx take GH_TOKEN from that cache each time they start.

The plugin version holding this script is copied over whenever a file in it was
added, removed or changed since the Sprite's copy, so a truncated install or a
half-finished copy is replaced on the next job, extracted into a directory of
its own beside the path and named by one rename of a link, with caches and
Claude Code's in-use marks left out, and a job holds a shared lock on the copy
it runs, so an older copy is deleted only once no job holds it; the drive's registry file on every run
with its `checkout` set to the job's checkout, since the Sprite holds that clone and not this machine's, the register
copy kept in the drive's state as a snapshot for that checkout, which rulings.py
on the Sprite reads instead of rebuilding the register cold with ccn, and the plan package the drive's
register names, the directory holding plan/plan.md, whenever a file in it was
added, removed or changed since the Sprite's copy. The job's checkout drops the
loose refs a Sprite restart leaves empty or filled with NULs, which every fetch
reads as the zero object, and fetches every remote unless a fetch that began after this job asked for one already
did, and the job runs there. <dir> is then copied
back, job.json, pid and job.log left as this machine wrote them and result.json last,
adopted through long_running/desk_result.py only when it parses, so the runner
settles the pass from this process and its files as it does a local one. Every
exit of this script leaves exactly one whole result.json: a job that ends with none, as
when the Sprite restarts mid-pass or cannot be reached, gets one from
desk_result.py naming the error and marked `no_result`, so the runner fails and requeues the
pass at once, without the retry hold a tooling error gets, instead of waiting out its deadline. The Sprite then pushes the records the job posted to the drive's
cci relay, retried five times over about 40 seconds; a push that still fails
loses nothing, since the runner reposts the job's records when it settles the
pass. A `cancelled` mark
the runner writes while the job runs is forwarded, and each codex run the job
started there is cancelled. Exits with the job's status.
EOF2
  exit 2
}

[ $# -eq 3 ] || usage
export COPYFILE_DISABLE=1
sprite=$1 job=$2 dir=$3
here=$(cd "$(dirname "$0")" && pwd)
results=$here/long_running/desk_result.py
no_result() {
  python3 "$results" lost "$dir" "the job on Sprite $sprite ended with status $1 and no result, as when the Sprite restarts mid-pass or cannot be reached"
}
trap 'no_result $?' EXIT
mark=$(date +%s)
took() {
  now=$(date +%s)
  echo "sprite-job: $1 took $((now - mark)) s" >&2
  mark=$now
}
refresh_gh_token() {
  stamp=$state/gh-token-$sprite
  [ -z "$(find "$stamp" -mmin -4 2>/dev/null)" ] || return 0
  touch "$stamp"
  token=$(python3 -c '
import json, os, subprocess
argv = json.loads(os.environ.get("SPRITE_JOB_KEYS") or "{}").get("GH_TOKEN")
if argv:
    print(subprocess.run(argv, capture_output=True, text=True).stdout.strip())')
  [ -n "$token" ] || return 0
  printf "protocol=https\nhost=github.com\nusername=x-access-token\npassword=%s\n\n" "$token" |
    sprite exec -s "$sprite" -- git credential-cache --timeout=3900 store
}
plugin=$(cd "$here/../../.." && pwd)
checkout=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["checkout"])' "$dir/job.json")
drive=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["drive"])' "$dir/job.json")
registry=${CLAUDE_CONFIG_DIR:-$HOME/.claude}/long-running/drives/$drive.json
state=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["state_dir"])' "$registry")
program=$(python3 -c 'import json, sys; from pathlib import Path; entry = json.load(open(sys.argv[1])); print(entry.get("program") or Path(entry["state_dir"]).name)' "$registry")
plan=$(python3 "$here/rulings.py" plan --repo "$checkout" --drive "$drive")
took "reading the plan"

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
      next="$1.d/$2"
      if [ -d "$next" ]; then
        cat >/dev/null
      else
        rm -rf "$1.part" && mkdir -p "$1.part" "$1.d" && tar -xz -m -C "$1.part" && mv "$1.part" "$next" || exit 1
      fi
      [ -L "$1" ] || rm -rf "$1"
      rm -f "$1.link" && ln -s "$next" "$1.link" && python3 -c "import os, sys; os.replace(sys.argv[1], sys.argv[2])" "$1.link" "$1" && printf %s "$2" >"$1.digest" || exit 1
      for old in "$1.d"/*/; do
        old=${old%/}
        [ -d "$old" ] && [ "$old" != "$next" ] && flock -n -x "$old.lock" rm -rf "$old" "$old.lock"
      done
      true' sh "$1" "$sum"
  fi
}

ship "$plugin"
took "the plugin copy"
tar --no-xattrs -C "$dir" -cz . | sprite exec -s "$sprite" -- sh -c 'mkdir -p "$1" && tar -xz -m -C "$1"' sh "$dir"
took "the job copy"
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
took "the registry and register copy"
if [ -n "$plan" ]; then
  ship "$(dirname "$(dirname "$plan")")"
fi
took "the plan copy"

keys=$(python3 -c '
import json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import desk_result
for name, argv in json.loads(os.environ.get("SPRITE_JOB_KEYS") or "{}").items():
    if not (value := subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()):
        error = f"the {name} command printed nothing, so the job would run without {name}"
        desk_result.write(Path(sys.argv[1]), json.dumps({"error": error}) + "\n")
        sys.exit(f"sprite-job: {error}")
    print(f"{name}={value}")
' "$dir" "$here/long_running") || exit 1
took "reading the keys"
sprite exec -s "$sprite" --no-stdin -- sh -c '
  rm -f "$2.keys" && mkfifo -m 600 "$2.keys"
  vars=$(timeout 120 cat "$2.keys") || exit 1
  rm -f "$2.keys"
  nl=$(printf "\n_")
  IFS=${nl%_}
  set -f
  for line in $vars; do export "$line"; done
  unset IFS vars
  set +f
  export GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0= GIT_CONFIG_KEY_1=credential.helper "GIT_CONFIG_VALUE_1=!gh auth git-credential"
  [ -z "${GH_TOKEN:-}" ] || printf "protocol=https\nhost=github.com\nusername=x-access-token\npassword=%s\n\n" "$GH_TOKEN" |
    git credential-cache --timeout=3900 store
  started=$(date +%s)
  find "$(git -C "$3" rev-parse --path-format=absolute --git-common-dir)/refs" -type f ! -name "*.lock" -exec grep -LE "^([0-9a-f]{40}|ref: .+)$" {} + | xargs rm -f
  echo "sprite-job: the ref cleanup took $(($(date +%s) - started)) s" >&2
  started=$(date +%s)
  asked=$(date +%s%N)
  exec 9>"$3.fetch.lock"
  flock 9
  echo "sprite-job: waiting on the fetch lock took $(($(date +%s) - started)) s" >&2
  if [ "$(cat "$3.fetched" 2>/dev/null || echo 0)" -lt "$asked" ]; then
    began=$(date +%s%N)
    started=$(date +%s)
    git -C "$3" fetch --quiet --all
    echo "sprite-job: git fetch --all took $(($(date +%s) - started)) s" >&2
    printf %s "$began" >"$3.fetched"
  fi
  exec 9>&-
  real=$(readlink "$4")
  export SPRITE_TOOLS_PATH="$PATH"
  [ -z "${GH_TOKEN:-}" ] || export PATH="$real/skills/long-running/scripts/sprite-fresh-token:$PATH"
  case "$1" in
    /*) exec flock -s "$real.lock" python3 "$real${1#"$4"}" "$2" ;;
    *) exec flock -s "$real.lock" "$real/bin/lr" grade "$1" "$2" ;;
  esac' sh "$job" "$dir" "$checkout" "$plugin" &
job=$!
{ [ -z "$keys" ] || printf "%s\n" "$keys"; } | sprite exec -s "$sprite" -- sh -c '
  for _ in $(seq 600); do
    if [ -p "$1.keys" ]; then exec cat >"$1.keys"; fi
    sleep 0.2
  done
  exit 1' sh "$dir"
(
  forwarded=
  ticks=0
  while kill -0 "$job" 2>/dev/null; do
    if [ -z "$forwarded" ] && [ -e "$dir/cancelled" ]; then
      sprite exec -s "$sprite" -- sh -c 'cat >"$1/cancelled"; for run in "$1"/codex-*; do [ -e "$run" ] && codex-ask --cancel "$run"; done; true' sh "$dir" <"$dir/cancelled" || true
      forwarded=1
    fi
    if [ "$ticks" -eq 0 ]; then
      refresh_gh_token &
      ticks=4
    fi
    ticks=$((ticks - 1))
    sleep 60 </dev/null >/dev/null 2>&1
  done
) &
tender=$!
status=0
wait "$job" || status=$?
kill "$tender" 2>/dev/null || true
sprite exec -s "$sprite" --no-stdin -- sh -c 'cd "$1" && find . -mindepth 1 ! -path ./job.json ! -path ./pid ! -path ./job.log ! -path ./result.json | tar -cz --no-recursion -T -' sh "$dir" | tar -xz -m -C "$dir" || true
sprite exec -s "$sprite" --no-stdin -- cat "$dir/result.json" | python3 "$results" adopt "$dir"
no_result "$status"
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
