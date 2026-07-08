# {{PROJECT_NAME}} Development Guide

{{#FEATURE_RELEASE}}{{DESCRIPTION}} Distributed via Homebrew: `brew install {{GITHUB_USER}}/tap/{{PROJECT_NAME}}`.{{/FEATURE_RELEASE}}{{^FEATURE_RELEASE}}{{DESCRIPTION}}{{/FEATURE_RELEASE}}

## Repository Structure

```
{{PROJECT_NAME}}/
├── Package.swift               # SPM manifest — targets, products, dependencies
├── Sources/
│   ├── {{MODULE_NAME}}/        # the library — all logic lives here
│   └── {{PROJECT_NAME}}/       # the executable — a thin ArgumentParser shell
├── Tests/{{MODULE_NAME}}Tests/ # Swift Testing (@Test / #expect) against the library
├── .github/                    # GitHub Actions workflows
├── AGENTS.md                   # This file — shared conventions
└── README.md                   # Project overview
```

{{> _partials/ask-before-assuming.md}}

{{> _partials/code-review-response.md}}

{{> _partials/parallelize.md}}

{{> _partials/writing-plans.md}}

{{> _partials/ccx.md}}

## Swift Style

Swift 6 language mode. Build with `swift build`, test with `swift test`, run with `swift run {{PROJECT_NAME}}`.

**Logic in the library, not the executable.** `Sources/{{PROJECT_NAME}}/` holds only the ArgumentParser command tree; everything it calls lives in `Sources/{{MODULE_NAME}}/`, where tests can reach it. A command body longer than argument parsing + one library call is logic in the wrong target.

**Doc comments on the public API only.** Public types and functions carry a `///` summary; internals get none. No other comments except TODOs, non-obvious workarounds, or disabled code.

**Typed errors, thrown.** Failures are `Error`-conforming enums thrown up the stack — no sentinel returns, no `fatalError` for recoverable conditions. See STYLEGUIDE.md § Error Handling.

@STYLEGUIDE.md

## General Rules

**Minimal changes.** Stay within scope; fix the issue, then stop.

**Match surrounding code.** Follow the conventions of the file you're in, then the module.

**No defensive coding.** No fallbacks, shims, or backwards-compat layers; no guards against impossible states. If unused, delete it. Crash on the unexpected.

**Search before writing.** Before creating a helper, query the codebase via `ccx code search` (intent or symbol queries both work). Sibling modules win over re-implementation.

**Code stewardship.** When you touch a file, fix nearby bugs, style violations, and broken tests; don't wave them off as pre-existing or out of scope.

**Observe, don't infer.** Inspect actual data — read fixtures, dump values, run the code — before reasoning from assumption.

**Don't use external failures as an excuse to stop.** API quota, rate-limit, and outage errors rarely block the whole task; trace the catch sites and confirm a failure actually stops you before claiming it does.

**Verify before asserting.** Don't report something as working, fixed, blocked, or impossible until you've checked — run it, read the output, reproduce the failure. "It should work" is not "it works."

**Reproduce before fixing.** When something breaks, isolate the smallest failing case before editing or re-running. Re-running the whole command while changing code between runs hides the root cause; narrow to the one failing test or input first.

**Research after repeated failure.** After ~2 failed approaches, stop guessing and gather evidence — search the web, read the docs and source — before a third attempt.

**Get a second opinion on a plateau.** On a debugging plateau (2 failed attempts before a 3rd), a non-trivial architectural decision, or algorithmic/security-sensitive code, get an outside check (e.g. `/codex`) before committing to the approach.

**Don't contort code to satisfy a linter.** The compiler and SwiftLint serve the code, not the other way around. Don't force-cast, widen a type to `Any`, or sprinkle `// swiftlint:disable` just to silence a diagnostic. If a clean fix isn't obvious, leave the diagnostic — a visible one is preferable to scar tissue.

**Mechanical linting.** Running `swiftformat .`/`swiftlint` by hand is fine, and encouraged — the pre-commit hooks (prek: swiftformat + swiftlint, calling the brew-installed binaries) also run on every `git commit`; run `uvx prek install` once to activate them. Fix what needs human judgment and let the tooling own the mechanical churn. When reviewing code, don't flag mechanical lint violations (whitespace, ordering, line length).

**Testing.** Tests live in `Tests/{{MODULE_NAME}}Tests/` and use Swift Testing — free `@Test` functions with `#expect`/`#require` against specific expected values, parameterized via `@Test(arguments:)`. Run them with `swift test`. Mock the boundaries the code talks to (network, filesystem, clock) and leave the function under test real.

**XcodeBuildMCP.** If using XcodeBuildMCP, use the installed `xcodebuildmcp-cli` skill before calling XcodeBuildMCP tools.

**Writing docs.** When writing or revising docs, a README, a tutorial, a how-to, or reference, use the `writing-docs` skill (Diataxis modes, voice rules, and runnable code-sample rules) and run `slop-cop check <file> --lang=markdown` before you finish (slop-cop is a Go binary; if it's not on PATH, run the `/slop-cop-check` skill — never `uvx slop-cop`).

{{> _partials/version-control.md}}
{{#FEATURE_RELEASE}}

**Releases.** Tagging `v*` triggers `.github/workflows/release.yml`, which forwards to the shared `release-swift.yml@swift-v1` reusable workflow: a universal (arm64 + x86_64) `swift build`, a GitHub release, and a Homebrew binary cask pushed to `{{GITHUB_USER}}/homebrew-tap`. The version comes from the tag, and the executable product must keep the repo's name — that's the whole calling contract. The release refuses to run unless the tagged commit is on `main` — tag a merged commit (e.g. `git tag vX.Y.Z origin/main`), not a feature branch. One-time setup: a `HOMEBREW_TAP_TOKEN` repo secret with push access to the tap. The binary is Developer-ID-signed and notarized when the `MACOS_*` repo secrets are set (optional; releases unsigned without them — see `reference/swift-ci-and-release.md`).
{{/FEATURE_RELEASE}}
