source "$(dirname "$0")/../lib.sh"
checkout .ci-restack-history-baseline 0beb1c60553e7992068845a822ec42921baf05fe
baseline=.ci-restack-history-baseline/plugins/long-running
cp plugins/long-running/tests/test_restack_read_cost.py "$baseline/tests/test_restack_read_cost.py"
for history in 32 10000; do
  negative="$RUNNER_TEMP/restack-history-$history-negative.log"
  if uv run --locked --project plugins/long-running pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_restack_read_cost.py::test_restack_verification_avoids_terminal_history[$history]" -q -s > "$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F "1 failed" "$negative"
  grep -F "actions instead of 4 active candidates" "$negative"
done
