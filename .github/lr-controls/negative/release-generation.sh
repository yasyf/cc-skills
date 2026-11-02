source "$(dirname "$0")/../lib.sh"
checkout .ci-release-generation-baseline c01c7aa04559b07ad03983301609163b0a4630d0
baseline=.ci-release-generation-baseline/plugins/long-running
cp plugins/long-running/tests/landing_release_history_characterization.py "$baseline/tests/landing_release_history_characterization.py"
cp plugins/long-running/tests/test_landing_release_generations.py "$baseline/tests/test_landing_release_generations.py"
for history in 32 10000; do
  negative="$RUNNER_TEMP/release-generation-old-$history.log"
  if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_landing_release_generations.py::test_warm_release_batches_decode_only_changed_and_active[history-$history-facts-four]" -q -s >"$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F "warm release-side batches materialized $((32 * (history + 3))) DTOs for $history retired actions in 32 polls" "$negative"
  grep -F '1 failed' "$negative"
done
