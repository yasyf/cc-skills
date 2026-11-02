source "$(dirname "$0")/../lib.sh"
checkout .ci-executor-reader-baseline 9d930510a7643fedb6d756055bfc2872979e2c9c
baseline=.ci-executor-reader-baseline/plugins/long-running
cp plugins/long-running/tests/test_executor_incident_readers.py "$baseline/tests/test_executor_incident_readers.py"
cp plugins/long-running/tests/test_build_cache.py "$baseline/tests/test_build_cache.py"
for history in 32 256; do
  negative="$RUNNER_TEMP/executor-readers-$history-negative.log"
  if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_executor_incident_readers.py::test_executor_scan_reuses_each_incident_reader[$history]" -q -s >"$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F "executor scan opened $((2 * history)) readers for $history incidents" "$negative"
  grep -F '1 failed' "$negative"
done
