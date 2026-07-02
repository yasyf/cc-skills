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
5. **Value types by default.** Reach for `struct` and `enum`; use `class` only
   for reference semantics or when a framework demands it.
6. **Let the compiler prove it.** Prefer `let` over `var`, non-optional over
   optional, and exhaustive `switch` over `default`. Push errors to compile time.
7. **No force-unwraps.** `!` and `try!` are banned outside tests. Unwrap with
   `guard let` and fail loud with a typed error.

## Swift

Naming follows the [Swift API Design Guidelines](https://www.swift.org/documentation/api-design-guidelines/):
`UpperCamelCase` for types, `lowerCamelCase` for everything else, methods that
read as phrases at the call site.

### Make invalid states unrepresentable

Model mutually exclusive states as an `enum` with associated values, not a bag of
optionals where some combinations are illegal.

```swift
// Good
enum FetchState {
    case idle
    case loading(Task<Data, Error>)
    case loaded(Data)
    case failed(Error)
}

// Bad
struct FetchState {
    var isLoading: Bool
    var task: Task<Data, Error>?
    var data: Data?
    var error: Error?
}
```

### Guard at the edges

Unwrap with `guard ... else` and exit early; keep the happy path unindented.

```swift
// Good
func export(_ report: Report?) throws -> URL {
    guard let report else { throw ExportError.noReport }
    return try report.write(to: outputURL)
}

// Bad
func export(_ report: Report?) throws -> URL {
    if report != nil {
        return try report!.write(to: outputURL)
    } else {
        throw ExportError.noReport
    }
}
```

### Concurrency

Use `async`/`await` and structured concurrency; don't thread completion-handler
callbacks through the code.

```swift
// Good
func loadReport(id: ReportID) async throws -> Report

// Bad
func loadReport(id: ReportID, completion: @escaping (Result<Report, Error>) -> Void)
```

### Access control

Default to `private`; widen to `internal` or `public` only when another type
needs it. Hide internals with the access modifier, never a naming convention.

### Logging

Diagnostics go through `os.Logger` with per-module categories on the project's
subsystem — never `print` for logging.

```swift
import os

extension Logger {
    static let app = Logger(subsystem: "com.{{GITHUB_USER}}.{{PROJECT_NAME}}", category: "App")
}
```

## Error Handling

Keep error-handling blocks minimal: only the operation that can fail belongs
inside. No catch-all handlers that swallow everything; use dedicated error types.
Read required configuration so a missing key fails at startup. No sentinel return
values; throw, or return a typed result.

## Code Organization

Order each module: imports, constants, type aliases, helpers, classes, then
functions. Constants sit immediately after imports, before any class or function.
Use the language's access-control mechanism instead of underscore/naming
conventions to hide internals.

## Comments & Docstrings

Code documents itself through names, types, and organization. No comments except
TODOs, non-obvious workarounds, or disabled code. Document the public API only;
a doc comment that restates the signature is clutter to delete.

## Testing

Use Swift Testing, never XCTest: free `@Test` functions, `#expect` for
assertions, `#require` for unwrap-or-fail. Write strict assertions against
specific expected values; a test that can't fail uncovers nothing. Parameterize
repeated test bodies with `@Test(arguments:)`, giving each case its own expected
value.

```swift
@Test(arguments: [("Ada", "Hello, Ada!"), ("Grace", "Hello, Grace!")])
func greets(name: String, expected: String) {
    #expect(greeting(for: name) == expected)
}
```

Mock the boundaries your code talks to, such as the network, filesystem, and
clock, and leave the function under test real. A database (or any stateful
service) is not a mock boundary: when a test needs one, start a real ephemeral
instance rather than mocking the driver or using an in-memory fake.
