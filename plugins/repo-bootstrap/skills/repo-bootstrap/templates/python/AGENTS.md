# {{PROJECT_NAME}} Development Guide

{{#FEATURE_PYPI}}{{DESCRIPTION}} Published to PyPI as `{{DIST_NAME}}`; the CLI is `{{DIST_NAME}}`, run as `uvx {{DIST_NAME}}`.{{/FEATURE_PYPI}}{{^FEATURE_PYPI}}{{DESCRIPTION}} The CLI is `{{DIST_NAME}}`, run with `uv run {{DIST_NAME}}`.{{/FEATURE_PYPI}}

## Repository Structure

```
{{PROJECT_NAME}}/
├── {{PACKAGE}}/      # The package — TODO(bootstrap): name the key modules
├── tests/            # Pytest suite
├── .github/          # GitHub Actions workflows
├── AGENTS.md         # This file — shared conventions
└── README.md         # Project overview
```

{{> _partials/ask-before-assuming.md}}

{{> _partials/code-review-response.md}}

{{> _partials/parallelize.md}}

{{> _partials/writing-plans.md}}

## Code Search

`semble` is wired up via `.mcp.json` (project-scoped MCP server, runs via `uvx` — nothing to install). It's the default tool for any "find code by intent or symbol" question:

1. **"How do we do X?" / "Where is the code that does Y?"** → `semble.search("...")`
2. **"Where is `Foo` defined?"** → `semble.search("Foo")` (or `search("class Foo")` for a relevance boost)
3. **"Show me other code like this"** → `semble.find_related` on a prior hit
4. **Cross-repo lookup** → pass an `https://...git` URL as `repo`

`repo` defaults to the current project root for local searches. Semble is purely semantic — it ranks by meaning, not substring, so it won't find literal strings that don't appear in nearby code.

Reach for your **LSP** when the answer must be *exhaustive* or *structural*:

1. **"Who calls X?" / "find every reference"** → `findReferences` / `incomingCalls`
2. **"Rename X → Y"** → `findReferences` first to enumerate every call site
3. **"What's the type of X?"** → `hover`
4. **"What implements Protocol P?"** → `goToImplementation`

Reach for **`Grep`** only for material neither tool indexes: literal *content* of strings/comments/docstrings (error messages, hard-coded URLs, env-var names, TODOs) and non-source files (logs, JSON, YAML, fixtures). File-pattern questions ("all `*.json` under `src/`") go through `Glob`.

## Python Style

Target Python {{PYTHON_MIN}}+. Run `uv sync --extra dev`, `uv run pytest`, and `uv build`.

**Docstrings on the public API only.** User-facing surfaces carry Google-style docstrings{{#FEATURE_DOCS}}; they render into the docs site via Great Docs{{/FEATURE_DOCS}}. Internal helpers get none. No comments except TODOs, non-obvious workarounds, or disabled code.

**Async-native from day 1.** Anything that touches I/O is `async def`, backed by a library with a native async API (e.g. `aiosqlite`) rather than a blocking call wrapped in `asyncio.to_thread`. Concurrency and tests run through `anyio`. See STYLEGUIDE.md § Async.

@STYLEGUIDE.md

## General Rules

**Minimal changes.** Stay within scope; fix the issue, then stop.

**Match surrounding code.** Follow the conventions of the file you're in, then the module.

**No defensive coding.** No fallbacks, shims, or backwards-compat layers; no guards against impossible states. If unused, delete it. Crash on the unexpected.

**Search before writing.** Before creating a helper, query the codebase via `semble.search` (intent or symbol queries both work). Sibling modules and base classes win over re-implementation.

**Code stewardship.** When you touch a file, fix nearby bugs, style violations, and broken tests; don't wave them off as pre-existing or out of scope. Trivial type-checker noise is the exception (see § Python Style).

**Observe, don't infer.** Inspect actual data — read fixtures, dump objects, run the code — before reasoning from assumption.

**Don't use external failures as an excuse to stop.** API quota, rate-limit, and outage errors rarely block the whole task; trace the catch sites and confirm a failure actually stops you before claiming it does.

**Verify before asserting.** Don't report something as working, fixed, blocked, or impossible until you've checked — run it, read the output, reproduce the failure. "It should work" is not "it works."

**Reproduce before fixing.** When something breaks, isolate the smallest failing case before editing or re-running. Re-running the whole command while changing code between runs hides the root cause; narrow to the one failing call, payload, or test first.

**Research after repeated failure.** After ~2 failed approaches, stop guessing and gather evidence — search the web, read the docs and source — before a third attempt.

**Get a second opinion on a plateau.** On a debugging plateau (2 failed attempts before a 3rd), a non-trivial architectural decision, or algorithmic/security-sensitive code, get an outside check (e.g. `/codex`) before committing to the approach.

**Don't contort code to satisfy a checker.** The type checker and linter serve the code, not the other way around. Don't reshape a data model, widen a type, or bolt on a `cast(...)` / narrowing-only `assert isinstance(...)` / blanket ignore just to silence a diagnostic. If a clean fix isn't obvious, leave the diagnostic — a visible diagnostic is preferable to scar tissue. (Most checker noise isn't worth acting on at all; act only when it flags a real bug.)

**Mechanical linting.** The pre-commit hooks (prek: ruff + ty) auto-format, fix import order, and print whole-project type warnings on every `git commit` (ty never blocks a commit) — run `uvx prek install` once to activate them. Leave `ruff` to the hook and fix only what needs human judgment; clean everything up-front with `uvx prek run --all-files` if you want. When reviewing code, don't flag mechanical lint violations (line length, whitespace, import order, trailing commas).

**Testing.** The suite lives in `tests/`; run it with `uv run pytest`. Use strict assertions and mock external dependencies while leaving the code under test real. Databases and other stateful services are not mock boundaries — when a test needs one, run a real ephemeral instance via `testcontainers` instead of mocking the driver.

**Writing docs.** When writing or revising docs, a README, a tutorial, a how-to, or reference, use the `writing-docs` skill (Diataxis modes, voice rules, and runnable code-sample rules) and run `slop-cop check <file> --lang=markdown` before you finish.

{{#FEATURE_DOCS}}
**Docs.** Any public API change must keep `uv run great-docs build` green; run `uv sync --group docs` first.

{{/FEATURE_DOCS}}
{{> _partials/version-control.md}}
{{#FEATURE_PYPI}}

**Releases.** Tagging `v*` triggers `.github/workflows/release-pypi.yml`, which builds, publishes to PyPI via trusted publishing, and cuts a GitHub release. The version comes from the tag. The release refuses to run unless the tagged commit is on `main` — tag a merged commit (e.g. `git tag vX.Y.Z origin/main`), not a feature branch.
{{/FEATURE_PYPI}}
