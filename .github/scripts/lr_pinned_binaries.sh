#!/bin/bash
set -euo pipefail

pack=plugins/long-running
for attempt in 1 2 3; do
  "$pack/scripts/binrun.sh" -- fetch "$pack/bin/lr.binrun" && break
  [ "$attempt" -lt 3 ]
  sleep $((attempt * 15))
done

version=$CCI_VERSION
arch=$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/')
archive="cci_${version}_$(uname -s | tr '[:upper:]' '[:lower:]')_$arch.tar.gz"
release="https://github.com/yasyf/cc-inbox/releases/download/v$version"
tmp=$(mktemp -d)
curl -fsSL --retry 4 --retry-all-errors --retry-delay 15 -o "$tmp/checksums.txt" "$release/checksums.txt"
curl -fsSL --retry 4 --retry-all-errors --retry-delay 15 -o "$tmp/$archive" "$release/$archive"
(cd "$tmp" && grep " $archive\$" checksums.txt | sha256sum -c -)
mkdir -p "$HOME/.cache/cc-skills/cci/$version"
tar -xzf "$tmp/$archive" -C "$HOME/.cache/cc-skills/cci/$version"
