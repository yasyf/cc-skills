# {{PROJECT_NAME}} Development Guide

{{#FEATURE_RELEASE}}{{DESCRIPTION}} Distributed via Homebrew: `brew install {{GITHUB_USER}}/tap/{{PROJECT_NAME}}`.{{/FEATURE_RELEASE}}{{^FEATURE_RELEASE}}{{DESCRIPTION}}{{/FEATURE_RELEASE}}

## Repository Structure

```
{{PROJECT_NAME}}/
├── cmd/{{PROJECT_NAME}}/   # main package — the CLI entry point
{{#FEATURE_DAEMONKIT}}
├── cmd/{{PROJECT_NAME}}d/  # the daemon binary (proc.CloseInheritedFDs is main's first call)
{{/FEATURE_DAEMONKIT}}
├── internal/
│   ├── cli/               # cobra command tree — TODO(bootstrap): name the commands
{{#FEATURE_DAEMONKIT}}
│   ├── daemon/            # daemonkit Runtime, persistent wire v1, launchd service
{{/FEATURE_DAEMONKIT}}
│   ├── version/           # build version, stamped via -ldflags
│   └── log/               # slog setup
{{#FEATURE_DAEMONKIT}}
├── scripts/test.sh        # RLIMIT_NPROC fork-bomb harness — the ONLY way to run the tests
{{/FEATURE_DAEMONKIT}}
├── .github/               # GitHub Actions workflows
├── AGENTS.md              # This file — shared conventions
└── README.md              # Project overview
```
{{#FEATURE_DAEMONKIT}}

## Daemon (daemonkit)

`cmd/{{PROJECT_NAME}}d` is a detached daemon built on [daemonkit](https://github.com/yasyf/daemonkit). `proc.CloseInheritedFDs()` is main's literal first call. One `daemon.Runtime` owns listener takeover, admission, persistent wire-v1 sessions, and ordered shutdown; `wire.LifecyclePeer` and `trust.Policy` provide the exact lifecycle and typed same-UID trust boundary. The version is stamped via `-ldflags -X {{MODULE_PATH}}/internal/daemon.buildVersion=vX.Y.Z`, dev builds fall back to `version.DevString`, and `{{PROJECT_NAME}}d service install|uninstall|status` manages the typed launchd policy.

**Never run bare `go test`** — `scripts/test.sh ./...` caps `RLIMIT_NPROC` so a `proc.Spawn` path that execs a test binary hits `EAGAIN` instead of fork-bombing the machine. CI routes through it too.

The scaffold pins the exact daemonkit revision it was integration-tested against. For simultaneous daemonkit development, point an untracked `go.work` at your checkout instead of committing a `replace`:

```bash
go work init . /path/to/daemonkit   # untracked; never commit it
```
{{/FEATURE_DAEMONKIT}}
