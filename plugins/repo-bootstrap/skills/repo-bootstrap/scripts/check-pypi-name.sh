#!/usr/bin/env bash
# Check whether a PyPI distribution name is free.
# Exit codes: 0 = AVAILABLE, 1 = TAKEN, 2 = UNKNOWN (verify manually), 3 = invalid name.
set -uo pipefail

name="${1:?usage: check-pypi-name.sh NAME}"

if ! printf '%s' "$name" | grep -Eq '^([A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9])$'; then
  echo "INVALID: ${name} is not a valid PyPI project name"
  exit 3
fi

status="$(curl -sS -o /dev/null -w '%{http_code}' "https://pypi.org/pypi/${name}/json" 2>/dev/null)" || status="000"

case "$status" in
  404) echo "AVAILABLE"; exit 0 ;;
  200) echo "TAKEN (${name} is an existing project)"; exit 1 ;;
  *)   echo "UNKNOWN: HTTP ${status} — verify manually at https://pypi.org/project/${name}/"; exit 2 ;;
esac
