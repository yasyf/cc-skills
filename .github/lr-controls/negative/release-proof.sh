source "$(dirname "$0")/../lib.sh"
checkout .ci-release-proof-baseline 82ab4e455cca7584000efc44439d74bb15f4b6b0
baseline=.ci-release-proof-baseline/plugins/long-running
cp plugins/long-running/tests/test_cut_release_read_proof.py "$baseline/tests/test_cut_release_read_proof.py"
for history in 32 10000; do
  negative="$RUNNER_TEMP/release-proof-old-$history.log"
  if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_cut_release_read_proof.py::test_warm_release_poll_uses_bounded_unchanged_proof[unchanged-$history]" -q -s >"$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F "warm release polling materialized $((32 * history)) DTOs for $history actions in 32 polls" "$negative"
  grep -F '1 failed' "$negative"
done
