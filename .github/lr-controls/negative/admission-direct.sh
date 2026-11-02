source "$(dirname "$0")/../lib.sh"
checkout .ci-admission-direct-baseline cd0740efe5bd10b0402bd310126cf566bb53a2c5
baseline=.ci-admission-direct-baseline/plugins/long-running
cp plugins/long-running/tests/test_admission_direct_merge_cost.py "$baseline/tests/test_admission_direct_merge_cost.py"
for history in 32 10000; do
  negative="$RUNNER_TEMP/admission-direct-merge-old-$history.log"
  if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_admission_direct_merge_cost.py::test_admission_avoids_unrelated_bus_history[$history]" -q -s >"$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F "direct-merge admission decoded $((history + 6)) actions instead of 6 candidates" "$negative"
  grep -F '1 failed' "$negative"
done
