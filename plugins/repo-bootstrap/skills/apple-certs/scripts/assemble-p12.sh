#!/usr/bin/env bash
# Assemble a full-chain Developer ID .p12: leaf + intermediate + Apple Root CA.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
assemble-p12.sh --cert LEAF.cer --key KEY [--out FILE] [--password PW]
assemble-p12.sh --self-test

Combine the leaf certificate downloaded from developer.apple.com with the private
key from gen-csr.sh AND the Developer ID intermediate + Apple Root CA fetched
(and SHA-256-pinned) from apple.com, into one PKCS#12 ready for `security import`
and `MACOS_SIGN_P12`. The full chain is mandatory: a leaf-only p12 signs cleanly
but SIGKILLs at exec (exit 137, unsatisfiable DR) — the quill failure. The final
step re-opens the p12 and fails unless all three certs are present and verify.

Args:
  --cert LEAF.cer   leaf cert from the portal (.cer DER, or PEM)
  --key KEY         private key from gen-csr.sh (developer-id.key)
  --out FILE        output p12 (default: developer-id.p12)
  --password PW     p12 export password; if omitted, a strong one is generated
                    and printed (store it as MACOS_SIGN_PASSWORD)
  --self-test       build a dummy PKI and prove the full-chain guard offline
                    (no network, no Apple certs) — accepts a 3-cert p12, rejects
                    a leaf-only p12

Env:
  P12_PASSWORD      alternative to --password
EOF
}

die() { echo "assemble-p12.sh: $*" >&2; exit 1; }

# Pinned Apple certificates (verified 2026-07-18 against apple.com; DER SHA-256).
APPLE_ROOT_URL="https://www.apple.com/appleca/AppleIncRootCertificate.cer"
APPLE_ROOT_SHA256="B0B1730ECBC7FF4505142C49F1295E6EDA6BCAED7E2C68C5BE91B5A11001F024"
DEVID_G1_URL="https://www.apple.com/certificateauthority/DeveloperIDCA.cer"
DEVID_G1_SHA256="7AFC9D01A62F03A2DE9637936D4AFE68090D2DE18D03F29C88CFB0B1BA63587F"
DEVID_G2_URL="https://www.apple.com/certificateauthority/DeveloperIDG2CA.cer"
DEVID_G2_SHA256="F16CD3C54C7F83CEA4BF1A3E6A0819C8AAA8E4A1528FD144715F350643D2DF3A"

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
CHECK_CHAIN="$SELF_DIR/check-p12-chain.sh"

CERT=""; KEY=""; OUT="developer-id.p12"; PW="${P12_PASSWORD:-}"; PW_SET=""; SELFTEST=""
[ -n "${P12_PASSWORD:-}" ] && PW_SET=1
while [ $# -gt 0 ]; do
  case "$1" in
    --cert)       CERT="$2"; shift 2 ;;
    --key)        KEY="$2"; shift 2 ;;
    --out)        OUT="$2"; shift 2 ;;
    --password)   PW="$2"; PW_SET=1; shift 2 ;;
    --self-test)  SELFTEST=1; shift ;;
    -h|--help)    usage; exit 0 ;;
    -*)           die "unknown flag: $1" ;;
    *)            die "unexpected argument: $1" ;;
  esac
done

command -v openssl >/dev/null 2>&1 || die "openssl not found on PATH"
[ -x "$CHECK_CHAIN" ] || die "sibling guard not found/executable: $CHECK_CHAIN"

fp_sha256() { openssl x509 -in "$1" -noout -fingerprint -sha256 2>/dev/null | sed 's/.*=//; s/://g' | tr 'a-f' 'A-F'; }

to_pem() {  # in out
  openssl x509 -inform DER -in "$1" -out "$2" 2>/dev/null && return 0
  openssl x509 -inform PEM -in "$1" -out "$2" 2>/dev/null && return 0
  die "not a valid certificate: $1"
}

export_p12() {  # key leaf chainfile pw out
  openssl pkcs12 -export -inkey "$1" -in "$2" -certfile "$3" \
    -passout "pass:$4" -name "Developer ID Application" -out "$5" 2>/dev/null \
    || die "openssl pkcs12 export failed"
}

