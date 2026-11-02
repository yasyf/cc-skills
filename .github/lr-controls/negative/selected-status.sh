source "$(dirname "$0")/../lib.sh"
checkout .ci-selected-status-baseline 021af5b6f12e50e35016e74ebe9d0ea02998c903
baseline=.ci-selected-status-baseline/plugins/long-running
cp plugins/long-running/tests/test_desk_runner.py "$baseline/tests/test_desk_runner.py"
for state in closed expired; do
  negative="$RUNNER_TEMP/selected-status-$state-negative.log"
  if uv run --locked --project plugins/long-running pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_desk_runner.py::test_stale_blocker_current_closure_keeps_original_receipt_without_reposting[$state]" -q > "$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F 'selected status did not retire externally closed blocker' "$negative"
  grep -F '1 failed' "$negative"
done
