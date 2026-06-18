# Go Stack: Rationale and Knobs

Every choice in the go layer, why it won, and what to adjust when the project deviates.
Worked example throughout: project `demo-proj`, module `github.com/yasyf/demo-proj`, binary `demo-proj`.

## Stack at a Glance

| Choice | Why | Rejected alternative |
|---|---|---|
| `cmd/<name>/` + `internal/` layout | The Go-team layout for a CLI: one `main` per binary, everything else compiler-private under `internal/`; free to refactor without breaking importers | `pkg/` — signals a public API a CLI doesn't have; flat root — no place for `main` + libraries to coexist cleanly |
| cobra | The de-facto CLI framework (kubectl, gh, docker): nested commands, flags, shell completion, `ExecuteContext` for cancellation | urfave/cli — smaller ecosystem; stdlib `flag` — manual subcommand wiring, no completion |
| `log/slog` (stdlib) | Structured logging in the standard library since Go 1.21 — text for humans, JSON for machines, zero deps | zerolog/zap — extra dep, only worth it for hot-path microsecond logging |
| golangci-lint v2 + gofumpt | One meta-linter runs the whole suite (errcheck, govet, staticcheck, gosec, revive…); gofumpt is the stricter-gofmt standard. CI and the commit hook own it | bare `go vet` — misses most of what golangci-lint catches; gofmt — gofumpt is a strict superset |
| stdlib `testing`, table-driven | The Go idiom: one slice of cases, `t.Run` subtests, `-race`. No assertion-library ceremony | testify — fine for big suites, but a dependency the scaffold doesn't need on day 1 |
| goreleaser → Homebrew cask | One config builds the matrix, stamps the version, cuts the release, and pushes a cask to the shared `yasyf/homebrew-tap`. See `reference/go-ci-and-release.md` | hand-rolled release matrix — re-implements goreleaser badly; per-repo taps — fragment installs across N tap repos |
| Taskfile (go-task) | YAML task runner, checksum-based deps, cross-platform. `task build/test/lint/ci` | Makefile — tab quirks, weaker deps; just — a command runner, not a build tool |
| version via `-ldflags` + `ReadBuildInfo` fallback | The build stamps `internal/version.Version`; a `go install`ed binary falls back to module build info so `--version` always says something true | a committed version constant — drifts; goes stale the moment you forget to bump it |

## Project Layout

```
demo-proj/
├── cmd/demo-proj/main.go   # the one main package: wire logging, signals, run the root cmd
├── internal/
│   ├── cli/                # cobra command tree (root.go + one file per command)
│   ├── version/            # Version/Commit/Date, stamped at build time
│   └── log/                # slog setup
├── Taskfile.yml            # build / test / lint / ci
├── .goreleaser.yaml        # release pipeline (feature `release`)
└── go.mod                  # module github.com/<user>/demo-proj
```

`internal/` is compiler-enforced: nothing outside this module can import it, so the
package boundaries are yours to move freely. Reach for `pkg/` only when you
deliberately publish a library API — a CLI rarely does. A library-only repo drops
`cmd/` and exposes packages at the module root instead (see SKILL.md escape hatch).

## Starter Anatomy

The scaffold ships a **minimal skeleton**, not a product (per the skill's scope rule):

- `cmd/demo-proj/main.go` — installs the slog logger, builds a cancellable context with
  `signal.NotifyContext`, runs `cli.NewRootCmd().ExecuteContext(ctx)`, and on error prints
  to stderr and exits 1. Grow the error handling into the exit-code taxonomy below.
- `internal/cli/root.go` — the cobra root: `SilenceUsage`/`SilenceErrors` true (so cobra
  doesn't print usage on every runtime error), `Version: version.String()`, a one-line version
  template, and `AddCommand(newHelloCmd())`.
- `internal/cli/hello.go` — the single starter command. A placeholder; replace it with real
  commands. Building the product begins after the repo is scaffolded.
- `internal/cli/hello_test.go` — one table-driven smoke test driving the root command with a
  captured buffer.
- `internal/version/version.go` — `Version/Commit/Date` vars + `String()` with a
  `debug.ReadBuildInfo()` fallback.
- `internal/log/log.go` — `slog` text/JSON handler chosen by `LOG_FORMAT`, level by `LOG_LEVEL`.

## Version Stamping

The version is **never a committed constant**. The flow:

1. `internal/version.Version` defaults to `"dev"` on `main`.
2. `task build` and `.goreleaser.yaml` pass `-ldflags "-X .../internal/version.Version=<v>"`
   to stamp the real version (a git describe locally; the tag in a release).
3. `String()` returns the stamped value, or — for a `go install`ed binary with no ldflags —
   falls back to `runtime/debug.ReadBuildInfo().Main.Version` so `--version` is still truthful.

Raising the supported Go version is a deliberate change: bump the `go` directive in `go.mod`
(CI reads it via `go-version-file: go.mod`) and note it in CHANGELOG.md.

## Error Handling → Exit Codes

The starter keeps error handling minimal: `main` prints `name: message` to stderr and exits 1.
The house pattern, to grow into as the second error type appears (documented in STYLEGUIDE.md):

- Define typed errors in `internal/cli` (`UsageError`, a not-found, a conflict…) and a pair
  `ExitCode(err) int` / `Label(err) string`.
- `main` calls them: `fmt.Fprintf(os.Stderr, "%s: %s\n", cli.Label(err), err); os.Exit(cli.ExitCode(err))`.
- Stdout carries machine-readable output (often JSON for agent consumers); stderr carries the
  one-line `label: message`; the exit code encodes the category so scripts can branch.

Add it when you have a reason (a second error category), not preemptively — the skeleton stays a
skeleton until real commands need the distinction.

## Logging

`internal/log.Setup()` installs the default `slog` logger. Diagnostics go through `slog.Info` /
`slog.Debug` with key-value attrs — never `fmt.Println`. `LOG_FORMAT=json` switches to the JSON
handler (the right default for a server or an agent-consumed tool); the text handler is the
human default. `LOG_LEVEL` sets the floor. Reach for zerolog/zap only if profiling shows logging
on a hot path; for everything else slog is enough and dependency-free.

## Testing

Tests sit beside the code as `*_test.go`; run them with `task test` (`go test -race -count=1 ./...`).
Table-driven is the idiom: a slice of cases, a `t.Run(tt.name, …)` loop, and strict assertions
against specific expected values. Use external test packages (`package cli_test`) for black-box
tests of the exported API; white-box (`package cli`) only when a test must reach unexported
internals. Mock boundaries (network, filesystem, clock) and leave the code under test real; for a
stateful service, stand up a real ephemeral instance rather than mocking the driver.

## Mechanical Linting

`golangci-lint` (v2) runs both the formatters (`gofumpt`, `goimports`) and the linter suite from
one `.golangci.yml`. CI runs it and the prek commit hook runs it on every commit
(`uvx prek install` to activate) — so it is **never** run by hand, and the `go` capt-hook pack
blocks manual `gofumpt`/`golangci-lint` invocations. The enabled linter set is deliberately
broad-but-quiet (`errcheck`, `govet`, `staticcheck`, `gosec`, `revive`, `errorlint`,
`contextcheck`, `prealloc`, `durationcheck`, `ineffassign`, `unused`); tune it in `.golangci.yml`,
not by sprinkling `//nolint`.
