#!/bin/bash
set -eu

RUNNER_REPO="yasyf/binrun"
RUNNER_TAG="v0.8.0"
RUNNER_SHA_darwin_arm64="be67dab089951cae4d1abfdbcadebf79ef2c706d83235302de0962c3b5d23fbd"
RUNNER_SHA_darwin_amd64="7ee512c05d587dc56bcd363cb3b3c3e5495a525879314bc5b6dd6ecf08c23319"
RUNNER_SHA_linux_amd64="45084812f87f3451b0ad30985d5ce766738f41ff2c3e8464739117c251e86256"
RUNNER_SHA_linux_arm64="bb622f2c119ca9bd6f3c3f94e2658116c27b4e703c30005ea17da521f820f899"

fail() {
  echo "binrun.sh: $1" >&2
  exit 1
}

[ -n "${BINRUN_BIN:-}" ] && exec "$BINRUN_BIN" "$@"

passwd_user="$(id -un)"
eval "passwd_home=~$passwd_user"
case "$passwd_home" in \~*) fail "no passwd home for '$passwd_user'" ;; esac
RUNNER_DIR="${DAEMONKIT_HOME:-$passwd_home}/.daemonkit/binrun/$RUNNER_TAG"
RUNNER_BIN="$RUNNER_DIR/binrun"

[ -x "$RUNNER_BIN" ] && exec "$RUNNER_BIN" "$@"
if binrun="$(command -v binrun 2>/dev/null)"; then
  exec "$binrun" "$@"
fi

os="$(uname -s | tr '[:upper:]' '[:lower:]')"
arch="$(uname -m)"
case "$arch" in
  x86_64 | amd64) arch=amd64 ;;
  arm64 | aarch64) arch=arm64 ;;
esac
case "${os}_${arch}" in
  darwin_arm64) expected="$RUNNER_SHA_darwin_arm64" ;;
  darwin_amd64) expected="$RUNNER_SHA_darwin_amd64" ;;
  linux_amd64) expected="$RUNNER_SHA_linux_amd64" ;;
  linux_arm64) expected="$RUNNER_SHA_linux_arm64" ;;
  *) fail "no binrun runner for ${os}_${arch}" ;;
esac

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

asset="binrun_${RUNNER_TAG#v}_${os}_${arch}.tar.gz"
url="https://github.com/$RUNNER_REPO/releases/download/$RUNNER_TAG/$asset"

mkdir -p "$RUNNER_DIR"
tmpd="$(mktemp -d "$RUNNER_DIR/.binrun.XXXXXX")"
trap 'rm -rf "$tmpd"' EXIT
curl -fsSL --retry 2 --connect-timeout 10 --max-time 300 -o "$tmpd/$asset" "$url" \
  || fail "could not download the pinned binrun runner ($url)"
actual="$(sha256_of "$tmpd/$asset")"
[ "$actual" = "$expected" ] \
  || fail "binrun runner checksum mismatch for $asset (expected $expected, got $actual)"
tar -xzf "$tmpd/$asset" -C "$tmpd" || fail "could not extract $asset"
[ -x "$tmpd/binrun" ] || fail "$asset did not contain a binrun executable"
mv -f "$tmpd/binrun" "$RUNNER_BIN"
rm -rf "$tmpd"
trap - EXIT
exec "$RUNNER_BIN" "$@"
