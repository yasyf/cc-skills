source "$(dirname "$0")/../lib.sh"
uv run --locked --project plugins/long-running python plugins/long-running/benchmarks/sqlite-action-store.py
uv run --locked --project plugins/long-running python plugins/long-running/benchmarks/mailbox-index.py
