## TypeScript Style

Run with `bun start`, test with `bun test`, type-check with `bun run typecheck`. bun executes TypeScript directly — no build step, no emitted JS. ESM only, with explicit `.ts` extensions on relative imports.

**Type everything; never `any`.** `strict` is on in `tsconfig.json` — keep it on. Use `unknown` at boundaries and narrow with a type guard; model mutually exclusive states as a discriminated union, not a bag of optionals. See STYLEGUIDE.md § TypeScript Rules.

**Functional over imperative.** `const` over `let`, expression-level `map`/`filter`/`flatMap` over accumulator loops, `readonly` arrays in signatures.

**Doc comments on the public API only.** Exported types and functions carry a terse summary; internals get none. No other comments except TODOs, non-obvious workarounds, or disabled code.

**Typed errors, thrown.** Failures raise or return a typed result — no sentinel returns, no silent defaults. See STYLEGUIDE.md § Error Handling.

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

**Don't contort code to satisfy the type checker.** `tsc` serves the code, not the other way around. Don't reshape a data model, widen a type to `any`, or bolt on a `cast`/blanket `// @ts-ignore` just to silence a diagnostic. If a clean fix isn't obvious, leave the diagnostic — a visible one is preferable to scar tissue.

**Mechanical linting.** No auto-formatter is wired here; keep to the surrounding file's style. When reviewing code, don't flag mechanical issues (whitespace, ordering, line length, trailing commas).

**Testing.** Tests live in `tests/` and use `bun:test` — `import { expect, test } from "bun:test"`, strict assertions against specific expected values. Run them with `bun test`, and type-check with `bun run typecheck`. Mock the boundaries the code talks to (network, filesystem, clock) and leave the function under test real.

**Writing docs.** When writing or revising docs, a README, a tutorial, a how-to, or reference, use the `writing-docs` skill (Diataxis modes, voice rules, and runnable code-sample rules) and run `slop-cop check <file> --lang=markdown` before you finish (slop-cop is a Go binary; if it's not on PATH, run the `/slop-cop-check` skill — never `uvx slop-cop`).
