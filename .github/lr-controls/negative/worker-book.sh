source "$(dirname "$0")/../lib.sh"
checkout .ci-worker-book-baseline 9d930510a7643fedb6d756055bfc2872979e2c9c
baseline=.ci-worker-book-baseline/plugins/long-running
cp plugins/long-running/tests/test_landing_book_readers.py "$baseline/tests/test_landing_book_readers.py"
for route in dispatch side; do
  negative="$RUNNER_TEMP/worker-book-$route-negative.log"
  if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_landing_book_readers.py" -k "$route and 32 and error" -q -s >"$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F "$route reopened 32 Runner Book readers" "$negative"
  grep -F '1 failed' "$negative"
done
