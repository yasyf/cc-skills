# {{PROJECT_NAME}} Development Guide

{{#FEATURE_RELEASE}}{{DESCRIPTION}} Distributed via Homebrew: `brew install {{GITHUB_USER}}/tap/{{PROJECT_NAME}}`.{{/FEATURE_RELEASE}}{{^FEATURE_RELEASE}}{{DESCRIPTION}}{{/FEATURE_RELEASE}}

## Repository Structure

```
{{PROJECT_NAME}}/
├── cmd/{{PROJECT_NAME}}/   # main package — the CLI entry point
├── internal/
│   ├── cli/               # cobra command tree — TODO(bootstrap): name the commands
│   ├── version/           # build version, stamped via -ldflags
│   └── log/               # slog setup
├── .github/               # GitHub Actions workflows
├── AGENTS.md              # This file — shared conventions
└── README.md              # Project overview
```

{{> _partials/ask-before-assuming.md}}

{{> _partials/code-review-response.md}}

{{> _partials/parallelize.md}}

{{> _partials/writing-plans.md}}

## Code Search

`semble` is wired up via `.mcp.json` (project-scoped MCP server, runs via `uvx` — nothing to install). It's the default tool for any "find code by intent or symbol" question:

1. **"How do we do X?" / "Where is the code that does Y?"** → `semble.search("...")`
2. **"Where is `Foo` defined?"** → `semble.search("Foo")` (or `search("func Foo")` for a relevance boost)
3. **"Show me other code like this"** → `semble.find_related` on a prior hit
4. **Cross-repo lookup** → pass an `https://...git` URL as `repo`

`repo` defaults to the current project root for local searches. Semble is purely semantic — it ranks by meaning, not substring, so it won't find literal strings that don't appear in nearby code.

Reach for your **LSP** (gopls) when the answer must be *exhaustive* or *structural*:

1. **"Who calls X?" / "find every reference"** → `findReferences` / `incomingCalls`
2. **"Rename X → Y"** → `findReferences` first to enumerate every call site
3. **"What's the type of X?"** → `hover`
4. **"What implements interface I?"** → `goToImplementation`

Reach for **`Grep`** only for material neither tool indexes: literal *content* of strings/comments (error messages, hard-coded URLs, env-var names, TODOs) and non-source files (logs, JSON, YAML, fixtures). File-pattern questions ("all `*.go` under `internal/`") go through `Glob`.

## Go Style

Target Go {{GO_VERSION}}+. Run `task build`, `task test` (`go test -race`), and `task lint`.

**Doc comments on exported identifiers only.** Exported types, funcs, and the package itself carry a doc comment that starts with the identifier name (`// NewRootCmd builds …`). Unexported helpers get none. No other comments except TODOs, non-obvious workarounds, or disabled code.

**Errors wrap with `%w`.** Return failures up the stack with `fmt.Errorf("…: %w", err)` and inspect them with `errors.Is` / `errors.As`, never string matching. See STYLEGUIDE.md § Error Handling.

**Structured logging via `log/slog`.** Diagnostics go through the configured default logger (`slog.Info`, `slog.Debug`) with key-value attrs — never `fmt.Println` for logging. See `internal/log`.

@STYLEGUIDE.md

## General Rules

**Minimal changes.** Stay within scope; fix the issue, then stop.

**Match surrounding code.** Follow the conventions of the file you're in, then the package.

**No defensive coding.** No fallbacks, shims, or backwards-compat layers; no guards against impossible states. If unused, delete it. Crash on the unexpected.

**Search before writing.** Before creating a helper, query the codebase via `semble.search` (intent or symbol queries both work). Sibling packages win over re-implementation.

**Code stewardship.** When you touch a file, fix nearby bugs, style violations, and broken tests; don't wave them off as pre-existing or out of scope.

**Observe, don't infer.** Inspect actual data — read fixtures, dump structs, run the code — before reasoning from assumption.

**Don't use external failures as an excuse to stop.** API quota, rate-limit, and outage errors rarely block the whole task; trace the catch sites and confirm a failure actually stops you before claiming it does.

**Verify before asserting.** Don't report something as working, fixed, blocked, or impossible until you've checked — run it, read the output, reproduce the failure. "It should work" is not "it works."

**Reproduce before fixing.** When something breaks, isolate the smallest failing case before editing or re-running. Re-running the whole command while changing code between runs hides the root cause; narrow to the one failing test or input first.

**Research after repeated failure.** After ~2 failed approaches, stop guessing and gather evidence — search the web, read the docs and source — before a third attempt.

**Get a second opinion on a plateau.** On a debugging plateau (2 failed attempts before a 3rd), a non-trivial architectural decision, or algorithmic/security-sensitive code, get an outside check (e.g. `/codex`) before committing to the approach.

**Don't contort code to satisfy a linter.** The compiler and `golangci-lint` serve the code, not the other way around. Don't widen a type to `any`, bolt on a needless type assertion, or sprinkle `//nolint` just to silence a diagnostic. If a clean fix isn't obvious, leave the diagnostic — a visible one is preferable to scar tissue.

**Mechanical linting.** The pre-commit hooks (prek: gofumpt + goimports + golangci-lint) format and lint on every `git commit` — run `uvx prek install` once to activate them. Leave formatting and linting to the hook; never run `gofumpt` or `golangci-lint` by hand (the `go` capt-hook pack blocks it). When reviewing code, don't flag mechanical lint violations (gofmt, import order, line length).

**Testing.** Tests live beside the code as `*_test.go`; run them with `task test` (`go test -race ./...`). Write table-driven tests with strict assertions against specific values, mock the boundaries your code talks to (network, filesystem, clock), and leave the code under test real.

**Writing docs.** When writing or revising docs, a README, a tutorial, a how-to, or reference, use the `writing-docs` skill (Diataxis modes, voice rules, and runnable code-sample rules) and run `slop-cop check <file> --lang=markdown` before you finish (slop-cop is a Go binary; if it's not on PATH, run the `/slop-cop-check` skill — never `uvx slop-cop`).

{{> _partials/version-control.md}}
{{#FEATURE_RELEASE}}

**Releases.** Tagging `v*` triggers `.github/workflows/release.yml`, which runs goreleaser to build the binaries, cut a GitHub release, and push the Homebrew cask to `{{GITHUB_USER}}/homebrew-tap`. The version comes from the tag. The release refuses to run unless the tagged commit is on `main` — tag a merged commit (e.g. `git tag vX.Y.Z origin/main`), not a feature branch. One-time setup: a `HOMEBREW_TAP_TOKEN` repo secret with push access to the tap. The macOS binaries are Developer-ID-signed and notarized when the `MACOS_*` repo secrets are set (optional; releases unsigned without them — see `reference/go-ci-and-release.md`).
{{/FEATURE_RELEASE}}
