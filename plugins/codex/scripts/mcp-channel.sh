#!/usr/bin/env bash
# MCP entrypoint: exec codex-ask in `channel` stdio mode (stdout is the MCP
# transport). Prefer the bundled bin/codex-ask, else PATH.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/bin/codex-ask"
if [ -x "$BIN" ]; then
  exec "$BIN" channel
fi
exec codex-ask channel
