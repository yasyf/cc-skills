#!/usr/bin/env bash
# Push the Go release secrets to a GitHub repo, straight from 1Password.
#
#   set-release-secrets.sh [OWNER/REPO ...] [--vault VAULT] [-n|--dry-run]
#
# For each release secret, reads op://VAULT/<NAME>/credential and, when 1Password
# returns a non-empty value, sets it as a repo secret with `gh secret set`. Secrets
# absent from 1Password are skipped: the release still runs — unsigned when a MACOS_*
# is missing, and without a cask push when HOMEBREW_TAP_TOKEN is missing. Idempotent:
# re-running overwrites with the current 1Password values. Run this AFTER the repo
# exists (e.g. after `gh repo create`).
#
# The secrets (all stored at op://VAULT/<NAME>/credential):
#   HOMEBREW_TAP_TOKEN        fine-grained PAT that pushes the Homebrew cask to the tap
#   MACOS_SIGN_P12            base64 Developer ID Application .p12 (full chain)
#   MACOS_SIGN_PASSWORD       password for that .p12
#   MACOS_NOTARY_ISSUER_ID    App Store Connect API issuer id
#   MACOS_NOTARY_KEY_ID       App Store Connect API key id
#   MACOS_NOTARY_KEY          base64 App Store Connect API .p8 key
#
# Args:
#   OWNER/REPO    repo(s) to set secrets on; with none, the repo inferred from the
#                 current directory. Each secret is read from 1Password once and reused
#                 across every repo.
#   --vault       1Password vault holding the credentials (default: OpenClaw)
#   -n,--dry-run  report which secrets would be set/skipped without setting any; still
#                 reads from 1Password (so presence is real — may prompt for Touch ID)
#
# Needs `gh` (authenticated). 1Password is best-effort: without `op` (or with a locked
# session) the script prints the names to set by hand and exits 0, so it never blocks a
# bootstrap. The first `op read` may prompt for Touch ID to unlock the vault. Exits
# non-zero only on hard errors (no/unauthenticated gh, repo not resolvable).
set -euo pipefail

die() { echo "set-release-secrets.sh: $*" >&2; exit 1; }

SECRETS="HOMEBREW_TAP_TOKEN MACOS_SIGN_P12 MACOS_SIGN_PASSWORD MACOS_NOTARY_ISSUER_ID MACOS_NOTARY_KEY_ID MACOS_NOTARY_KEY"

VAULT="OpenClaw"; DRY=""; REPOS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --vault)       VAULT="$2"; shift 2 ;;
    -n|--dry-run)  DRY=1; shift ;;
    -h|--help)     sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 0 ;;
    -*)            die "unknown flag: $1" ;;
    *)             REPOS="${REPOS:+$REPOS }$1"; shift ;;
  esac
done

command -v gh >/dev/null 2>&1 || die "gh CLI not found on PATH"
gh auth status >/dev/null 2>&1 || die "gh is not authenticated — run 'gh auth login' first"

# Resolve the repo(s) so every gh call works regardless of the current directory.
if [ -z "$REPOS" ]; then
  REPOS="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null)" \
    || die "could not infer repo — pass OWNER/REPO (create the repo first?)"
fi

# 1Password is best-effort: if it isn't reachable, list the secrets to set by hand and
# bow out cleanly so a bootstrap is never blocked. `op whoami` tells us op is usable; it
# does NOT promise an unlocked vault — the per-read tolerance below handles a locked item.
if ! command -v op >/dev/null 2>&1 || ! op whoami >/dev/null 2>&1; then
  echo "set-release-secrets.sh: 1Password CLI unavailable or not signed in — set these by hand:" >&2
  for name in $SECRETS; do echo "  gh secret set $name -R <owner>/<repo>   # op://$VAULT/$name/credential" >&2; done
  exit 0
fi

echo "set-release-secrets.sh: reading release secrets from 1Password (vault $VAULT) — may prompt for Touch ID" >&2

# Read each secret once; reuse across every repo (avoids a Touch ID prompt per repo).
# Values are single-line (base64 or token), so command substitution + `printf '%s'`
# round-trips them byte-for-byte; no trailing-newline trim is needed or wanted.
present=""; absent=""
for name in $SECRETS; do
  if [ -n "$(op read "op://$VAULT/$name/credential" 2>/dev/null || true)" ]; then
    present="${present:+$present }$name"
  else
    absent="${absent:+$absent }$name"
  fi
done

if [ -z "$present" ]; then
  echo "set-release-secrets.sh: 0 of 6 secrets found in vault '$VAULT' — check --vault and item names" >&2
fi

set_secret() {  # name repo
  [ -n "$DRY" ] && return 0
  printf '%s' "$(op read "op://$VAULT/$1/credential")" | gh secret set "$1" -R "$2"
}

for repo in $REPOS; do
  echo
  echo "=== $repo ==="
  gh repo view "$repo" --json id >/dev/null 2>&1 \
    || { echo "skipped — repo not reachable (create it first)"; continue; }
  for name in $present; do
    set_secret "$name" "$repo"
    echo "${DRY:+would }set: $name"
  done
  for name in $absent; do
    echo "skipped (not in 1Password): $name"
  done
done

# A missing tap token breaks the cask push; missing MACOS_* only means an unsigned release.
case " $absent " in
  *" HOMEBREW_TAP_TOKEN "*)
    echo >&2
    echo "set-release-secrets.sh: WARNING — HOMEBREW_TAP_TOKEN not set; the Homebrew cask push will fail." >&2 ;;
esac
case "$absent" in
  *MACOS_*) echo "set-release-secrets.sh: note — some MACOS_* absent; the release will run unsigned." >&2 ;;
esac
