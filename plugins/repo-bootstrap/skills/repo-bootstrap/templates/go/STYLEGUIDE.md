# {{PROJECT_NAME}} Style Guide

The concrete style rules for this module. Target Go {{GO_VERSION}}+.

## Core Principles

1. **Fail fast, fail loud.** No defensive coding: no fallbacks, shims, or
   backwards-compat layers, and no guards against impossible states. Return an
   error or `panic` on the truly impossible; never paper over it with a zero value.
2. **Make invalid states unrepresentable.** Named types for branded primitives,
   small interfaces, required struct fields over optional pointers.
3. **Minimal changes.** Stay within scope. Make the test pass, then stop. Improve
   only the code you touch.
4. **Match surrounding code.** Follow this guide first, then the file you're in,
   then the package. If surrounding code violates this guide, fix it.
5. **Errors are values.** Wrap with `%w`, inspect with `errors.Is`/`errors.As`,
   and let them flow up — don't log-and-swallow.
6. **Accept interfaces, return structs.** Take the narrowest interface a function
   needs; return concrete types so callers aren't boxed in.
7. **Flat over nested.** Handle the error first and return early; the happy path
   stays at the left margin. Nesting deeper than three levels is a smell.

## Errors

Wrap with context as an error travels up; never match on error strings.

```go
// Good — wrap with %w, inspect with errors.Is / errors.As
cfg, err := loadConfig(path)
if err != nil {
	return fmt.Errorf("load config %q: %w", path, err)
}

// Good — sentinel + typed errors for callers to branch on
var ErrNotFound = errors.New("not found")

if errors.Is(err, ErrNotFound) { ... }

// Bad — string matching, lost context
if strings.Contains(err.Error(), "not found") { ... }
```

Keep the fallible operation alone on its line; don't wrap a whole block in error
handling. Read required configuration at startup so a missing key fails loudly,
not lazily on first use.

### Exit codes (the CLI convention to grow into)

The starter `main` prints the error and exits 1. As real commands land, map error
*categories* to exit codes with a typed taxonomy in `internal/cli` — a `UsageError`
(exit 2), a not-found (exit 3), and so on — and an `ExitCode(err) int` /
`Label(err) string` pair that `main` consults. Stdout carries machine-readable
output (often JSON); stderr carries `label: message`; the exit code encodes the
category for scripts. Add it when the second error type appears, not before.

## Concurrency & Context

Pass `context.Context` as the first parameter of anything that does I/O or can
block, and honor cancellation. Prefer structured concurrency (`errgroup`) over bare
goroutines; never start a goroutine you can't account for.

```go
// Good — ctx threaded, cancellation honored
func fetch(ctx context.Context, url string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("build request: %w", err)
	}
	...
}
```

## Code Organization

One package per directory; `internal/` for everything not part of a deliberate
public API. Order each file: package doc comment, imports, constants, types,
constructors, then methods. Exported identifiers are the API surface — keep the
exported set small and unexport anything callers don't need.

Use Go's capitalization for visibility; never an underscore prefix. A leading
underscore has no meaning in Go.

## Comments & Doc Comments

Comments are terse and used sparingly — the code documents itself through names, types,
and organization. The one exception is documentation-generation comments: godoc on
exported types, funcs, and the package, each beginning with the identifier's name;
unexported helpers get none. Beyond godoc, comment only for TODOs, non-obvious
workarounds, or disabled code — never to restate the signature.

```go
// Good — exported, starts with the name, says what's not obvious from the signature
// NewRootCmd builds the command tree; callers Execute it.
func NewRootCmd() *cobra.Command { ... }

// Bad — restates the signature
// NewRootCmd returns a new root command.
func NewRootCmd() *cobra.Command { ... }
```

## Testing

Tests live beside the code as `*_test.go`; run them with `go test -race ./...`.
Table-driven tests are the idiom — one slice of cases, a `t.Run(tt.name, …)` loop,
strict assertions against specific expected values (a test that can't fail uncovers
nothing). Prefer the standard library `testing` package; reach for a helper only
when it earns its keep. Mock the boundaries your code talks to (network, filesystem,
clock) and leave the code under test real; a stateful service is not a mock boundary
— stand up a real ephemeral instance instead.

```go
func TestExitCode(t *testing.T) {
	tests := []struct {
		name string
		err  error
		want int
	}{
		{"nil", nil, 0},
		{"not found", ErrNotFound, 3},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := ExitCode(tt.err); got != tt.want {
				t.Errorf("ExitCode() = %d, want %d", got, tt.want)
			}
		})
	}
}
```
