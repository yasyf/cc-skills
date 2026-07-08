# {{PROJECT_NAME}} Development Guide

{{DESCRIPTION}}

## Repository Structure

```
{{PROJECT_NAME}}/
├── {{PROJECT_NAME}}.xcodeproj/  # Synced-folder project — one committed project.pbxproj
├── {{PROJECT_NAME}}/            # App sources (file-system-synchronized group)
│   ├── App/                     # Entry point + SwiftUI views
│   └── Assets.xcassets/         # App icon + accent color
├── {{PROJECT_NAME}}Tests/       # Swift Testing target (synchronized group)
├── AGENTS.md                    # This file — shared conventions
├── README.md                    # Project overview
└── STYLEGUIDE.md                # Swift style rules for this repo
```

Sources live in file-system-synchronized folders: add a `.swift` file by creating
it under `{{PROJECT_NAME}}/` (app) or `{{PROJECT_NAME}}Tests/` (tests) — no
`.pbxproj` edit. The app targets iOS {{IOS_DEPLOYMENT_TARGET}} (Swift 6 language
mode, complete strict concurrency). The project file uses fixed synthetic UUIDs —
never regenerate it or accept an Xcode "upgrade" of it.

{{> _partials/ask-before-assuming.md}}

{{> _partials/code-review-response.md}}

{{> _partials/parallelize.md}}

{{> _partials/writing-plans.md}}

{{> _partials/ccx.md}}

## Swift Style

Swift 6 language mode with complete strict concurrency. Build and test through
`xcodebuild` (or XcodeBuildMCP — see General Rules), not `swift build`: this is an
Xcode app project, not an SPM package.

**Doc comments on the public API only.** Public types and functions carry a `///` summary; internals get none. No other comments except TODOs, non-obvious workarounds, or disabled code.

**Typed errors, thrown.** Failures are `Error`-conforming enums thrown up the stack — no sentinel returns, no `fatalError` for recoverable conditions. See STYLEGUIDE.md § Error Handling.

**Logging via `os.Logger`.** Diagnostics go through per-module `Logger` categories on the `{{BUNDLE_ID}}` subsystem (`extension Logger { static let capture = Logger(subsystem: "{{BUNDLE_ID}}", category: "Capture") }`) — never `print`. Stream them with `log stream --predicate 'subsystem == "{{BUNDLE_ID}}"'`.

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

**Testing.** Tests live in the `{{PROJECT_NAME}}Tests` target and use Swift Testing — free `@Test` functions with `#expect`/`#require` against specific expected values. Run them with `xcodebuild test -project {{PROJECT_NAME}}.xcodeproj -scheme {{PROJECT_NAME}} -destination 'platform=iOS Simulator,name=iPhone 17'` (use any installed simulator).

**XcodeBuildMCP.** If using XcodeBuildMCP, use the installed `xcodebuildmcp-cli` skill before calling XcodeBuildMCP tools.

**Writing docs.** When writing or revising docs, a README, a tutorial, a how-to, or reference, use the `writing-docs` skill (Diataxis modes, voice rules, and runnable code-sample rules) and run `slop-cop check <file> --lang=markdown` before you finish (slop-cop is a Go binary; if it's not on PATH, run the `/slop-cop-check` skill — never `uvx slop-cop`).

{{> _partials/version-control.md}}
