source "$(dirname "$0")/../lib.sh"
checkout .ci-release-cursor-baseline 91c856f8556b4462bb57ca215d34fef8254a92d9
baseline=.ci-release-cursor-baseline/plugins/long-running
cp plugins/long-running/tests/test_release_cursor_noop.py "$baseline/tests/test_release_cursor_noop.py"
for history in 32 10000; do
  negative="$RUNNER_TEMP/release-cursor-old-$history.log"
  if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_release_cursor_noop.py::test_no_event_release_poll_avoids_writer_transaction[read-$history]" -q -s >"$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F 'no-event release polling opened 32 writers for 32 calls' "$negative"
  grep -F '1 failed' "$negative"
done