# ---- self-test: dummy PKI, offline, proves the guard both ways ---------------
if [ -n "$SELFTEST" ]; then
  W="$(mktemp -d "${TMPDIR:-/tmp}/p12selftest.XXXXXX")"
  trap 'rm -rf "$W"' EXIT
  printf 'basicConstraints=critical,CA:true\nkeyUsage=critical,keyCertSign,cRLSign\n' > "$W/ca.ext"
  printf 'basicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=critical,codeSigning\n' > "$W/leaf.ext"

  openssl req -x509 -newkey rsa:2048 -nodes -keyout "$W/root.key" -out "$W/root.pem" \
    -subj "/CN=Dummy Root CA" -days 2 -addext "basicConstraints=critical,CA:true" 2>/dev/null \
    || die "self-test: dummy root gen failed"
  openssl req -new -newkey rsa:2048 -nodes -keyout "$W/int.key" -subj "/CN=Dummy Intermediate CA" -out "$W/int.csr" 2>/dev/null
  openssl x509 -req -in "$W/int.csr" -CA "$W/root.pem" -CAkey "$W/root.key" -CAcreateserial \
    -extfile "$W/ca.ext" -days 2 -out "$W/int.pem" 2>/dev/null || die "self-test: dummy intermediate gen failed"
  openssl req -new -newkey rsa:2048 -nodes -keyout "$W/leaf.key" -subj "/CN=Dummy Developer ID Application" -out "$W/leaf.csr" 2>/dev/null
  openssl x509 -req -in "$W/leaf.csr" -CA "$W/int.pem" -CAkey "$W/int.key" -CAcreateserial \
    -extfile "$W/leaf.ext" -days 2 -out "$W/leaf.pem" 2>/dev/null || die "self-test: dummy leaf gen failed"

  cat "$W/int.pem" "$W/root.pem" > "$W/chain.pem"
  export_p12 "$W/leaf.key" "$W/leaf.pem" "$W/chain.pem" "selftestpw" "$W/full.p12"
  openssl pkcs12 -export -inkey "$W/leaf.key" -in "$W/leaf.pem" \
    -passout "pass:selftestpw" -name "Developer ID Application" -out "$W/leafonly.p12" 2>/dev/null \
    || die "self-test: leaf-only p12 export failed"

  rc=0
  if "$CHECK_CHAIN" --p12 "$W/full.p12" --password "selftestpw" --apple-root "$W/root.pem" >/dev/null 2>&1; then
    echo "self-test: 3-cert p12 accepted   OK"
  else
    echo "self-test: 3-cert p12 REJECTED (bug)"; rc=1
  fi
  if "$CHECK_CHAIN" --p12 "$W/leafonly.p12" --password "selftestpw" >/dev/null 2>&1; then
    echo "self-test: leaf-only p12 accepted (bug)"; rc=1
  else
    echo "self-test: leaf-only p12 rejected  OK"
  fi
  if [ "$rc" -eq 0 ]; then echo "self-test: PASS"; else echo "self-test: FAIL"; fi
  exit "$rc"
fi

# ---- production: real leaf + pinned Apple certs ------------------------------
[ -n "$CERT" ] || die "--cert is required"
[ -n "$KEY" ] || die "--key is required"
[ -f "$CERT" ] || die "leaf cert not found: $CERT"
[ -f "$KEY" ] || die "private key not found: $KEY"

if [ -z "$PW_SET" ]; then
  PW="$(openssl rand -base64 18 | tr -d '\n')"
fi
[ -n "$PW" ] || die "p12 password must not be empty"

W="$(mktemp -d "${TMPDIR:-/tmp}/p12assemble.XXXXXX")"
trap 'rm -rf "$W"' EXIT

to_pem "$CERT" "$W/leaf.pem"

fetch_pinned() {  # url want-sha out
  curl -fsSL --max-time 30 -o "$W/dl.tmp" "$1" || die "download failed: $1"
  to_pem "$W/dl.tmp" "$3"
  got="$(fp_sha256 "$3")"
  [ "$got" = "$2" ] || die "PIN MISMATCH for $1
     expected $2
     got      $got
     Apple rotated this cert, or the download was tampered — update the pin only
     after confirming the new fingerprint against apple.com."
}

fetch_pinned "$APPLE_ROOT_URL" "$APPLE_ROOT_SHA256" "$W/root.pem"
fetch_pinned "$DEVID_G1_URL"   "$DEVID_G1_SHA256"   "$W/g1.pem"
fetch_pinned "$DEVID_G2_URL"   "$DEVID_G2_SHA256"   "$W/g2.pem"

# Pick the intermediate under which the leaf actually verifies (G1 vs G2).
INTERMEDIATE=""
for cand in "$W/g2.pem" "$W/g1.pem"; do
  if openssl verify -CAfile "$W/root.pem" -untrusted "$cand" "$W/leaf.pem" >/dev/null 2>&1; then
    INTERMEDIATE="$cand"; break
  fi
done
[ -n "$INTERMEDIATE" ] || die "leaf does not chain to Apple Root CA via either Developer ID intermediate
     — is --cert really a Developer ID Application certificate?"

cat "$INTERMEDIATE" "$W/root.pem" > "$W/chain.pem"
export_p12 "$KEY" "$W/leaf.pem" "$W/chain.pem" "$PW" "$OUT"

"$CHECK_CHAIN" --p12 "$OUT" --password "$PW" --apple-root "$W/root.pem" \
  || die "assembled p12 failed the full-chain guard — not written for release"

echo "assemble-p12.sh: wrote $OUT (full chain: leaf + Developer ID intermediate + Apple Root CA)"
if [ -z "$PW_SET" ]; then
  echo "  generated p12 password (store as MACOS_SIGN_PASSWORD): $PW"
fi
echo "  base64 for MACOS_SIGN_P12:  base64 -i $OUT"
echo "  next: verify-signing-identity.sh --p12 $OUT --password <pw>"
