#!/usr/bin/env bash
# MCP entrypoint: exec codex-ask in `channel` stdio mode (stdout is the MCP
# transport). Pre-warm the version-exact binary first so binrun's resolve/
# download noise stays off the MCP stdio transport (stdout must be clean
# JSON-RPC), then exec it from the warm cache. bin/codex-ask is the committed
# binrun shim; it resolves and caches the pinned artifact.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/bin/codex-ask"

"$BIN" --version >/dev/null 2>&1 || true
exec "$BIN" channel
