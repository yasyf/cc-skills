source "$(dirname "$0")/../lib.sh"
checkout .ci-stale-blocker-baseline 897a7efad74429a246c919bcaf3e91a926736389
baseline=.ci-stale-blocker-baseline/plugins/long-running
cp plugins/long-running/tests/test_desk_runner.py "$baseline/tests/test_desk_runner.py"
for origin in owner remote; do
  negative="$RUNNER_TEMP/stale-blocker-$origin.log"
  if uv run --locked --project "$baseline" pytest "$baseline/tests/test_desk_runner.py::test_stale_blocker_uses_internal_reference_for_mixed_origin_receipt[$origin]" -q >"$negative" 2>&1; then
    cat "$negative"
    echo "stale blocker human-reference baseline unexpectedly passed"
    exit 1
  fi
  cat "$negative"
  grep -F 'stale blocker used human reference for internal sequence' "$negative"
  grep -F '1 failed' "$negative"
done
for history in 32 10000; do
  negative="$RUNNER_TEMP/stale-blocker-history-$history.log"
  if uv run --locked --project "$baseline" pytest "$baseline/tests/test_desk_runner.py::test_stale_blocker_cleanup_has_history_bounded_noop_work[$history]" -q >"$negative" 2>&1; then
    cat "$negative"
    echo "completed-history cleanup baseline unexpectedly passed"
    exit 1
  fi
  cat "$negative"
  grep -F "stale cleanup decoded $history historical actions" "$negative"
  grep -F '1 failed' "$negative"
done
