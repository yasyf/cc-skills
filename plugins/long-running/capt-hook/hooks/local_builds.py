from __future__ import annotations

from captain_hook import Allow, BaseHookEvent, Block, Call, Event, HookResult, Input, Tool, on
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState

COMMANDS = frozenset({"tsc", "pytest", "jest", "vitest", "eslint", "mypy", "pyright", "golangci-lint", "xcodebuild"})
VERBS = {
    "go": frozenset({"build", "vet", "test"}),
    "cargo": frozenset({"build", "check", "test", "clippy", "nextest", "bench"}),
    "just": frozenset({"vet", "build", "test", "lint", "check", "typecheck"}),
    "buck2": frozenset({"build", "test"}),
    "swift": frozenset({"build", "test"}),
    "ruff": frozenset({"check"}),
    "biome": frozenset({"check", "lint", "ci"}),
    "ty": frozenset({"check"}),
    "capt-hook": frozenset({"test", "lint"}),
}
MAKE_TARGETS = frozenset({"all", "build", "test", "check", "lint", "vet", "typecheck"})
SCRIPTS = frozenset({"tsc", "build", "test", "lint", "typecheck"})
VALUE_FLAGS = {
    "go": frozenset({"-C", "-run", "-tags", "-timeout", "-count", "-p"}),
    "cargo": frozenset({"--manifest-path", "--config", "-Z", "--color", "-p", "--package"}),
    "just": frozenset({"-f", "--justfile", "-d", "--working-directory"}),
    "make": frozenset({"-C", "-f", "--file", "--directory"}),
    "npm": frozenset({"--prefix", "-w", "--workspace"}),
    "pnpm": frozenset({"--filter", "-F", "-C", "--dir"}),
    "yarn": frozenset({"--cwd"}),
    "bun": frozenset({"--cwd"}),
    "npx": frozenset({"-p", "--package"}),
    "uv": frozenset({"--project", "--directory", "--with", "--python", "-p"}),
    "uvx": frozenset({"--from", "--with", "--python", "-p"}),
}
LAUNCHERS = frozenset({"npx", "bunx", "uvx"})
INFO_FLAGS = frozenset({"--version", "-v", "-V", "--help", "-h"})
VERBOSE_V = frozenset({"pytest"})
RELEASE_CLI = "tools/ci"
LANE_ENV = {"CLAUDE_LONG_RUNNING_LANE": "lane-b", "CLAUDE_LONG_RUNNING_DRIVE": "leftovers-oct-18"}
ROOT = [CompactionState(active=True)]
REFUSAL = (
    "No local compile, test, vet, typecheck, or lint in a drive, in any lane or the root and for any reason, a red CI step included"
    " (owner, Oct 13; F341, F348): CI grades. Commit and run `ccx vcs ship`, then read the failing step's log."
    " A Go golden's single `go test -update` run is the one exception."
)


def words(name: str, args: tuple[str, ...]) -> list[str]:
    valued = VALUE_FLAGS.get(name, frozenset())
    return [arg for before, arg in zip(("", *args), args, strict=False) if before not in valued and not arg.startswith(("-", "+"))]


def informational(name: str, args: tuple[str, ...]) -> bool:
    flags = INFO_FLAGS - {"-v"} if name in VERBOSE_V else INFO_FLAGS
    return bool(args) and flags.issuperset(args)


def golden(args: tuple[str, ...]) -> bool:
    return any(arg == "-update" or arg.startswith("-update=") for arg in args)


def after(args: tuple[str, ...], tool: str) -> tuple[str, ...]:
    return args[args.index(tool) + 1 :]


def runs(name: str, args: tuple[str, ...]) -> bool:
    match name, words(name, args):
        case "make", targets:
            return not informational(name, args) and (not targets or targets[0] in MAKE_TARGETS)
        case tool, _ if tool in COMMANDS:
            return not informational(name, args)
        case "go", ["test", *_]:
            return not golden(args)
        case tool, [verb, *_] if verb in VERBS.get(tool, ()):
            return True
        case launcher, [tool, *_] if launcher in LAUNCHERS:
            return runs(tool, after(args, tool))
        case ("pnpm", ["exec", tool, *_]) | ("yarn", ["dlx", tool, *_]) | ("uv", ["run", tool, *_]):
            return runs(tool, after(args, tool))
        case ("python" | "python3"), _ if len(args) > 1 and args[0] == "-m":
            return runs(args[1], args[2:])
        case "yarn", ["workspace", _, script, *_]:
            return script.split(":")[0] in SCRIPTS
        case ("yarn" | "npm" | "pnpm" | "bun"), ["run", script, *_] | [script, *_]:
            return script.split(":")[0] in SCRIPTS
    return False


def builds(call: Call) -> bool:
    if call.name == "ci" and words(call.name, call.args)[:2] == ["release", "plan"]:
        return call.command.executable.endswith(RELEASE_CLI)
    return runs(call.name, call.args)


