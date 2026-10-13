from __future__ import annotations

import subprocess
import sys

from captain_hook import Allow, Annotated, BaseHookEvent, Block, Call, Event, HookResult, Input, Tool, on

LOAD_FRACTION = 0.75
LOAD_PROBE = "import os; print(os.getloadavg()[0], os.cpu_count())"
VERBS = {
    "go": frozenset({"build", "vet", "test", "generate"}),
    "cargo": frozenset({"build", "check", "test", "clippy", "nextest"}),
    "just": frozenset({"vet", "build", "test"}),
    "buck2": frozenset({"build", "test"}),
}
SCRIPTS = frozenset({"tsc", "build", "test"})
EXEC_TOOLS = frozenset({"tsc", "jest", "vitest"})
VALUE_FLAGS = {
    "go": frozenset({"-C"}),
    "cargo": frozenset({"--manifest-path", "--config", "-Z", "--color"}),
    "just": frozenset({"-f", "--justfile", "-d", "--working-directory"}),
    "npm": frozenset({"--prefix", "-w", "--workspace"}),
    "pnpm": frozenset({"--filter", "-F", "-C", "--dir"}),
    "yarn": frozenset({"--cwd"}),
    "bun": frozenset({"--cwd"}),
    "npx": frozenset({"-p", "--package"}),
    "uv": frozenset({"--project", "--directory", "--with", "--python", "-p"}),
}
INFO_FLAGS = frozenset({"--version", "-v", "-V", "--help", "-h"})
VERBOSE_V = frozenset({"pytest"})
RELEASE_CLI = "tools/ci"

PROBE = f"{sys.executable} -c"
LOADED = {PROBE: "30.4 32"}
AT_LIMIT = {PROBE: "24.0 32"}
UNDER_LIMIT = {PROBE: "23.9 32"}
IDLE = {PROBE: "6.2 32"}


def words(call: Call) -> list[str]:
    valued = VALUE_FLAGS.get(call.name, frozenset())
    return [arg for before, arg in zip(("", *call.args), call.args) if before not in valued and not arg.startswith(("-", "+"))]


def informational(call: Call) -> bool:
    flags = INFO_FLAGS - {"-v"} if call.name in VERBOSE_V else INFO_FLAGS
    return bool(call.args) and flags.issuperset(call.args)


def builds(call: Call) -> bool:
    match call.name, words(call):
        case ("make" | "tsc" | "pytest" | "jest" | "vitest"), _:
            return not informational(call)
        case ("go" | "cargo" | "just" | "buck2") as tool, [verb, *_]:
            return verb in VERBS[tool]
        case (("npx" | "bunx"), [tool, *_]) | ("pnpm", ["exec", tool, *_]) | ("yarn", ["dlx", tool, *_]):
            return tool in EXEC_TOOLS
        case "yarn", ["workspace", _, script, *_]:
            return script.split(":")[0] in SCRIPTS
        case ("yarn" | "npm" | "pnpm" | "bun"), ["run", script, *_] | [script, *_]:
            return script.split(":")[0] in SCRIPTS
        case ("python" | "python3"), ["pytest", *_]:
            return call.args[:2] == ("-m", "pytest")
        case "uv", ["run", "pytest", *_]:
            return True
        case "ci", ["release", "plan", *_]:
            return call.command.executable.endswith(RELEASE_CLI)
    return False


