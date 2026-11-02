source "$(dirname "$0")/../lib.sh"
checkout .ci-escalation-selection-baseline 526a1ce33fb23188ac3a1f8a238487d38dd4354f
cp plugins/long-running/tests/overflow_read_integration.py .ci-escalation-selection-baseline/plugins/long-running/tests/overflow_read_integration.py
for history in 32 10000; do
  negative="$RUNNER_TEMP/escalation-selection-negative-$history.log"
  if uv run --locked --project plugins/long-running pytest -c .ci-escalation-selection-baseline/plugins/long-running/pyproject.toml ".ci-escalation-selection-baseline/plugins/long-running/tests/overflow_read_integration.py::test_actual_overflow_refusal_historical_read_cost[$history]" -q -s > "$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F "overflow route history decoded $((history + 3)) actions; expected 3" "$negative"
  grep -F '1 failed' "$negative"
done
