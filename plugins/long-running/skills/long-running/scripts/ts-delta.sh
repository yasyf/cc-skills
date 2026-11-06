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
git archive -o "$baseline/base.tar" "$base" -- "${existing[@]}"
tar -xf "$baseline/base.tar" -C "$baseline"

errors "$baseline" "$baseline/base.txt" "$@"
errors "$root" "$baseline/work.txt" "$@"

awk -v base_root="$baseline" -v head_root="$root" -v left_out="$baseline/left-out.txt" '
  BEGIN { quote = sprintf("%c", 39) }
  function parse(line, at) {
    match(line, /\([0-9]+,[0-9]+\): /)
    file = substr(line, 1, RSTART - 1)
    split(substr(line, RSTART + 1, RLENGTH - 4), at, ",")
    row = at[1] + 0
    said = substr(line, RSTART + RLENGTH)
  }
  function text(root, path, n, key, line, count) {
    key = root SUBSEP path
    if (!(key in loaded)) {
      loaded[key] = 1
      while ((getline line < (root "/" path)) > 0) source[key, ++count] = line
      close(root "/" path)
    }
    line = source[key, n]
    gsub(/^[ \t]+|[ \t]+$/, "", line)
    return line
  }
  function named(message) {
    match(message, quote "[^" quote "]*" quote)
    return substr(message, RSTART + 1, RLENGTH - 2)
  }
  function lacking(message) {
    return message ~ /^error TS(2580|2582|2591|2593): / || message ~ /^error TS(2307|2792|7016): / && named(message) !~ /^[.\/]/
  }
  function bound(root, path, n, specifier, statement, words, count, i, last, names) {
    statement = text(root, path, n)
    if (index(statement, "\"" specifier "\"") == 0 && index(statement, quote specifier quote) == 0 || statement ~ /(import|require)\(/) return ""
    for (i = n; statement !~ /^(import|export)[ \t{*]/; statement = text(root, path, i) " " statement) if (--i < 1 || n - i > 50) return ""
    sub(/[ \t]from[ \t].*$/, "", statement)
    sub(/^(import|export)[ \t]+(type[ \t]+)?/, "", statement)
    count = split(statement, words, /[ \t,{}]+/)
    for (i = 1; i <= count; i++) {
      if (words[i] == "" || words[i] == "type" || words[i] == "*" || words[i] ~ "^[\"" quote "]") continue
      if (words[i] == "as") { last = words[++i]; continue }
      if (last != "") names = names " " last
      last = words[i]
    }
    return last == "" ? names : names " " last
  }
  function uses(line, names, list, count, i, at, step) {
    count = split(names, list, " ")
    for (i = 1; i <= count; i++) {
      for (at = index(line, list[i]); at > 0; at = step ? at + step : 0) {
        if ((at == 1 || substr(line, at - 1, 1) !~ /[A-Za-z0-9_$.]/) && substr(line, at + length(list[i]), 1) !~ /[A-Za-z0-9_$]/) return 1
        step = index(substr(line, at + 1), list[i])
      }
    }
    return 0
  }
  function classify(side, root, total, i, line) {
    for (i = 1; i <= total; i++) {
      parse(lines[side, i])
      if (!lacking(said)) continue
      missing[side, i] = 1
      imports[side, file] = imports[side, file] bound(root, file, row, named(said))
    }
    for (i = 1; i <= total; i++) {
      if (missing[side, i]) continue
      parse(lines[side, i])
      line = text(root, file, row)
      if (uses(line, imports[side, file])) missing[side, i] = 1
      else keys[side, i] = file SUBSEP said SUBSEP line
    }
  }
  NR == FNR { lines["base", ++bases] = $0; next }
  { lines["head", ++heads] = $0 }
  END {
    classify("base", base_root, bases)
    classify("head", head_root, heads)
    for (i = 1; i <= bases; i++) if (!missing["base", i]) seen[keys["base", i]]++
    for (i = 1; i <= heads; i++) {
      if (missing["head", i]) { dropped++; continue }
      if (seen[keys["head", i]] > 0) seen[keys["head", i]]--
      else print lines["head", i]
    }
    print dropped + 0 > left_out
  }
' "$baseline/base.txt" "$baseline/work.txt" >"$baseline/new.txt"

new=$(wc -l <"$baseline/new.txt" | tr -d ' ')
echo "ts-delta: $new new errors in $package against $(git rev-parse --short "$base"), $(cat "$baseline/left-out.txt") from types this checkout lacks left out; the baseline held $(wc -l <"$baseline/base.txt" | tr -d ' ')" >&2
cat "$baseline/new.txt"
[ "$new" = 0 ]
