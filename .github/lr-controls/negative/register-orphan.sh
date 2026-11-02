source "$(dirname "$0")/../lib.sh"
checkout .ci-register-orphan-baseline 963e964f15a919b6f0588fba2d7dd68de41c4d91
git show 1ddc785a2796f63677408f168e590e95a6fc02b0:plugins/long-running/tests/test_register_reads.py > .ci-register-orphan-baseline/plugins/long-running/tests/test_register_reads.py
if uv run --locked --project plugins/long-running pytest -c .ci-register-orphan-baseline/plugins/long-running/pyproject.toml .ci-register-orphan-baseline/plugins/long-running/tests/test_register_reads.py -q -s -k test_register_query_timeout_leaves_no_child > "$RUNNER_TEMP/register-orphan-negative.log" 2>&1; then
  cat "$RUNNER_TEMP/register-orphan-negative.log"
  exit 1
fi
cat "$RUNNER_TEMP/register-orphan-negative.log"
grep -F 'query survived register budget:' "$RUNNER_TEMP/register-orphan-negative.log"
