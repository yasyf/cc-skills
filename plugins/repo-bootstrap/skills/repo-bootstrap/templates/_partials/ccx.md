## Compact Context (ccx)

`cc-context` — the `ccx` CLI and the `cc-context` MCP (its `mcp__cc-context__*` tools mirror the CLI 1:1) — is the DEFAULT for reading code, finding symbols, searching, and reviewing diffs. It returns token-bounded output (signatures + line numbers, explicit overflow, never silent truncation) instead of raw dumps, and the capt-hook `ccx` guard pack BLOCKS the token-heavy primitives — so reach for ccx first.

1. **Orient a repo** → `ccx overview`
2. **"How does X work / where is Y" (intent)** → `ccx search "<question>"` (semantic, semble-backed)
3. **A specific symbol (def + callers + callees)** → `ccx symbol <name>` (alias `ccx grok`)
4. **Literal / structural text** → `ccx grep <text> [--glob G]`
5. **List files** → `ccx find "<glob>"`
6. **Read a file** → `ccx outline <file>` first, then `ccx read <file> --section A-B` for the part you need (whole file: `ccx read <file> --full`)
7. **Review changes** → `ccx diff [src]` (structural, jj-aware; exact hunks: `git diff -- <file>`)

Reach for your **LSP** when the answer must be exhaustive/structural (findReferences, rename, goToImplementation). Use **Grep/Glob** only for literal content in non-source files (logs, JSON, YAML).
