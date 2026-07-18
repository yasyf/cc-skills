#!/usr/bin/env bash
# Generate a Developer ID signing keypair + CSR; the private key never leaves here.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
gen-csr.sh --out-dir DIR [--common-name CN] [--email EMAIL] [--key FILE]

Create an RSA-2048 private key (Apple Developer ID requires RSA 2048) and a
matching PKCS#10 CSR. Upload ONLY the .csr to developer.apple.com (Certificates
-> Developer ID Application -> upload CSR); the .key stays on this machine. Apple
sets the certificate subject from your account, so the CSR subject is cosmetic.

Outputs (in --out-dir, default ./devid-csr):
  developer-id.key   RSA-2048 private key, mode 0600 — KEEP LOCAL, never upload
  developer-id.csr   the CSR to upload to the portal

Args:
  --out-dir DIR     where to write the key + CSR (default: ./devid-csr)
  --common-name CN  CSR subject common name (default: "Developer ID Application")
  --email EMAIL     optional emailAddress in the CSR subject
  --key FILE        reuse an existing local key (fresh CSR against the same key)
  -n, --dry-run     print the openssl commands without running them
EOF
}

die() { echo "gen-csr.sh: $*" >&2; exit 1; }

OUT_DIR="./devid-csr"
COMMON_NAME="Developer ID Application"
EMAIL=""
EXISTING_KEY=""
DRY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --out-dir)      OUT_DIR="$2"; shift 2 ;;
    --common-name)  COMMON_NAME="$2"; shift 2 ;;
    --email)        EMAIL="$2"; shift 2 ;;
    --key)          EXISTING_KEY="$2"; shift 2 ;;
    -n|--dry-run)   DRY=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    -*)             die "unknown flag: $1" ;;
    *)              die "unexpected argument: $1" ;;
  esac
done

command -v openssl >/dev/null 2>&1 || die "openssl not found on PATH"
[ -n "$COMMON_NAME" ] || die "--common-name must not be empty"

KEY="$OUT_DIR/developer-id.key"
CSR="$OUT_DIR/developer-id.csr"

SUBJ="/CN=$COMMON_NAME"
[ -n "$EMAIL" ] && SUBJ="$SUBJ/emailAddress=$EMAIL"

run() {
  if [ -n "$DRY" ]; then
    printf '  '; printf '%q ' "$@"; printf '\n'
  else
    "$@"
  fi
}

if [ -n "$EXISTING_KEY" ]; then
  [ -f "$EXISTING_KEY" ] || die "existing key not found: $EXISTING_KEY"
  KEY="$EXISTING_KEY"
fi

if [ -z "$DRY" ]; then
  mkdir -p "$OUT_DIR"
  umask 077
fi

if [ -n "$EXISTING_KEY" ]; then
  run openssl req -new -key "$KEY" -subj "$SUBJ" -out "$CSR"
else
  # -nodes: unencrypted key so codesign/Keychain use it without a passphrase.
  run openssl req -new -newkey rsa:2048 -nodes -keyout "$KEY" -subj "$SUBJ" -out "$CSR"
fi

[ -n "$DRY" ] && exit 0

chmod 600 "$KEY"
openssl req -in "$CSR" -noout -verify >/dev/null 2>&1 \
  || die "generated CSR failed self-verification: $CSR"

echo "gen-csr.sh: wrote key + CSR"
echo "  private key (KEEP LOCAL, never upload): $KEY"
echo "  CSR (upload to developer.apple.com):    $CSR"
echo
echo "Next: developer.apple.com -> Certificates -> + -> Developer ID Application"
echo "      -> upload $CSR -> download the .cer -> assemble-p12.sh"
