source "$(dirname "$0")/../lib.sh"
checkout .ci-board-ruling-baseline 6b728f365a4a04af870bf8018ae391de986bebf8
baseline=.ci-board-ruling-baseline/plugins/long-running
cp plugins/long-running/tests/test_board_rows.py "$baseline/tests/test_board_rows.py"
negative="$RUNNER_TEMP/board-ruling-old.log"
if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_board_rows.py::test_multispace_closing_list_has_linear_cpu_growth" "$baseline/tests/test_board_rows.py::test_board_ruling_predicates_visit_only_the_requested_pr_records" -q -s >"$negative" 2>&1; then
  cat "$negative"
  exit 1
fi
cat "$negative"
grep -F 'closing-list CPU grew superlinearly' "$negative"
grep -F 'unrelated ruling predicates were evaluated' "$negative"
grep -F '5 failed' "$negative"
