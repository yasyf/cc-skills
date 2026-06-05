#!/usr/bin/env bash
# Fetches the prebuilt slop-cop binary matching the host into the plugin's
# persistent data directory and prints its absolute path on stdout.
#
# The binary lives under ${CLAUDE_PLUGIN_DATA} (which survives plugin updates,
# per the Claude Code plugin docs) rather than ${CLAUDE_PLUGIN_ROOT} (which is
# wiped on update). It is fetched from the yasyf/slop-cop releases — the CLI is
# released from that repo, not bundled in this marketplace.
#
# Idempotent: a no-op fast path when a working binary is already present. Run
# eagerly by the plugin's SessionStart hook, and lazily by the slop-cop skill.
# Diagnostics go to stderr; the resolved binary path is the only stdout line.
set -euo pipefail

# Persistent home for the binary. Fall back to a cache dir when
# CLAUDE_PLUGIN_DATA is unset (e.g. running this script outside a plugin).
DATA_DIR="${CLAUDE_PLUGIN_DATA:-${XDG_CACHE_HOME:-$HOME/.cache}/slop-cop}"
BIN_DIR="$DATA_DIR/bin"
BIN_PATH="$BIN_DIR/slop-cop"

# Fast path: a working binary is already installed.
if [ -x "$BIN_PATH" ] && "$BIN_PATH" version >/dev/null 2>&1; then
  echo "$BIN_PATH"
  exit 0
fi

os=$(uname -s | tr '[:upper:]' '[:lower:]')
arch=$(uname -m)
case "$arch" in
  x86_64|amd64)  arch=amd64 ;;
  arm64|aarch64) arch=arm64 ;;
  *) echo "install-binary.sh: unsupported arch: $arch" >&2; exit 1 ;;
esac
case "$os" in
  darwin|linux) ;;
  *) echo "install-binary.sh: unsupported os: $os (use install-binary.ps1 on Windows)" >&2; exit 1 ;;
esac

tarball="slop-cop_${os}_${arch}.tar.gz"
# /releases/latest/download/<asset> is GitHub's native redirect to the newest
# release's asset; curl follows the 302.
url="https://github.com/yasyf/slop-cop/releases/latest/download/${tarball}"

mkdir -p "$BIN_DIR"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

echo "install-binary.sh: downloading $url" >&2
curl --fail --show-error --silent --location "$url" -o "$tmp/slop-cop.tgz"
tar -C "$tmp" -xzf "$tmp/slop-cop.tgz"

mv "$tmp/slop-cop_${os}_${arch}/slop-cop" "$BIN_PATH"
chmod +x "$BIN_PATH"

"$BIN_PATH" version >/dev/null
echo "install-binary.sh: installed $BIN_PATH" >&2
echo "$BIN_PATH"
