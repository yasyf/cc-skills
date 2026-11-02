source "$(dirname "$0")/../lib.sh"
checkout .ci-verified-cause-baseline 2ef339b0d78951d89000b3f012b65905dc852b52
baseline=.ci-verified-cause-baseline/plugins/long-running
cp plugins/long-running/tests/test_desk_runner.py "$baseline/tests/test_desk_runner.py"
for receipt in closed resolved; do
  negative="$RUNNER_TEMP/verified-cause-$receipt-negative.log"
  if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_desk_runner.py::test_posted_blocker_cause_stays_available_after_verification[$receipt]" -q >"$negative" 2>&1; then
    cat "$negative"
    exit 1
  fi
  cat "$negative"
  grep -F 'verified posted blocker disappeared from cause history' "$negative"
  grep -F '1 failed' "$negative"
done
negative="$RUNNER_TEMP/verified-cause-race-negative.log"
if uv run --locked --project "$baseline" pytest -c "$baseline/pyproject.toml" "$baseline/tests/test_desk_runner.py::test_same_red_cause_uses_original_blocker_after_cleanup_wins_the_race" -q >"$negative" 2>&1; then
  cat "$negative"
  exit 1
fi
cat "$negative"
grep -F 'cleanup-before-cause lookup duplicated original blocker' "$negative"
grep -F '1 failed' "$negative"
