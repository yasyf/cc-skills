#!/bin/bash

set -euo pipefail

package=${1:?usage: ts-delta.sh <package-dir> [tsgo args]}
shift
root=$(git rev-parse --show-toplevel)
cd "$root"
[ ! -e node_modules ] || { echo "ts-delta: this checkout has node_modules; run its own typecheck, such as yarn tsc:<package>" >&2; exit 2; }
[ -f "$package/tsconfig.json" ] || { echo "ts-delta: $package has no tsconfig.json" >&2; exit 2; }
base=$(git merge-base HEAD origin/dev)

tsgo_binary() {
  local version os arch dir staging
  version=$(sed -n 's/^  resolution: "@typescript\/native-preview@npm:\(.*\)"$/\1/p' yarn.lock)
  os=$(uname -s | tr '[:upper:]' '[:lower:]')
  arch=$(uname -m)
  case $arch in x86_64) arch=x64 ;; aarch64) arch=arm64 ;; esac
  dir=${XDG_CACHE_HOME:-$HOME/.cache}/ts-delta/$version-$os-$arch
  if [ ! -x "$dir/package/lib/tsgo" ]; then
    mkdir -p "${dir%/*}"
    staging=$(mktemp -d "$dir.XXXXXX")
    npm pack "@typescript/native-preview-$os-$arch@$version" --pack-destination "$staging" --silent >/dev/null
    tar -xzf "$staging"/*.tgz -C "$staging"
    if [ -e "$dir" ]; then rm -rf "$staging"; else mv "$staging" "$dir"; fi
  fi
  echo "$dir/package/lib/tsgo"
}

diagnostic='error TS[0-9]+: '

errors() {
  local dir=$1 out=$2 status=0
  shift 2
  (cd "$dir" && "$tsgo" --noEmit -p "./$package" --preserveSymlinks false --composite false --pretty false --types "" "$@") >"$out.raw" 2>&1 || status=$?
  if [ "$status" -gt 1 ] || { [ "$status" = 1 ] && ! grep -qE "$diagnostic" "$out.raw"; } || grep -qvE "^[[:space:]]|$diagnostic" "$out.raw"; then
    cat "$out.raw" >&2
    echo "ts-delta: tsgo failed in $dir with status $status" >&2
    exit 2
  fi
  grep -E "$diagnostic" "$out.raw" >"$out" || true
}

tsgo=$(tsgo_binary)
baseline=$(mktemp -d)
trap 'rm -rf "$baseline"' EXIT
program=$("$tsgo" --listFilesOnly -p "./$package" --preserveSymlinks false --composite false --types "" "$@" | grep -F "$root/" | sed "s|^$root/||")
existing=()
while IFS= read -r path; do existing+=("$path"); done < <(printf '%s\n' tsconfig.base.json "$package" "$program" | xargs git ls-tree -r --name-only "$base" --)
git archive "$base" -- "${existing[@]}" | tar -x -C "$baseline"

errors "$baseline" "$baseline/base.txt" "$@"
errors "$root" "$baseline/work.txt" "$@"

awk -F'\t' '
  function key(line) { sub(/\([0-9]+,[0-9]+\)/, "", line); return line }
  NR == FNR { seen[key($0)]++; next }
  { k = key($0); if (seen[k] > 0) seen[k]--; else print }
' "$baseline/base.txt" "$baseline/work.txt" >"$baseline/new.txt"

new=$(wc -l <"$baseline/new.txt" | tr -d ' ')
echo "ts-delta: $new new errors in $package against $(git rev-parse --short "$base"); the baseline held $(wc -l <"$baseline/base.txt" | tr -d ' '), mostly packages missing from node_modules" >&2
cat "$baseline/new.txt"
[ "$new" = 0 ]
