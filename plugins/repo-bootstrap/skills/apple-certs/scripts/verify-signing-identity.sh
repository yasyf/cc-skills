#!/usr/bin/env bash
# Prove a Developer ID .p12 signs: import into a throwaway keychain, then codesign.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
verify-signing-identity.sh --p12 FILE --password PW [--keep]

End-to-end proof the assembled p12 actually signs code, without touching your
login keychain. Runs the full-chain guard, imports the p12 into a throwaway
keychain, lists the identity (`security find-identity`), signs a scratch binary,
and verifies that signature (`codesign --verify --strict`). The throwaway
keychain is deleted on exit.

Args:
  --p12 FILE      the p12 from assemble-p12.sh
  --password PW   the p12 export password (use '' for none)
  --keep          leave the throwaway keychain in place (debugging)

macOS only (uses `security` and `codesign`).
EOF
}

die() { echo "verify-signing-identity.sh: $*" >&2; exit 1; }

P12=""; PW=""; PW_SET=""; KEEP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --p12)       P12="$2"; shift 2 ;;
    --password)  PW="$2"; PW_SET=1; shift 2 ;;
    --keep)      KEEP=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    -*)          die "unknown flag: $1" ;;
    *)           die "unexpected argument: $1" ;;
  esac
done

command -v security >/dev/null 2>&1 || die "security not found — this script is macOS only"
command -v codesign >/dev/null 2>&1 || die "codesign not found — this script is macOS only"
[ -n "$P12" ] || die "--p12 is required"
[ -f "$P12" ] || die "p12 not found: $P12"
[ -n "$PW_SET" ] || die "--password is required (pass '' for an empty password)"

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
CHECK_CHAIN="$SELF_DIR/check-p12-chain.sh"
[ -x "$CHECK_CHAIN" ] || die "sibling guard not found/executable: $CHECK_CHAIN"

# Gate on the full chain before we bother importing anything.
"$CHECK_CHAIN" --p12 "$P12" --password "$PW" || die "p12 failed the full-chain guard"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/p12verify.XXXXXX")"
KEYCHAIN="$WORK/verify.keychain-db"
KP="verify-$$-$RANDOM"
ORIG_KEYCHAINS="$(security list-keychains -d user | sed 's/[[:space:]]*"\(.*\)"/\1/')"

cleanup() {
  # shellcheck disable=SC2086
  security list-keychains -d user -s $ORIG_KEYCHAINS >/dev/null 2>&1 || true
  if [ -z "$KEEP" ]; then
    security delete-keychain "$KEYCHAIN" >/dev/null 2>&1 || true
    rm -rf "$WORK"
  else
    echo "verify-signing-identity.sh: kept keychain $KEYCHAIN (password $KP)" >&2
  fi
}
trap cleanup EXIT

security create-keychain -p "$KP" "$KEYCHAIN"
security set-keychain-settings "$KEYCHAIN"
security unlock-keychain -p "$KP" "$KEYCHAIN"
# shellcheck disable=SC2086
security list-keychains -d user -s "$KEYCHAIN" $ORIG_KEYCHAINS >/dev/null

security import "$P12" -k "$KEYCHAIN" -P "$PW" -T /usr/bin/codesign -A -f pkcs12 \
  || die "security import failed (wrong password, or a p12 macOS can't read)"
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KP" "$KEYCHAIN" >/dev/null 2>&1

echo "=== security find-identity -v -p codesigning ==="
security find-identity -v -p codesigning "$KEYCHAIN"

IDENTITY="$(security find-identity -v -p codesigning "$KEYCHAIN" | awk '$1 ~ /^[0-9]+\)$/ {print $2; exit}')"
[ -n "$IDENTITY" ] || die "no codesigning identity found in the imported p12"

SCRATCH="$WORK/scratch-bin"
cp /bin/ls "$SCRATCH"
codesign --force --options runtime --timestamp=none -s "$IDENTITY" --keychain "$KEYCHAIN" "$SCRATCH" \
  || die "scratch codesign failed with identity $IDENTITY"
codesign --verify --strict --verbose=2 "$SCRATCH" \
  || die "codesign --verify failed on the scratch binary"

echo "verify-signing-identity.sh: OK — identity $IDENTITY signed and verified a scratch binary"
