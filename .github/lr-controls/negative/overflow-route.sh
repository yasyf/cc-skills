source "$(dirname "$0")/../lib.sh"
checkout .ci-overflow-route-baseline f37dde3a53da2f13404a3417cbfb611d963f9531
uv run --locked --project plugins/long-running pytest -c .ci-overflow-route-baseline/plugins/long-running/pyproject.toml .ci-overflow-route-baseline/plugins/long-running/tests/overflow_read_integration.py -q -s
cp plugins/long-running/tests/overflow_read_integration.py .ci-overflow-route-baseline/plugins/long-running/tests/overflow_read_integration.py
for history in 32 10000; do
  negative="$RUNNER_TEMP/overflow-route-negative-$history.log"
  if uv run --locked --project plugins/long-running pytest -c .ci-overflow-route-baseline/plugins/long-running/pyproject.toml ".ci-overflow-route-baseline/plugins/long-running/tests/overflow_read_integration.py::test_actual_overflow_refusal_historical_read_cost[$history]" -q -s > "$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F 'overflow route history decoded' "$negative"
  grep -F '1 failed' "$negative"
done
negative="$RUNNER_TEMP/overflow-reader-negative.log"
if uv run --locked --project plugins/long-running pytest -c .ci-overflow-route-baseline/plugins/long-running/pyproject.toml .ci-overflow-route-baseline/plugins/long-running/tests/overflow_read_integration.py::test_overflow_thread_reuses_readers_sees_writes_and_closes_on_exit -q -s > "$negative" 2>&1; then
  cat "$negative"
  exit 1
fi
cat "$negative"
grep -F 'overflow thread opened' "$negative"
grep -F '1 failed' "$negative"
