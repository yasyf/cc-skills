source "$(dirname "$0")/../lib.sh"
checkout .ci-heartbeat-lifecycle-baseline 72a91ecd4a1a26a4666e39a7f0f8237444359c53
baseline=.ci-heartbeat-lifecycle-baseline/plugins/long-running
cp plugins/long-running/tests/test_desk_runner.py "$baseline/tests/test_desk_runner.py"
for desk in orca landing; do
  negative="$RUNNER_TEMP/heartbeat-lifecycle-old-$desk.log"
  if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_desk_runner.py" -k "test_controller_return_stops_its_real_heartbeat and once and $desk" -q >"$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F 'returned controller left its heartbeat stop event unset' "$negative"
  grep -F '1 failed' "$negative"
done
