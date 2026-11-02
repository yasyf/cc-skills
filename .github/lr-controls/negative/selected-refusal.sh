source "$(dirname "$0")/../lib.sh"
checkout .ci-selected-refusal-baseline c19f048042b4b8eb52598a609054b907679af5b3
baseline=.ci-selected-refusal-baseline/plugins/long-running
cp plugins/long-running/tests/test_refusal_selected_pr_contract.py "$baseline/tests/test_refusal_selected_pr_contract.py"
for route in gate state; do
  for history in 32 10000; do
    negative="$RUNNER_TEMP/selected-refusal-old-$route-$history.log"
    if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_refusal_selected_pr_contract.py::test_actual_refusal_callers_do_not_load_unrelated_pr_history[$history-$route]" -q -s --junitxml="$RUNNER_TEMP/selected-refusal-old-$route-$history.xml" >"$negative" 2>&1; then
      cat "$negative"
      exit 1
    else
      test "$?" -eq 1
    fi
    cat "$negative"
    grep -F "$route loaded $((history + 1)) refusal Action records for $history unrelated records" "$negative"
    grep -F '1 failed' "$negative"
    if grep -E '[1-9][0-9]* (skipped|errors?)' "$negative"; then exit 1; fi
  done
done
