# {{PROJECT_NAME}} Style Guide

The concrete style rules for this repository.

## Core Principles

1. **Fail fast, fail loud.** No defensive coding: no fallbacks, shims, or
   backwards-compat layers, and no guards against impossible states. No sentinel
   values, no silent defaults. If unused, delete it. Crash on the unexpected.
2. **Make invalid states unrepresentable.** Branded/newtype primitives, immutable
   data structures, required fields over optionals.
3. **Minimal changes.** Stay within scope. Make the test pass, then stop. Improve
   only the code you touch.
4. **Match surrounding code.** Follow this guide first, then the file you're in,
   then the module. If surrounding code violates this guide, fix it.

## TypeScript Rules

1. **Type everything; never `any`.** Use `unknown` at boundaries and narrow.
   `strict` is on in `tsconfig.json`; don't weaken it per-file.

   ```ts
   // Good
   function parseEvent(raw: unknown): SessionEvent {
     if (!isSessionEvent(raw)) throw new Error(`not a session event: ${JSON.stringify(raw)}`)
     return raw
   }

   // Bad
   function parseEvent(raw: any): SessionEvent {
     return raw
   }
   ```

2. **Discriminated unions for state.** Model mutually exclusive states as a
   tagged union, not a bag of optionals.

   ```ts
   // Good
   type Session =
     | { status: "running"; pid: number }
     | { status: "exited"; code: number }

   // Bad
   type Session = { status: string; pid?: number; code?: number }
   ```

3. **Functional over imperative.** `const` over `let`, expression-level
   `map`/`filter`/`flatMap` over accumulator loops, `readonly` arrays in
   signatures.

   ```ts
   // Good
   const titles = sessions.filter((s) => s.status === "running").map((s) => s.title)

   // Bad
   let titles = []
   for (const s of sessions) {
     if (s.status === "running") titles.push(s.title)
   }
   ```

## Naming & Idioms

`camelCase` for functions and values, `PascalCase` for types and classes,
`SCREAMING_SNAKE_CASE` for module constants. Source files are lower-case
(`app.ts`), one cohesive unit each. ESM only, with explicit `.ts` extensions on
relative imports (`import { buildApp } from "./app.ts"`); bun runs TypeScript
directly — no build step, no emitted JS. Top-level `await` over `main()`
wrappers in entry points.

## Error Handling

Keep error-handling blocks minimal: only the operation that can fail belongs
inside. No catch-all handlers that swallow everything; use dedicated error types.
Read required configuration so a missing key fails at startup. No sentinel return
values; raise, or return a typed result.

## Code Organization

Order each module: imports, constants, type aliases, helpers, classes, then
functions. Constants sit immediately after imports, before any class or function.
Use the language's export-control mechanism instead of underscore/naming
conventions to hide internals.

## Comments & Docstrings

Comments are terse and used sparingly — the code documents itself through names, types,
and organization. The one exception is documentation-generation comments: the doc
comments your language's doc tool renders for the public API, each a real description
rather than a restatement of the signature. Beyond those, comment only for TODOs,
non-obvious workarounds, or disabled code.

## Testing

Write strict assertions against specific expected values; a test that can't fail
uncovers nothing. Mock the boundaries your code talks to, such as the network,
filesystem, and clock, and leave the function under test real. A database (or any
stateful service) is not a mock boundary: when a test needs one, start a real
ephemeral instance with testcontainers rather than mocking the driver or using an
in-memory fake. Parameterize repeated test bodies, giving each case a descriptive
id and its own expected values.
