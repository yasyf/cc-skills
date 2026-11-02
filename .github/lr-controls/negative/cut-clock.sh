source "$(dirname "$0")/../lib.sh"
checkout .ci-cut-clock-baseline a99ff7311aa2d4c71ba696cf608f4ee9fb2b8917
baseline=.ci-cut-clock-baseline/plugins/long-running
cp plugins/long-running/tests/test_open_cut_clock_noop.py "$baseline/tests/test_open_cut_clock_noop.py"
for source in empty existing-release; do
  for history in 32 10000; do
    negative="$RUNNER_TEMP/cut-clock-old-$source-$history.log"
    if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_open_cut_clock_noop.py::test_unchanged_cut_poll_avoids_writer_transaction[$source-open-$history]" -q -s >"$negative" 2>&1; then
      cat "$negative"
      exit 1
    fi
    cat "$negative"
    grep -F 'unchanged cut polling opened 32 writers for 32 calls' "$negative"
    grep -F '1 failed' "$negative"
  done
done
