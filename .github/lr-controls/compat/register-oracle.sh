source "$(dirname "$0")/../lib.sh"
checkout .ci-register-oracle-baseline 963e964f15a919b6f0588fba2d7dd68de41c4d91
git show 1ddc785a2796f63677408f168e590e95a6fc02b0:plugins/long-running/tests/test_register_reads.py > .ci-register-oracle-baseline/plugins/long-running/tests/test_register_reads.py
REGISTER_READ_ORACLE="$RUNNER_TEMP/register-read-oracle.json" REGISTER_READ_ORACLE_WRITE=1 uv run --locked --project plugins/long-running pytest -c .ci-register-oracle-baseline/plugins/long-running/pyproject.toml .ci-register-oracle-baseline/plugins/long-running/tests/test_register_reads.py -q -s -k test_register_read_cli_oracle
REGISTER_READ_ORACLE="$RUNNER_TEMP/register-read-oracle.json" uv run --locked --project plugins/long-running pytest plugins/long-running/tests/test_register_reads.py::test_register_read_cli_oracle -q -s
