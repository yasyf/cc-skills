#!/usr/bin/env bash
# Resolve author identity from local tooling. Emits KEY=VALUE lines on stdout.
# Never invents values: anything unresolvable prints KEY= and a MISSING note on stderr.
set -euo pipefail

command -v git >/dev/null 2>&1 || { echo "ERROR: git is not installed" >&2; exit 2; }

author_name="$(git config --get user.name || true)"
author_email="$(git config --get user.email || true)"

github_user=""
if command -v gh >/dev/null 2>&1; then
  github_user="$(gh api user -q .login 2>/dev/null || true)"
fi
[ -n "$github_user" ] || github_user="$(git config --get github.user || true)"

echo "AUTHOR_NAME=${author_name}"
echo "AUTHOR_EMAIL=${author_email}"
echo "GITHUB_USER=${github_user}"

missing=0
for pair in "AUTHOR_NAME:${author_name}" "AUTHOR_EMAIL:${author_email}" "GITHUB_USER:${github_user}"; do
  key="${pair%%:*}"
  value="${pair#*:}"
  if [ -z "$value" ]; then
    echo "MISSING: ${key} — ask the user" >&2
    missing=$((missing + 1))
  fi
done

[ "$missing" -lt 3 ] || exit 1
