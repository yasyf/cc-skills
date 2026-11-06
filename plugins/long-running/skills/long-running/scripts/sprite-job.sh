#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF2'
usage: sprite-job.sh <sprite> <job> <dir>

Runs the desk job <dir> on the Sprite <sprite> through <job>, either a grader
name for `lr grade`, finder or fidelity, or the absolute path of a Python script
in this plugin, at the same absolute paths, so job.json and every path it names read
the same there. The Sprite mirrors this machine's home at its own path, has
codex, codex-ask, ccx and ccn installed,
and holds a clone at the job's checkout whose remotes fetch the branches the
job reads. It holds no cc-notes: the coordinator reads the answers a pass needs
and puts them in <dir>. `lr grade` moves that clone's .git to the Sprite's local disk on the
first pass that finds it on the network disk home lives on, takes the clone's
whole history when it is shallow, since a shallow trunk reads every commit behind
a head as the head's own, and starts one background move of the clone's worktree and the tools a pass reads. <dir> and each
copy this script ships land on the local disk too, behind a link at the same path,
and <dir> goes once it is copied back.

SPRITE_JOB_KEYS, a JSON object of environment names to the commands that print
their values, such as {"GH_TOKEN": ["gh", "auth", "token"]}, is run here at
start; each value reaches the job's process on the Sprite through a named pipe
the job reads once and its environment only, never a stored file or an argument,
written by a short exec's standard input. The job runs detached on the Sprite in a session of its own,
its output and exit status in .out and .status files beside <dir>'s directory on the local disk, so a job
that loses <dir> still reports, and no exec stream outlives a short call, so a dropped one cannot lose a pass. Every 20
seconds one short exec returns the output written since the last and whether the job runs, exited with
a status, or is gone, and that output lands in job.log here; a Sprite no poll reaches 15 times running
ends the pass with status 255. A command that prints
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
reads as the zero object. Once `lr grade` has moved its .git to the local disk, it fetches every remote unless a fetch
that began after this job asked for one already did; before that it fetches nothing, since a stale clone's catch-up
on the network disk holds the lock `lr grade` needs for the move, for an hour on dar-fleet-4. The job runs there. <dir> is then copied
back, job.json, pid, job.log, cards.md, landed.json and answers.json left as this machine wrote them (the job only reads them, and landed.json alone is 350 KB, more than a dropped Sprite CLI stream resumes) and result.json last,
adopted through long_running/desk_result.py only when it parses, so the runner
settles the pass from this process and its files as it does a local one. Every
exit of this script leaves exactly one whole result.json: a job that ends with none, as
when the Sprite restarts mid-pass or cannot be reached, gets one from
desk_result.py naming the error and marked `no_result`, so the runner fails and requeues the
pass at once, without the retry hold a tooling error gets, instead of waiting out its deadline. The job's first act on
the Sprite writes its process id and start time to job-process, since setsid makes that process the leader of its
own process group; the runner's kill of a stalled pass checks both before it signals that group and nothing else. A finder or fidelity job writes its records to posts.jsonl for the
coordinator to publish after the files return, without a cci replica on the grader.
A job named by a Python script path then pushes the records it posted to the drive's
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
  if [ "$(sprite exec -s "$sprite" --no-stdin -- sh -c 'mkdir -p "$(dirname "$1")"; [ ! -d "$1/" ] || cat "$1.digest" 2>/dev/null' sh "$1")" != "$sum" ]; then
    tar --no-xattrs --exclude=__pycache__ --exclude=.in_use --exclude=.orphaned_at -C "$1" -cz . | sprite exec -s "$sprite" -- flock "$1.lock" sh -c '
      if [ -d "$1/" ] && [ "$(cat "$1.digest" 2>/dev/null)" = "$2" ]; then exec cat >/dev/null; fi
      if [ ! -e "$1.d" ]; then
        kept=/tmp/lr-ship/$(printf %s "$1" | sha256sum | cut -c1-16)
        mkdir -p "$kept" && ln -sfn "$kept" "$1.d" || exit 1
      fi
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
      for other in "$(dirname "$1")"/*.d; do
        [ -L "$other" ] && [ "$other" != "$1.d" ] && [ -n "$(find "$other/" -maxdepth 0 -mmin +60)" ] || continue
        for old in "$other"/*/; do
          old=${old%/}
          [ -d "$old" ] && flock -n -x "$old.lock" rm -rf "$old" "$old.lock"
        done
        rmdir "$(readlink "$other")" 2>/dev/null && rm -f "$other" "${other%.d}" "${other%.d}.digest" "${other%.d}.lock"
      done
      true' sh "$1" "$sum"
  fi
}

ship "$plugin"
took "the plugin copy"
tar --no-xattrs -C "$dir" -cz . | sprite exec -s "$sprite" -- sh -c '
  if [ ! -e "$1" ]; then
    mkdir -p "$(dirname "$1")" /tmp/lr-scratch && ln -sfn "$(mktemp -d /tmp/lr-scratch/XXXXXXXX)" "$1" || exit 1
  fi
  tar -xz -m -C "$1"' sh "$dir"
