#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $0 <prepared-task.json>" >&2
  exit 2
fi
task=$1
released=("ccx yasyf/cc-context checksums.txt" "cc-notes yasyf/cc-notes SHA256SUMS.txt ccn")
unfit=76

die() {
  echo "$*" >&2
  exit 1
}

forward=$(jq -er '.forward | [.host, .config] | select(all(type == "string" and length > 0)) | @tsv' "$task") ||
  die "$task names no ssh forward host and config, so no Sprite was reached"
IFS=$'\t' read -r host config <<<"$forward"

words=()
for entry in "${released[@]}"; do
  read -r bin repo sums alias <<<"$entry"
  asset=${bin}_linux_amd64
  printed=$("$bin" --version 2>&1) || die "$bin --version failed on this Mac: ${printed##*$'\n'}, so no tool was installed"
  tag=${printed%% *}
  [[ $tag =~ ^v[0-9]+(\.[0-9]+)+$ ]] ||
    die "this Mac's $bin --version printed '$printed', not a release tag, so no tool was installed"
  release=https://github.com/$repo/releases/download/$tag
  listed=$(curl -fsSL --retry 3 "$release/$sums") || die "curl could not read $release/$sums, so no tool was installed"
  digest=$(awk -v asset="$asset" '$2 == asset { print $1 }' <<<"$listed")
  [[ $digest =~ ^[0-9a-f]{64}$ ]] ||
    die "$release/$sums names no sha256 for $asset, so no tool was installed"
  words+=("$bin" "$tag" "$release/$asset" "$digest" "$alias")
done

read -ra tools <<<"${WORKER_TOOLS:-}"
placed=()
for pair in ${tools[@]+"${tools[@]}"}; do
  name=${pair%%=*} file=${pair#*=}
  [[ $pair == *=* && $name =~ ^[A-Za-z0-9._-]+$ && -f $file ]] ||
    die "WORKER_TOOLS holds $pair, and each of its words is NAME=FILE naming a file on this Mac, so no tool was installed"
  placed+=("$name" "$(base64 <"$file" | tr -d '\n')")
done

printf -v args ' %q' "$((${#placed[@]} / 2))" ${placed[@]+"${placed[@]}"} "${words[@]}"
status=0
ssh -F "$config" -o BatchMode=yes -o ConnectTimeout=30 "$host" "bash -s --$args" <<'SPRITE' || status=$?
set -eu
dest=$HOME/.local/bin
staged=$(mktemp -d)
trap 'rm -rf "$staged"' EXIT
mkdir -p "$dest"
placed=$1
shift
while [ "$placed" -gt 0 ]; do
  name=$1 text=$2
  shift 2
  placed=$((placed - 1))
  printf '%s' "$text" | base64 -d >"$staged/$name"
  chmod 755 "$staged/$name"
  mv -f "$staged/$name" "$dest/$name"
  found=$(bash -lc "command -v $name" 2>/dev/null) || found=nothing
  [ "$found" = "$dest/$name" ] || { echo "a login shell runs $name from $found, not the $dest/$name placed for it" >&2; exit 1; }
  echo "$name placed"
done
while [ $# -gt 0 ]; do
  bin=$1 tag=$2 url=$3 sha=$4 alias=$5
  shift 5
  curl -fsSL --retry 3 -o "$staged/$bin" "$url"
  echo "$sha  $staged/$bin" | sha256sum -c --status || { echo "$url does not hash to $sha" >&2; exit 1; }
  chmod 755 "$staged/$bin"
  mv -f "$staged/$bin" "$dest/$bin"
  [ -z "$alias" ] || ln -sfn "$bin" "$dest/$alias"
  for name in "$bin" $alias; do
    found=$(bash -lc "command -v $name" 2>/dev/null) || found=nothing
    printed=$(bash -lc "$name --version" 2>/dev/null) || printed=
    [ "$found" = "$dest/$name" ] && [ "${printed%% *}" = "$tag" ] ||
      { echo "a login shell runs $name from $found, printing '${printed%% *}', not $tag from $dest/$name" >&2; exit 1; }
    echo "$name $tag"
  done
done
sudo -n /.sprite/bin/rustup toolchain install stable --profile minimal --target wasm32-unknown-unknown --no-self-update >&2
echo "rust stable wasm32-unknown-unknown"
zig=$HOME/.local/share/zig/0.14.0
if [ "$("$dest/zig" version 2>/dev/null)" != 0.14.0 ]; then
  url=https://ziglang.org/download/0.14.0/zig-linux-x86_64-0.14.0.tar.xz
  sha=473ec26806133cf4d1918caf1a410f8403a13d979726a9045b421b685031a982
  curl -fsSL --retry 3 -o "$staged/zig.tar.xz" "$url"
  echo "$sha  $staged/zig.tar.xz" | sha256sum -c --status || { echo "$url does not hash to $sha" >&2; exit 1; }
  rm -rf "$zig"
  mkdir -p "$zig"
  tar -xJf "$staged/zig.tar.xz" -C "$zig" --strip-components=1
  ln -sfn "$zig/zig" "$dest/zig"
fi
echo "zig $("$dest/zig" version)"
SPRITE
[ "$status" -eq 0 ] || [ "$status" -eq 255 ] || exit "$unfit"
exit "$status"
