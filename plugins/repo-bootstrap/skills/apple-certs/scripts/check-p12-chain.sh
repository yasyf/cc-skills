#!/usr/bin/env bash
# Hard guard: a .p12 must carry the FULL chain (leaf + intermediate + root).
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
check-p12-chain.sh --p12 FILE --password PW [--apple-root PEM]

Fail loud unless the p12 carries a complete Developer ID chain. A p12 with only
the leaf (or leaf + intermediate but no root) produces a signature whose
designated requirement `anchor apple generic` cannot be satisfied at exec:
Gatekeeper/taskgated SIGKILLs the process (exit 137) even though `codesign
--verify` on the static signature looks valid — the quill unsatisfiable-DR
failure. This check stops that shipping.

Checks:
  1. the p12 contains >= 3 certificates
  2. they form a complete chain ending in a self-signed root (openssl verify)
  3. with --apple-root, the bundled root is byte-identical to that Apple Root CA

Args:
  --p12 FILE        the PKCS#12 to inspect
  --password PW     the p12 export password (use '' for none)
  --apple-root PEM  pin the bundled root against this PEM (Apple Root CA)
EOF
}

die() { echo "check-p12-chain.sh: $*" >&2; exit 1; }

P12=""; PW=""; PW_SET=""; APPLE_ROOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --p12)         P12="$2"; shift 2 ;;
    --password)    PW="$2"; PW_SET=1; shift 2 ;;
    --apple-root)  APPLE_ROOT="$2"; shift 2 ;;
    -h|--help)     usage; exit 0 ;;
    -*)            die "unknown flag: $1" ;;
    *)             die "unexpected argument: $1" ;;
  esac
done

command -v openssl >/dev/null 2>&1 || die "openssl not found on PATH"
[ -n "$P12" ] || die "--p12 is required"
[ -f "$P12" ] || die "p12 not found: $P12"
[ -n "$PW_SET" ] || die "--password is required (pass '' for an empty password)"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/p12chain.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

# Leaf and CA certs come out of separate PKCS#12 bags.
openssl pkcs12 -in "$P12" -passin "pass:$PW" -nokeys -clcerts -out "$WORK/leaf.pem" 2>/dev/null \
  || die "could not read leaf from p12 (wrong password, or LibreSSL/OpenSSL p12 mismatch)"
openssl pkcs12 -in "$P12" -passin "pass:$PW" -nokeys -cacerts -out "$WORK/cas.pem" 2>/dev/null \
  || die "could not read CA certs from p12"

count_certs() { grep -c 'BEGIN CERTIFICATE' "$1" 2>/dev/null || true; }

LEAF_N="$(count_certs "$WORK/leaf.pem")"
CA_N="$(count_certs "$WORK/cas.pem")"
TOTAL=$(( ${LEAF_N:-0} + ${CA_N:-0} ))

[ "$LEAF_N" -ge 1 ] || die "no leaf certificate in p12"
if [ "$TOTAL" -lt 3 ]; then
  die "p12 carries $TOTAL certificate(s); the full Developer ID chain needs 3
     (leaf + Developer ID intermediate + Apple Root CA). An incomplete chain
     reproduces the quill unsatisfiable-DR failure: exit 137 at exec with an
     apparently-valid signature. Re-run assemble-p12.sh so the p12 embeds the
     intermediate AND the root."
fi

# Split the CA bundle into one file per cert so we can find the self-signed root.
awk -v dir="$WORK" '
  /BEGIN CERTIFICATE/ { n++; f = sprintf("%s/ca-%02d.pem", dir, n) }
  n > 0 { print > f }
' "$WORK/cas.pem"

ROOT=""
for f in "$WORK"/ca-*.pem; do
  [ -e "$f" ] || continue
  subj="$(openssl x509 -in "$f" -noout -subject 2>/dev/null | sed 's/^subject=//')"
  iss="$(openssl x509 -in "$f" -noout -issuer 2>/dev/null | sed 's/^issuer=//')"
  if [ -n "$subj" ] && [ "$subj" = "$iss" ]; then
    ROOT="$f"
  else
    cat "$f" >> "$WORK/intermediates.pem"
  fi
done

[ -n "$ROOT" ] || die "no self-signed root in the p12 chain — the Apple Root CA is missing"
[ -f "$WORK/intermediates.pem" ] || die "no intermediate certificate between leaf and root"

# The chain must build leaf -> intermediate(s) -> root with the root as the anchor.
openssl verify -CAfile "$ROOT" -untrusted "$WORK/intermediates.pem" "$WORK/leaf.pem" >/dev/null 2>&1 \
  || die "leaf does not verify against the bundled intermediate + root — chain is broken"

if [ -n "$APPLE_ROOT" ]; then
  [ -f "$APPLE_ROOT" ] || die "--apple-root file not found: $APPLE_ROOT"
  want="$(openssl x509 -in "$APPLE_ROOT" -inform PEM -noout -fingerprint -sha256 2>/dev/null \
          || openssl x509 -in "$APPLE_ROOT" -inform DER -noout -fingerprint -sha256 2>/dev/null)"
  got="$(openssl x509 -in "$ROOT" -noout -fingerprint -sha256 2>/dev/null)"
  [ -n "$want" ] && [ "$want" = "$got" ] \
    || die "bundled root is not the pinned Apple Root CA (got $got)"
fi

echo "check-p12-chain.sh: OK — full chain present ($TOTAL certs: leaf + intermediate + root)"
