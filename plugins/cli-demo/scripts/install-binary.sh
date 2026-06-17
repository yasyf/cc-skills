#!/usr/bin/env bash
# Fetches the prebuilt evp binary into the plugin's persistent data directory and
# prints its absolute path on stdout.
#
# evp (https://github.com/HalFrgrd/evp) publishes ONE prebuilt artifact:
# x86_64-unknown-linux-musl. So unlike a host-matched download, the binary we fetch
# only runs natively on Linux/x86_64. On every other host (macOS, Linux/arm64) it is
# still downloaded — evp-run.sh bakes it into a linux/amd64 Docker image — but it must
# never be exec'd here, or the liveness check would fail forever and re-download every
# session. Hence the host-aware verify below.
#
# The binary lives under ${CLAUDE_PLUGIN_DATA} (which survives plugin updates, per the
# Claude Code plugin docs) rather than ${CLAUDE_PLUGIN_ROOT} (wiped on update).
#
# Idempotent: a no-op fast path when a working binary is already present. Run eagerly
# by the plugin's SessionStart hook, and lazily by the cli-demo skill / evp-run.sh.
# Diagnostics go to stderr; the resolved binary path is the only stdout line.
set -euo pipefail

# Pinned so the Docker image tags built from this binary are reproducible. Override
# with EVP_VERSION=x.y.z (no leading "v") to track a different release.
EVP_VERSION="${EVP_VERSION:-0.13.0}"

DATA_DIR="${CLAUDE_PLUGIN_DATA:-${XDG_CACHE_HOME:-$HOME/.cache}/cli-demo}"
BIN_DIR="$DATA_DIR/bin"
BIN_PATH="$BIN_DIR/evp"

# True only where the downloaded musl binary can run natively.
host_native() {
  [ "$(uname -s)" = "Linux" ] || return 1
  case "$(uname -m)" in x86_64 | amd64) return 0 ;; *) return 1 ;; esac
}

# Cheap structural check for non-native hosts: a non-empty file whose first 4 bytes
# are the ELF magic (0x7f 45 4c 46). od is present on both macOS and Linux.
is_elf() {
  [ -s "$1" ] || return 1
  [ "$(head -c 4 "$1" | od -An -tx1 2>/dev/null | tr -d ' \n')" = "7f454c46" ]
}

# Verify a candidate binary: exec it where that's possible, else check it's an ELF.
verify() {
  if host_native; then "$1" --version >/dev/null 2>&1; else is_elf "$1"; fi
}

# Fast path: a usable binary is already installed.
if [ -e "$BIN_PATH" ] && verify "$BIN_PATH"; then
  echo "$BIN_PATH"
  exit 0
fi

asset="evp-${EVP_VERSION}-x86_64-unknown-linux-musl.tar.gz"
url="https://github.com/HalFrgrd/evp/releases/download/v${EVP_VERSION}/${asset}"

mkdir -p "$BIN_DIR"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

echo "install-binary.sh: downloading $url" >&2
curl --fail --show-error --silent --location "$url" -o "$tmp/evp.tgz"
tar -C "$tmp" -xzf "$tmp/evp.tgz"

# The tarball extracts to evp-<ver>-x86_64-unknown-linux-musl/evp; find it without
# hard-coding the layout in case a future release nests it differently.
src=$(find "$tmp" -type f -name evp | head -n 1)
[ -n "$src" ] || {
  echo "install-binary.sh: no 'evp' binary inside $asset" >&2
  exit 1
}

mv "$src" "$BIN_PATH"
chmod +x "$BIN_PATH"

verify "$BIN_PATH" || {
  echo "install-binary.sh: downloaded evp failed verification" >&2
  exit 1
}
echo "install-binary.sh: installed $BIN_PATH (evp ${EVP_VERSION})" >&2
echo "$BIN_PATH"
