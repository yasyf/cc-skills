source "$(dirname "$0")/../lib.sh"
checkout .ci-remote-routing-baseline d37c9e67cd6632faa9bd90805188b4a5fb29216e
baseline=.ci-remote-routing-baseline/plugins/long-running
cp plugins/long-running/tests/test_desk_runner.py "$baseline/tests/test_desk_runner.py"
negative="$RUNNER_TEMP/lr-remote-routing-old.log"
if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_desk_runner.py::test_a_selected_remote_lane_clears_only_the_local_route_override[inherited]" -q >"$negative" 2>&1; then
  cat "$negative"
  exit 1
fi
cat "$negative"
grep -F 'selected remote route retained local override' "$negative"
grep -F '1 failed' "$negative"
