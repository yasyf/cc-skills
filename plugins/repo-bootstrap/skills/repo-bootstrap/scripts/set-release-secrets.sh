#!/usr/bin/env bash
# Push the Go release secrets to a GitHub repo, straight from 1Password.
#
#   set-release-secrets.sh [OWNER/REPO ...] [--vault VAULT] [-n|--dry-run]
#
# Reads every required release secret from op://VAULT/<NAME>/credential, verifies the
# complete set before changing any repo, then writes it with `gh secret set`. Missing
# signing, notarization, or tap credentials are fatal. Re-running overwrites the repo
# secrets with the current 1Password values. Run this AFTER the repo exists.
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
#   -n,--dry-run  report which secrets would be set without setting any; still
#                 reads from 1Password (so presence is real — may prompt for Touch ID)
#
# Needs authenticated `gh` and `op`. The first `op read` may prompt for Touch ID.
# Any missing prerequisite or secret exits non-zero before a repo is changed.
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

if ! command -v op >/dev/null 2>&1 || ! op whoami >/dev/null 2>&1; then
  die "1Password CLI unavailable or not signed in; refusing incomplete release configuration"
fi

echo "set-release-secrets.sh: reading release secrets from 1Password (vault $VAULT) — may prompt for Touch ID" >&2

# Read each secret once into a private temporary directory, then reuse those exact bytes
# across every repo. This prevents a vault relock from producing a partial configuration.
SECRETS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/repo-bootstrap-release-secrets.XXXXXX")"
chmod 700 "$SECRETS_DIR"
trap 'rm -rf "$SECRETS_DIR"' EXIT

absent=""
for name in $SECRETS; do
  if ! op read "op://$VAULT/$name/credential" >"$SECRETS_DIR/$name" 2>/dev/null \
      || [ ! -s "$SECRETS_DIR/$name" ]; then
    absent="${absent:+$absent }$name"
  fi
  chmod 600 "$SECRETS_DIR/$name"
done

if [ -n "$absent" ]; then
  die "missing required release secrets in 1Password vault '$VAULT': $absent"
fi

for repo in $REPOS; do
  gh repo view "$repo" --json id >/dev/null 2>&1 \
    || die "repo not reachable: $repo"
done

set_secret() {  # name repo
  [ -n "$DRY" ] && return 0
  gh secret set "$1" -R "$2" <"$SECRETS_DIR/$1"
}

for repo in $REPOS; do
  echo
  echo "=== $repo ==="
  for name in $SECRETS; do
    set_secret "$name" "$repo"
    echo "${DRY:+would }set: $name"
  done
done
