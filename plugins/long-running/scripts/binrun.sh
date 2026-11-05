#!/bin/bash
set -eu

RUNNER_REPO="yasyf/binrun"
RUNNER_TAG="v0.9.0"
RUNNER_SHA_darwin_arm64="db63eb56ef291e6c0a57d4120de21e9457802e53768d60af131658b12239466e"
RUNNER_SHA_darwin_amd64="3e61231d4d4b471ea873084a59e46308a2ea8a3bd90c588e97c0d19228de302c"
RUNNER_SHA_linux_amd64="fa1991af7943c1465429de7792f88bdfe35174c6bfea5a13d91980cdab914870"
RUNNER_SHA_linux_arm64="27c8e6e53d6e7eceaed45b3e6eb0d373eae7e7fc5e7fa3cb67e66adeb660438d"

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
