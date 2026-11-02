source "$(dirname "$0")/../lib.sh"
checkout .ci-refusal-history-baseline 3a0cf42c873087b50698fcdd43cd9856413ec6f5
baseline=.ci-refusal-history-baseline/plugins/long-running
cp plugins/long-running/tests/test_refusal_history_invocation.py "$baseline/tests/test_refusal_history_invocation.py"
for history in 32 10000; do
  negative="$RUNNER_TEMP/refusal-history-old-$history.log"
  if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_refusal_history_invocation.py::test_unchanged_bus_history_is_loaded_once_per_invocation[$history]" -q -s >"$negative" 2>&1; then
    cat "$negative"
    exit 1
  else
    test "$?" -eq 1
  fi
  cat "$negative"
  grep -F "refusal history materialized $((32 * history)) DTOs; expected $history for 32 eligible PRs" "$negative"
  grep -F '1 failed' "$negative"
done
