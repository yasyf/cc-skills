source "$(dirname "$0")/../lib.sh"
uv run --locked --project plugins/long-running pytest plugins/long-running/tests/overflow_read_integration.py -q -s