def in_drive(evt: BaseHookEvent) -> bool:
    return bool(reqenv.getenv("CLAUDE_LONG_RUNNING_LANE") or reqenv.getenv("CLAUDE_LONG_RUNNING_DRIVE")) or CompactionState.load(evt).active


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        Input(command="go build ./...", env=LANE_ENV): Block(pattern=r"^No local compile, test, vet, typecheck, or lint in a drive[\s\S]*ccx vcs ship"),
        Input(command="go build ./...", state=ROOT): Block(),
        Input(command="go vet ./internal/sandsql/...", env={"CLAUDE_LONG_RUNNING_LANE": "lane-b"}): Block(),
        Input(command="cd go/sand && go test ./internal/vm/...", env=LANE_ENV): Block(),
        Input(command="go -C go/sand test ./...", env=LANE_ENV): Block(),
        Input(command="cargo nextest run -p sandsql-api", env=LANE_ENV): Block(),
        Input(command="cargo +nightly clippy --all-targets", env=LANE_ENV): Block(),
        Input(command="cargo --locked check", env=LANE_ENV): Block(),
        Input(command="cargo --manifest-path rust/Cargo.toml test", env=LANE_ENV): Block(),
        Input(command="bash -c 'cargo build --release'", env=LANE_ENV): Block(),
        Input(command="tsc --noEmit -p api", env=LANE_ENV): Block(),
        Input(command="yarn tsc:all", env=LANE_ENV): Block(),
        Input(command="yarn tsc:common", env=LANE_ENV): Block(),
        Input(command="npm run build", env=LANE_ENV): Block(),
        Input(command="npm run lint", env=LANE_ENV): Block(),
        Input(command="pnpm -r test", env=LANE_ENV): Block(),
        Input(command="pnpm --filter api build", env=LANE_ENV): Block(),
        Input(command="yarn workspace api test", env=LANE_ENV): Block(),
        Input(command="bun run test:unit", env=LANE_ENV): Block(),
        Input(command="just vet", env=LANE_ENV): Block(),
        Input(command="buck2 build //go/...", env=LANE_ENV): Block(),
        Input(command="make -j8 all", env=LANE_ENV): Block(),
        Input(command="make", env=LANE_ENV): Block(),
        Input(command="swift build -c release", env=LANE_ENV): Block(),
        Input(command="tools/ci release plan --target sand", env=LANE_ENV): Block(),
        Input(command="pytest tests/", env=LANE_ENV): Block(),
        Input(command="python -m pytest tests/ -q", env=LANE_ENV): Block(),
        Input(command="uv run --project plugins/long-running pytest plugins/long-running/tests -q", env=LANE_ENV): Block(),
        Input(command="uvx --isolated capt-hook test .claude/hooks/x.py", env=LANE_ENV): Block(),
        Input(command="uvx --isolated capt-hook lint .claude/hooks/x.py", env=LANE_ENV): Block(),
        Input(command="jest --ci", env=LANE_ENV): Block(),
        Input(command="vitest run", env=LANE_ENV): Block(),
        Input(command="npx tsc --noEmit", env=LANE_ENV): Block(),
        Input(command="bunx biome check api/src common/src", env=LANE_ENV): Block(),
        Input(command="biome check api/src", env=LANE_ENV): Block(),
        Input(command="pnpm exec eslint .", env=LANE_ENV): Block(),
        Input(command="uv run ruff check", env=LANE_ENV): Block(),
        Input(command="ruff check --fix .", env=LANE_ENV): Block(),
        Input(command="golangci-lint run ./...", env=LANE_ENV): Block(),
        Input(command="python3 -m mypy scripts", env=LANE_ENV): Block(),
        Input(command="go test ./internal/iris/... -run TestGolden -update", env=LANE_ENV): Allow(),
        Input(command="go test ./internal/parser -update=true", env=LANE_ENV): Allow(),
        Input(command="go build ./...", env={}): Allow(),
        Input(command="pytest tests/", env={}): Allow(),
        Input(command="go env GOFLAGS", env=LANE_ENV): Allow(),
        Input(command="go run ./cmd/sand version", env=LANE_ENV): Allow(),
        Input(command="git status", env=LANE_ENV): Allow(),
        Input(command="cargo fmt --check", env=LANE_ENV): Allow(),
        Input(command="ruff format scripts", env=LANE_ENV): Allow(),
        Input(command="biome format --write api/src", env=LANE_ENV): Allow(),
        Input(command="npx prettier --check .", env=LANE_ENV): Allow(),
        Input(command="yarn install --immutable", env=LANE_ENV): Allow(),
        Input(command="make docs", env=LANE_ENV): Allow(),
        Input(command="make --version", env=LANE_ENV): Allow(),
        Input(command="just --list", env=LANE_ENV): Allow(),
        Input(command="buck2 targets //go/...", env=LANE_ENV): Allow(),
        Input(command="tools/ci release status", env=LANE_ENV): Allow(),
        Input(command="ci release plan", env=LANE_ENV): Allow(),
        Input(command="python -m http.server", env=LANE_ENV): Allow(),
        Input(command="python scripts/pytest_report.py", env=LANE_ENV): Allow(),
        Input(command="python3 -m py_compile scripts/x.py", env=LANE_ENV): Allow(),
        Input(command="tsc --version", env=LANE_ENV): Allow(),
        Input(command="pytest -V", env=LANE_ENV): Allow(),
        Input(command="echo go build ./...", env=LANE_ENV): Allow(),
        Input(command="ccx vcs ship -m 'go: build fix'", env=LANE_ENV): Allow(),
        Input(command="gh run view 123 --log-failed", env=LANE_ENV): Allow(),
    },
)
def no_local_builds_in_a_drive(evt: BaseHookEvent) -> HookResult | None:
    if not any(builds(call) for call in evt.command.calls()) or not in_drive(evt):
        return None
    return evt.block(REFUSAL)
