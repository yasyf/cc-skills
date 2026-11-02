source "$(dirname "$0")/../lib.sh"
checkout .ci-remote-bootstrap-baseline d37c9e67cd6632faa9bd90805188b4a5fb29216e
baseline=.ci-remote-bootstrap-baseline/plugins/long-running
cp plugins/long-running/tests/test_sprite_job.py "$baseline/tests/test_sprite_job.py"
negative="$RUNNER_TEMP/lr-remote-bootstrap-old.log"
if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_sprite_job.py::test_a_job_keeps_fetch_start_freshness_without_running_pack_maintenance[no-prior-fetch]" -q >"$negative" 2>&1; then
  cat "$negative"
  exit 1
fi
cat "$negative"
grep -F 'Sprite startup ran explicit pack maintenance' "$negative"
grep -F '1 failed' "$negative"
