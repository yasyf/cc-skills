source "$(dirname "$0")/../lib.sh"
checkout .ci-reader-validation-baseline 91c856f8556b4462bb57ca215d34fef8254a92d9
baseline=.ci-reader-validation-baseline/plugins/long-running
cp plugins/long-running/tests/test_reader_validation_snapshot.py "$baseline/tests/test_reader_validation_snapshot.py"
negative="$RUNNER_TEMP/reader-validation-old.log"
if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_reader_validation_snapshot.py::test_every_reader_validation_uses_the_requested_snapshot[fact-scoped-32]" -q -s >"$negative" 2>&1; then
  cat "$negative"
  exit 1
fi
cat "$negative"
grep -F 'reader validation opened 32 implicit snapshots' "$negative"
grep -F '1 failed' "$negative"
