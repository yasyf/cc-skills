source "$(dirname "$0")/../lib.sh"
checkout .ci-merge-waits-baseline 05c2ec4e237c066c76ce3f90ad1e3d2d20db3dd0
baseline=.ci-merge-waits-baseline/plugins/long-running
cp plugins/long-running/tests/test_merge_wait_fact_cost.py "$baseline/tests/test_merge_wait_fact_cost.py"
for history in 32 10000; do
  negative="$RUNNER_TEMP/merge-waits-old-$history.log"
  if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_merge_wait_fact_cost.py::test_unchanged_merge_wait_reads_only_graded_children[$history]" -q -s >"$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F "unchanged merge waits decoded $((2 * (history + 1))) fact values instead of 2" "$negative"
  grep -F '1 failed' "$negative"
done
