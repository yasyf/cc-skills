#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF2'
usage: sprite-cargo.sh <sprite> <lane> <repo> <ref> <command> <path>...

Builds <ref> of the local checkout <repo> on the Sprite <sprite>. The <path>s of
<ref> are archived, extracted into a fresh source tree on the Sprite, and
<command>, one shell string, runs there through sh -c with CARGO_TARGET_DIR set to the lane's own
target directory, <root>/target-<lane>. The source tree is removed when
<command> exits, and the script exits with <command>'s status.

Before extracting, every <root>/target* and <root>/src-* directory other than the
lane's own whose newest entry within two levels is more than six hours old is
removed, so idle lanes' targets and the trees of interrupted runs do not fill the
Sprite's disk. A run holds a shared flock on <dir>.lock for its target and tree
while <command> runs, and removal takes that lock exclusively without waiting,
so a directory a running build holds is never removed however old it looks.

  SPRITE_CARGO_ROOT   directory on the Sprite holding targets and trees, default /work
EOF2
  exit 2
}

[ $# -ge 6 ] || usage
sprite=$1 lane=$2 repo=$3 ref=$4 command=$5
shift 5

root=${SPRITE_CARGO_ROOT:-/work}
target=$root/target-$lane
src=$root/src-$lane-$(date +%s)-$$

git -C "$repo" archive --format=tar "$ref" "$@" | gzip -1 |
  sprite exec -s "$sprite" -- sh -c '
    root=$1 target=$2 src=$3
    for d in "$root"/target* "$root"/src-*; do
      case $d in *.lock) continue ;; esac
      [ -d "$d" ] && [ "$d" != "$target" ] || continue
      exec 9>"$d.lock"
      if flock -n -x 9 && [ -z "$(find "$d" -maxdepth 2 -mmin -360 -print -quit)" ]; then
        echo "sprite-cargo: removing $d, idle over 6 h" >&2
        rm -rf "$d"
      fi
      exec 9>&-
    done
    mkdir -p "$target" "$src" && touch "$target" && tar -xz -m -C "$src"' sh "$root" "$target" "$src"

sprite exec -s "$sprite" --no-stdin --dir "$src" --env "CARGO_TARGET_DIR=$target,CARGO_TERM_COLOR=never" -- \
  flock -s "$target.lock" flock -s "$src.lock" sh -c '(eval "$1"); status=$?; rm -rf "$2" "$2.lock"; exit $status' sh "$command" "$src"
