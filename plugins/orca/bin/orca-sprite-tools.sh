#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $0 <prepared-task.json>" >&2
  exit 2
fi
task=$1
released=("ccx yasyf/cc-context" "cc-notes yasyf/cc-notes ccn")

die() {
  echo "$*" >&2
  exit 1
}

forward=$(jq -er '.forward | [.host, .config] | select(all(type == "string" and length > 0)) | @tsv' "$task") ||
  die "$task names no ssh forward host and config, so no Sprite was reached"
IFS=$'\t' read -r host config <<<"$forward"

words=()
for entry in "${released[@]}"; do
  read -r bin repo alias <<<"$entry"
  asset=${bin}_linux_amd64
  latest=$(gh api "repos/$repo/releases/latest" --jq "[.tag_name, (.assets[] | select(.name == \"$asset\") | .browser_download_url, .digest)] | @tsv") ||
    die "gh could not read the latest $repo release, so no tool was installed"
  IFS=$'\t' read -r tag url digest <<<"$latest"
  [[ $tag =~ ^v[0-9]+(\.[0-9]+)+$ && $url == "https://github.com/$repo/releases/download/$tag/$asset" && $digest =~ ^sha256:[0-9a-f]{64}$ ]] ||
    die "the latest $repo release reads '$latest', not a version tag with a $asset asset and its sha256 digest, so no tool was installed"
  words+=("$bin" "$tag" "$url" "${digest#sha256:}" "$alias")
done

printf -v args ' %q' "${words[@]}"
ssh -F "$config" -o BatchMode=yes -o ConnectTimeout=30 "$host" "bash -s --$args" <<'SPRITE'
set -eu
dest=$HOME/.local/bin
staged=$(mktemp -d)
trap 'rm -rf "$staged"' EXIT
mkdir -p "$dest"
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
SPRITE
