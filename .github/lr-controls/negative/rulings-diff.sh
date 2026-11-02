source "$(dirname "$0")/../lib.sh"
checkout .ci-rulings-diff-baseline 14e26e2b070d27c28d49530cc08ae0a6a4a42d7a
baseline=.ci-rulings-diff-baseline/plugins/long-running
cp plugins/long-running/tests/test_rulings_diff_cache.py "$baseline/tests/test_rulings_diff_cache.py"
for size in 32 4096; do
  negative="$RUNNER_TEMP/rulings-diff-old-$size.log"
  if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_rulings_diff_cache.py::test_same_cold_key_computes_one_copy_aware_diff[$size]" -q -s >"$negative" 2>&1; then
    cat "$negative"
    exit 1
  else
    code=$?
  fi
  cat "$negative"
  test "$code" -eq 1
  grep -F 'duplicate expensive copy-aware diffs: expected 1, got 2' "$negative"
  grep -F '1 failed' "$negative"
done
