source "$(dirname "$0")/../lib.sh"
checkout .ci-reader-working-set-baseline 55b1b01654e37be57b7c2d102f5b4492a7fec1a5
baseline=.ci-reader-working-set-baseline/plugins/long-running
cp plugins/long-running/tests/test_reader_working_set.py "$baseline/tests/test_reader_working_set.py"
for lanes in 63 64; do
  negative="$RUNNER_TEMP/reader-working-set-lanes-$lanes-negative.log"
  if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_reader_working_set.py::test_actual_refusal_reader_working_set_boundary[lanes-$lanes-seed-0]" -q -s >"$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F "cyclic reader working set reopened $(((lanes + 2) * 16)) connections" "$negative"
  grep -F '1 failed' "$negative"
done
