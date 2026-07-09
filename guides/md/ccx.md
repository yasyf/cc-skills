## Compact Context (ccx)

`cc-context` — the `ccx` CLI and the `cc-context` MCP (its `mcp__cc-context__*` tools mirror the query surface — read, search, symbol, outline, diff, edit — plus `ccx_exec`/`ccx_exec_tools` for multi-call composition and `BashFormat` for JSON re-encoding) — is the DEFAULT for reading code, finding symbols, searching, and reviewing diffs. It returns token-bounded output (signatures + line numbers, explicit overflow, never silent truncation) instead of raw dumps, and the capt-hook `ccx` guard pack rewrites the mappable token-heavy commands (raw `grep`, bare `git diff`/`git show`, page-dump `curl`, oversized `Read`s) to their ccx equivalents in place and BLOCKS the rest — so reach for ccx first.

1. **Orient a repo** → `ccx repo overview`
2. **"How does X work / where is Y" (intent)** → `ccx code search "<question>"` (semantic, semble-backed)
3. **A specific symbol (def + callers + callees)** → `ccx code symbol <name>` (alias `ccx code grok`)
4. **Literal / structural text** → `ccx code grep <text> [--glob G] [--scope dir] [-i] [-w]` (`-i`/`-w` run on ripgrep; system `grep` fills in when `rg` is missing)
5. **List files** → `ccx repo find "<glob>"`
6. **Read a file** → `ccx code outline <file-or-dir>` first (ast-grep structural map for the languages it outlines and any directory, tilth signatures otherwise), then `ccx code read <file> --section A-B` for the part you need (whole file: `ccx code read <file> --full`)
7. **Edit a file** → `ccx code edit <file> --at A-B#hash --content <text>` (hash-verified write: refuses on anchor mismatch, re-anchors moved content, returns the new anchor so edits chain; `--content -` reads stdin, `--delete` removes the range)
8. **Review changes** → `ccx vcs diff [src]` (structural, jj-aware; exact hunks: `git diff -- <file>`)
9. **Inspect one commit** → `ccx vcs show [ref]` (message + structural per-file diff; default `@-`/HEAD)
10. **How a file evolved** → `ccx vcs history <path> [-n N]` (per-commit sha · date · subject + changed symbols)
11. **Locate a repo/module/package on disk** → `ccx repo locate <name>` (sibling repo, Go module, or Python package; prints tab-separated `kind`/`path`/`version`, exit 3 when unresolved)
12. **Commit, push, watch CI** → `ccx vcs ship -m "<msg>"` (jj-aware commit + push + `gh run watch --exit-status` in one call)
13. **Compose several calls / post-process any output** → `ccx exec '<python>'` — a sandboxed script whose async host functions are every ccx query op, a gated `sh(cmd)`, and every stateless MCP server's tools (auto-reflected, no flag needed); only the script's return value enters context. Rule of thumb: one question → one ccx call (entries 1–12, 14–17); a pipeline, filter, fan-out, or any output you'd immediately post-process (project a JSON blob, sweep signatures across files, join search hits) → exec. Discover the host functions and the Python-subset rules with `ccx exec --list-tools` (MCP: `ccx_exec_tools`), once per session.
14. **Re-encode JSON tool output** → `ccx format -- <cmd>` (or `… | ccx format`) — a shape classifier picks the leanest encoding (prose, markdown table, CSV/TSV, TOON, TRON, JSONL, or compact JSON), never larger than compact JSON by bytes; `--format=X` forces one encoder
15. **Map a web page** → `ccx web outline <url>` (heading tree with stable `§` section refs; pages cache 24h, `--refresh` refetches)
16. **Read one section of a page** → `ccx web read <url> --section <ref>` (budget-capped section subtree + prev/next nav; whole page: `--full`)
17. **Ask a question of a page** → `ccx web search <url> "<question>"` (top-k relevant chunks with `<url> §2.3#hash` cites; hybrid BM25 + local embeddings, BM25-only with a note when `uv` is absent)

Entries 9–12 are CLI-only — the MCP mirrors the query surface (1–8) plus exec (13), format (14, as `BashFormat`), and web (15–17, as `ccx_web_outline`/`ccx_web_read`/`ccx_web_search`), not these.

Durable prose — plans, reviews, memory files — cites code as `path:line#hash` (e.g. `internal/render/finalize.go:31#k2fa`); any later session resolves the cite statelessly with ccx, because the hash re-anchors by content even after the file drifts.

Reach for your **LSP** when the answer must be exhaustive/structural (findReferences, rename, goToImplementation). Use **Grep/Glob** only for literal content in non-source files (logs, JSON, YAML).