def machine_load() -> tuple[float, int]:
    load, cores = subprocess.run([sys.executable, "-c", LOAD_PROBE], capture_output=True, text=True, check=True).stdout.split()
    return float(load), int(cores)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    skip_if=[Annotated("raw")],
    tests={
        Input(command="go build ./...", commands=LOADED): Block(pattern=r"load is 30\.4 on 32 CPUs[\s\S]*limit of 24 [\s\S]*ccx vcs ship"),
        Input(command="cd go/sand && go test ./internal/vm/...", commands=LOADED): Block(),
        Input(command="go vet ./... && go generate ./...", commands=LOADED): Block(),
        Input(command="cargo nextest run -p sandsql-api", commands=LOADED): Block(),
        Input(command="cargo +nightly clippy --all-targets", commands=LOADED): Block(),
        Input(command="cargo --locked check", commands=LOADED): Block(),
        Input(command="tsc --noEmit -p api", commands=LOADED): Block(),
        Input(command="yarn tsc:all", commands=LOADED): Block(),
        Input(command="npm run build", commands=LOADED): Block(),
        Input(command="pnpm -r test", commands=LOADED): Block(),
        Input(command="bun run test:unit", commands=LOADED): Block(),
        Input(command="just vet", commands=LOADED): Block(),
        Input(command="buck2 build //go/...", commands=LOADED): Block(),
        Input(command="make -j8 all", commands=LOADED): Block(),
        Input(command="tools/ci release plan --target sand", commands=LOADED): Block(),
        Input(command="bash -c 'cargo build --release'", commands=LOADED): Block(),
        Input(command="go -C go/sand test ./...", commands=LOADED): Block(),
        Input(command="cargo --manifest-path rust/Cargo.toml test", commands=LOADED): Block(),
        Input(command="cargo --config net.offline=true build", commands=LOADED): Block(),
        Input(command="cargo -Z unstable-options check", commands=LOADED): Block(),
        Input(command="cargo --color always clippy", commands=LOADED): Block(),
        Input(command="npm --prefix web run build", commands=LOADED): Block(),
        Input(command="npm -w api test", commands=LOADED): Block(),
        Input(command="pnpm --filter api build", commands=LOADED): Block(),
        Input(command="pnpm -C packages/web test", commands=LOADED): Block(),
        Input(command="yarn --cwd api tsc", commands=LOADED): Block(),
        Input(command="yarn workspace api test", commands=LOADED): Block(),
        Input(command="just -f ci/justfile test", commands=LOADED): Block(),
        Input(command="just -d go/sand vet", commands=LOADED): Block(),
        Input(command="pytest tests/", commands=LOADED): Block(),
        Input(command="pytest -v", commands=LOADED): Block(),
        Input(command="python -m pytest tests/ -q", commands=LOADED): Block(),
        Input(command="uv run pytest -q", commands=LOADED): Block(),
        Input(command="uv run --project plugins/long-running pytest plugins/long-running/tests -q", commands=LOADED): Block(),
        Input(command="jest --ci", commands=LOADED): Block(),
        Input(command="vitest run", commands=LOADED): Block(),
        Input(command="npx tsc --noEmit", commands=LOADED): Block(),
        Input(command="bunx vitest run", commands=LOADED): Block(),
        Input(command="pnpm exec jest", commands=LOADED): Block(),
        Input(command="yarn dlx tsc -p .", commands=LOADED): Block(),
        Input(command="go build ./...", commands=AT_LIMIT): Block(pattern=r"load is 24\.0 on 32 CPUs"),
        Input(command="go build ./...", commands=UNDER_LIMIT): Allow(),
        Input(command="go build ./...", commands=IDLE): Allow(),
        Input(command="go env GOFLAGS", commands=LOADED): Allow(),
        Input(command="git status", commands=LOADED): Allow(),
        Input(command="cargo fmt --check", commands=LOADED): Allow(),
        Input(command="yarn install --immutable", commands=LOADED): Allow(),
        Input(command="npm run lint", commands=LOADED): Allow(),
        Input(command="just --list", commands=LOADED): Allow(),
        Input(command="buck2 targets //go/...", commands=LOADED): Allow(),
        Input(command="tools/ci release status", commands=LOADED): Allow(),
        Input(command="ci release plan", commands=LOADED): Allow(),
        Input(command="go -C test env", commands=LOADED): Allow(),
        Input(command="cargo --manifest-path test/Cargo.toml fmt", commands=LOADED): Allow(),
        Input(command="cargo --color test fmt", commands=LOADED): Allow(),
        Input(command="npm --prefix test install", commands=LOADED): Allow(),
        Input(command="npm --workspace build install", commands=LOADED): Allow(),
        Input(command="pnpm -F test install", commands=LOADED): Allow(),
        Input(command="pnpm --dir build install", commands=LOADED): Allow(),
        Input(command="yarn --cwd test install", commands=LOADED): Allow(),
        Input(command="yarn workspace test add lodash", commands=LOADED): Allow(),
        Input(command="just --justfile build.just --list", commands=LOADED): Allow(),
        Input(command="just --working-directory test fmt", commands=LOADED): Allow(),
        Input(command="python -m http.server", commands=LOADED): Allow(),
        Input(command="python scripts/pytest_report.py", commands=LOADED): Allow(),
        Input(command="uv run ruff check", commands=LOADED): Allow(),
        Input(command="npx prettier --check .", commands=LOADED): Allow(),
        Input(command="bunx biome check", commands=LOADED): Allow(),
        Input(command="pnpm exec eslint .", commands=LOADED): Allow(),
        Input(command="yarn dlx create-vite app", commands=LOADED): Allow(),
        Input(command="tsc --version", commands=LOADED): Allow(),
        Input(command="tsc -v", commands=LOADED): Allow(),
        Input(command="make --version", commands=LOADED): Allow(),
        Input(command="make -h", commands=LOADED): Allow(),
        Input(command="pytest -V", commands=LOADED): Allow(),
        Input(command="jest --help", commands=LOADED): Allow(),
        Input(command="echo go build ./...", commands=LOADED): Allow(),
        Input(command="go build ./...  # ccx:raw", commands=LOADED): Allow(),
    },
)
def no_local_builds_under_load(evt: BaseHookEvent) -> HookResult | None:
    if not any(builds(call) for call in evt.command.calls()):
        return None
    load, cores = machine_load()
    limit = LOAD_FRACTION * cores
    if load < limit:
        return None
    return evt.block(
        f"The 1-minute load is {load:.1f} on {cores} CPUs, at or over the limit of {limit:g} for a local compile, typecheck, or test."
        " Commit and run `ccx vcs ship`, then read the CI log; `# ccx:raw` runs this command as written."
    )
