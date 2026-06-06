#!/usr/bin/env bash
# Verify a scaffolded repo. Runs every check, reports PASS/FAIL per check,
# exits 1 if any check failed. Python layer adds sync/test/build/wheel-smoke.
set -uo pipefail

layer="base"
target="."
while [ $# -gt 0 ]; do
  case "$1" in
    --layer)  layer="$2"; shift 2 ;;
    --target) target="$2"; shift 2 ;;
    *) echo "usage: verify.sh [--layer base|python] [--target DIR]" >&2; exit 2 ;;
  esac
done
cd "$target"

failures=0
check() {
  local name="$1"; shift
  if "$@" >/tmp/verify-check.log 2>&1; then
    echo "PASS  ${name}"
  else
    echo "FAIL  ${name}"
    sed 's/^/      /' /tmp/verify-check.log | tail -30
    failures=$((failures + 1))
  fi
}

no_leftover_tokens() {
  ! rg -n --no-ignore-vcs -g '!.git' -g '!.venv' -g '!dist' -g '!great-docs' '\{\{[A-Z_]+\}\}' .
}

license_present() {
  [ -s LICENSE ]
}

hook_tests() {
  command -v uvx >/dev/null 2>&1 || { echo "uvx not found — install uv: https://docs.astral.sh/uv/"; return 1; }
  uvx --from capt-hook python -c "from captain_hook.util.model_cache import ensure_spacy_model; ensure_spacy_model()"
  uvx capt-hook test
}

check "no unrendered {{...}} tokens" no_leftover_tokens
check "LICENSE present" license_present
check "hook inline tests (uvx capt-hook test)" hook_tests

if rg -n --no-ignore-vcs -g '!.git' 'TODO\(bootstrap\)' . >/tmp/verify-todos.log 2>/dev/null; then
  echo "NOTE  TODO(bootstrap) markers remain (replace them with real prose):"
  sed 's/^/      /' /tmp/verify-todos.log
fi

if [ "$layer" = "python" ]; then
  check "uv sync --extra dev" uv sync --extra dev
  check "uv run pytest" uv run pytest
  check "uv build" uv build

  wheel_smoke() {
    local dist_name
    dist_name="$(sed -n 's/^name = "\(.*\)"$/\1/p' pyproject.toml | head -1)"
    [ -n "$dist_name" ] || { echo "could not read project name from pyproject.toml"; return 1; }
    rm -rf .wheel-smoke
    uv venv --seed .wheel-smoke &&
      uv pip install --python .wheel-smoke/bin/python dist/*.whl &&
      ".wheel-smoke/bin/${dist_name}" --help
    local rc=$?
    rm -rf .wheel-smoke
    return $rc
  }
  check "wheel smoke test" wheel_smoke
fi

[ "$failures" -eq 0 ] || { echo "${failures} check(s) failed"; exit 1; }
echo "All checks passed"
