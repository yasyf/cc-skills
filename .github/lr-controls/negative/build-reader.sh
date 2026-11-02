source "$(dirname "$0")/../lib.sh"
checkout .ci-build-reader-baseline 2be0655efb61b8c4d65ecdea9d42b6660c092626
uv run --locked --project plugins/long-running pytest -c .ci-build-reader-baseline/plugins/long-running/pyproject.toml .ci-build-reader-baseline/plugins/long-running/tests/test_build_cache.py -q -s -k test_actual_landing_selected_cached_build_read_connections
cp plugins/long-running/tests/test_build_cache.py .ci-build-reader-baseline/plugins/long-running/tests/test_build_cache.py
for population in 32 10000; do
  negative="$RUNNER_TEMP/build-reader-negative-$population.log"
  if uv run --locked --project plugins/long-running pytest -c .ci-build-reader-baseline/plugins/long-running/pyproject.toml ".ci-build-reader-baseline/plugins/long-running/tests/test_build_cache.py::test_actual_landing_selected_cached_build_read_connections[$population]" -q -s > "$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F 'selected cached builds reopened 32 readers' "$negative"
  grep -F '1 failed' "$negative"
done
