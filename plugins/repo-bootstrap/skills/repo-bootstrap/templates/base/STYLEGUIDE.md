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

TODO(bootstrap): add language-specific rules — naming, organization, error
handling, idioms — with Good/Bad examples for each, following the sections below.

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

Code documents itself through names, types, and organization. No comments except
TODOs, non-obvious workarounds, or disabled code. Document the public API only;
a doc comment that restates the signature is clutter to delete.

## Testing

Write strict assertions against specific expected values; a test that can't fail
uncovers nothing. Mock the boundaries your code talks to, such as the network,
filesystem, and clock, and leave the function under test real. A database (or any
stateful service) is not a mock boundary: when a test needs one, start a real
ephemeral instance with testcontainers rather than mocking the driver or using an
in-memory fake. Parameterize repeated test bodies, giving each case a descriptive
id and its own expected values.