took "the job copy"
python3 -c 'import json, sys; entry = json.load(open(sys.argv[1])); entry["checkout"] = sys.argv[2]; print(json.dumps(entry))' "$registry" "$checkout" |
  sprite exec -s "$sprite" -- sh -c 'mkdir -p "$(dirname "$1")" && cat >"$1.$$" && mv "$1.$$" "$1"' sh "$registry"
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
run='
  printf "%s %s\n" "$$" "$(cut -d" " -f22 "/proc/$$/stat")" >"$2/job-process"
  rm -f "$2.keys" && mkfifo -m 600 "$2.keys"
  vars=$(timeout 120 cat "$2.keys") || exit 1
  rm -f "$2.keys"
  nl=$(printf "\n_")
  IFS=${nl%_}
  set -f
  for line in $vars; do export "$line"; done
  unset IFS vars
  set +f
  export GIT_CONFIG_COUNT=4 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0= GIT_CONFIG_KEY_1=credential.helper "GIT_CONFIG_VALUE_1=!gh auth git-credential" GIT_CONFIG_KEY_2=core.fsync GIT_CONFIG_VALUE_2=committed GIT_CONFIG_KEY_3=core.fsyncMethod GIT_CONFIG_VALUE_3=batch
  [ -z "${GH_TOKEN:-}" ] || printf "protocol=https\nhost=github.com\nusername=x-access-token\npassword=%s\n\n" "$GH_TOKEN" |
    git credential-cache --timeout=3900 store
  started=$(date +%s)
  common=$(git -C "$3" rev-parse --path-format=absolute --git-common-dir)
  find "$common/refs" -type f ! -name "*.lock" -exec grep -LE "^([0-9a-f]{40}|ref: .+)$" {} + | xargs rm -f
  find "$common/objects" -mindepth 2 -maxdepth 2 -path "$common/objects/??/*" -type f -size 0 ! -name "tmp_*" -delete
  echo "sprite-job: the ref cleanup took $(($(date +%s) - started)) s" >&2
  if [ -L "$3/.git" ]; then
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
  fi
  real=$(readlink "$4")
  export SPRITE_TOOLS_PATH="$PATH"
  [ -z "${GH_TOKEN:-}" ] || export PATH="$real/skills/long-running/scripts/sprite-fresh-token:$PATH"
  case "$1" in
    /*) flock -s "$real.lock" python3 "$real${1#"$4"}" "$2" ;;
    *) flock -s "$real.lock" "$real/bin/lr" grade "$1" "$2" ;;
  esac
  printf "%s\n" "$?" >"$5.status.part" && mv "$5.status.part" "$5.status"'
sprite exec -s "$sprite" --no-stdin -- sh -c '
  disk=$(readlink -f "$2")
  rm -f "$2/job-process" "$disk.status" "$disk.out"
  setsid sh -c "$1" sh "$3" "$2" "$4" "$5" "$disk" </dev/null >"$disk.out" 2>&1 &
  for _ in $(seq 100); do
    [ ! -s "$2/job-process" ] || exit 0
    sleep 0.1
  done
  exit 1' sh "$run" "$dir" "$job" "$checkout" "$plugin" || exit 1
took "starting the job"
{ [ -z "$keys" ] || printf "%s\n" "$keys"; } | sprite exec -s "$sprite" -- sh -c '
  for _ in $(seq 600); do
    if [ -p "$1.keys" ]; then exec cat >"$1.keys"; fi
    sleep 0.2
  done
  exit 1' sh "$dir"
status=
offset=0
misses=0
forwarded=
ticks=0
delta=$(mktemp)
while [ -z "$status" ]; do
  if [ -z "$forwarded" ] && [ -e "$dir/cancelled" ]; then
    sprite exec -s "$sprite" -- sh -c 'cat >"$1/cancelled"; for run in "$1"/codex-*; do [ -e "$run" ] && codex-ask --cancel "$run"; done; true' sh "$dir" <"$dir/cancelled" || true
    forwarded=1
  fi
  if [ "$ticks" -eq 0 ]; then
    refresh_gh_token &
    ticks=12
  fi
  ticks=$((ticks - 1))
  if said=$(sprite exec -s "$sprite" --no-stdin -- sh -c '
    disk=$(readlink -f "$1")
    state=gone
    if read -r pid start 2>/dev/null <"$1/job-process" && [ "$(cut -d" " -f22 "/proc/$pid/stat" 2>/dev/null)" = "$start" ]; then
      state=running
    fi
    [ ! -e "$disk.status" ] || state=$(cat "$disk.status")
    tail -c +"$2" "$disk.out" 2>/dev/null
    echo "$state" >&2' sh "$dir" "$((offset + 1))" 2>&1 >"$delta"); then
    misses=0
  else
    misses=$((misses + 1))
  fi
  cat "$delta" >&2
  offset=$((offset + $(wc -c <"$delta")))
  said=$(printf "%s\n" "$said" | tail -n 1)
  case $said in
    gone) status=1 ;;
    running) sleep 20 ;;
    '' | *[!0-9]*) if [ "$misses" -ge 15 ]; then status=255; else sleep 20; fi ;;
    *) status=$said ;;
  esac
done
rm -f "$delta"
archive=$(mktemp)
if sprite exec -s "$sprite" --no-stdin -- sh -c 'cd "$1" || exit 2; find . -mindepth 1 ! -path ./job.json ! -path ./pid ! -path ./job.log ! -path ./result.json ! -path ./cards.md ! -path ./landed.json ! -path ./answers.json | tar -cz --no-recursion -T -; [ $? -le 1 ]' sh "$dir" >"$archive" &&
  tar -tzf "$archive" >/dev/null && tar -xz -m -f "$archive" -C "$dir"; then
  rm -f "$archive"
else
  copied=$?
  rm -f "$archive"
  exit "$((status ? status : copied))"
fi
sprite exec -s "$sprite" --no-stdin -- cat "$dir/result.json" | python3 "$results" adopt "$dir"
no_result "$status"
sprite exec -s "$sprite" --no-stdin -- sh -c 'disk=$(readlink -f "$1"); rm -f "$disk.status" "$disk.out"; [ ! -L "$1" ] || rm -rf "$(readlink "$1")" "$1"' sh "$dir" || true
case "$job" in
/*)
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
;;
esac
exit "$status"
