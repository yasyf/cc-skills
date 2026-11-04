from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Call, Event, HookResult, Input, Tool, Warn, WorkflowState, on
from captain_hook.util.builds import LOAD_PER_CORE, is_local_build
from pydantic import BaseModel

from .compaction_handoff import COMMUNICATOR_LANE, ROOT_LANE, Bound
from .lane_landing import lane_name, program, run_quietly

PUSHES = frozenset({("git", "push"), ("ccx", "vcs", "push"), ("ccx", "vcs", "ship"), ("gt", "submit")})
NOT_A_PUSH = frozenset({"--no-push", "--dry-run", "-n", "--delete", "-d"})
GO_KINDS = ("build", "vet")
TYPECHECK = "typecheck"
TYPECHECKERS = frozenset({"tsc", "tsgo"})
RUNNERS = frozenset({"yarn", "npm", "pnpm", "bun"})
LAUNCHERS = frozenset({"npx", "bunx"})
GO_VALUE_FLAGS = frozenset({"-o", "-tags", "-p", "-mod", "-modfile", "-overlay", "-pkgdir", "-gcflags", "-ldflags", "-asmflags", "-toolexec", "-exec", "-vettool"})
PROJECT_FLAGS = frozenset({"-p", "--project"})
REMOTE_VET = "remote-vet.py"
REMOTE_VET_VALUE_FLAGS = frozenset({"--config", "--lane", "--sprite", "--base", "--timeout"})
SEPARATORS = frozenset({"&&", "||", ";", "|"})
TS_SUFFIXES = frozenset({".ts", ".tsx", ".mts", ".cts"})
NOT_PACKAGES = frozenset({"testdata", "vendor"})
NOT_LANES = frozenset({None, "", ROOT_LANE, COMMUNICATOR_LANE})
FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "vet-push"
LANE_ENV = {"CLAUDE_LONG_RUNNING_LANE": "lane-a"}
SATURATED = LANE_ENV | {LOAD_PER_CORE: "0"}
HEALTHY = LANE_ENV | {LOAD_PER_CORE: "1000000"}
SHIP = "ccx vcs ship -m 'sand: 🐛 fix the vm'"
REPO = {"git rev-parse --show-toplevel": f"{FIXTURE}\n", "git config --bool --default false core.sparseCheckout": "false\n", "go env GOROOT": "/usr/local/go\n"}
GO_DIFF = REPO | {"git diff": "go/sand/internal/vm/vm.go\ndocs/vm.md\n"}
TS_DIFF = REPO | {"git diff": "api/src/a.ts\n"}
OWN_TS_DIFF = REPO | {"git diff": "tools/own/src/a.ts\n"}
REFUSAL = (
    "A lane runs `go build` and `go vet` over the Go packages it touched, and the typecheck over its TypeScript, after its last edit and before it pushes; a run the host load gate refused counts, and CI still runs every test."
    " Run these, then push again:\n```\n{commands}\n```"
)
UNREAD = "Warning, nothing was refused: {why}, so this push goes ahead unchecked. Run `go build`, `go vet` and the typecheck over what it touched where they can run."


class Check(BaseModel):
    kind: str
    root: str
    recursive: bool
    at: float


class CompileChecks(WorkflowState):
    checks: list[Check] = []


def fresh(*kinds: str, root: str, recursive: bool = True, at: float = 4e9) -> CompileChecks:
    return CompileChecks(checks=[Check(kind=kind, root=str(FIXTURE / root), recursive=recursive, at=at) for kind in kinds])


def in_lane(evt: BaseHookEvent) -> bool:
    return lane_name(evt) not in NOT_LANES


def pushes(call: Call) -> bool:
    return any(call.verb_argv[: len(verb)] == verb for verb in PUSHES) and not any(arg.split("=", 1)[0] in NOT_A_PUSH for arg in call.args)


def go_checks(base: Path, args: tuple[str, ...]) -> list[tuple[str, Path, bool]]:
    if args[:1] == ("-C",) and len(args) > 1:
        return go_checks(base / args[1], args[2:])
    if not args or args[0] not in GO_KINDS:
        return []
    words = [arg for before, arg in zip(args, args[1:], strict=False) if before not in GO_VALUE_FLAGS and not arg.startswith("-")]
    checks = []
    for pattern in words or ["."]:
        if pattern == "..." or pattern.endswith("/..."):
            checks.append((args[0], (base / pattern.removesuffix("...")).resolve(), True))
        elif pattern.endswith(".go"):
            checks.append((args[0], (base / pattern).resolve().parent, False))
        elif pattern.startswith((".", "/")):
            checks.append((args[0], (base / pattern).resolve(), False))
    return checks


def projects(base: Path, scripts: dict[str, str], words: list[str], seen: frozenset[str] = frozenset()) -> set[Path]:
    found, checking, named = set(), False, False
    for before, word in zip(["", *words], [*words, ";"], strict=False):
        if word in SEPARATORS:
            if checking and not named:
                found.add(base.resolve())
            checking = named = False
        elif Path(word).name in TYPECHECKERS:
            checking = True
        elif checking and before in PROJECT_FLAGS:
            named = True
            path = (base / word).resolve()
            found.add(path.parent if path.suffix == ".json" else path)
        elif word in scripts and word not in seen:
            found |= projects(base, scripts, scripts[word].split(), seen | {word})
    return found


def scripts_at(base: Path) -> tuple[Path, dict[str, str]]:
    manifest = next((folder / "package.json" for folder in (base, *base.parents) if (folder / "package.json").is_file()), None)
    return (manifest.parent, json.loads(manifest.read_text()).get("scripts", {})) if manifest else (base, {})


def typecheck_checks(base: Path, call: Call) -> list[tuple[str, Path, bool]]:
    words = [arg for arg in call.args if arg != "run"]
    if call.name in TYPECHECKERS or call.name in LAUNCHERS and words[:1] and words[0] in TYPECHECKERS:
        dirs = projects(base, {}, [call.name, *call.args])
    elif call.name in RUNNERS and words[:1] and words[0] in TYPECHECKERS:
        dirs = projects(base, {}, list(call.args))
    elif call.name in RUNNERS and words[:1]:
        dirs = projects(*scripts_at(base), words=words[:1])
    else:
        return []
    return [(TYPECHECK, path, False) for path in dirs]


def remote_vet_checks(base: Path, args: tuple[str, ...]) -> list[tuple[str, Path, bool]]:
    worktree = next((arg for before, arg in zip(("", *args), args, strict=False) if before not in REMOTE_VET_VALUE_FLAGS and not arg.startswith("-")), ".")
    return [(kind, (base / worktree).resolve(), True) for kind in GO_KINDS]


def compiles(evt: BaseHookEvent, call: Call) -> list[tuple[str, Path, bool]]:
    base = Path(evt.cwd, call.cwd or ".")
    if call.name == "go":
        return go_checks(base, call.args)
    if (run := program((call.name, *call.args))) and Path(run[0]).name == REMOTE_VET:
        return remote_vet_checks(base, run[1:])
    return typecheck_checks(base, call)


@on(
    Event.PostToolUse | Event.PostToolUseFailure,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command="go -C go/sand vet ./...", env=LANE_ENV, cwd=str(FIXTURE)): Allow(),
        Input(command="cd go/sand && go build ./internal/vm", env=LANE_ENV, cwd=str(FIXTURE)): Allow(),
        Input(command="yarn tsc:api", env=LANE_ENV, cwd=str(FIXTURE)): Allow(),
        Input(command="python3 ~/.claude/plugins/long-running/skills/long-running/scripts/remote-vet.py --config /s/lr.json --lane lane-a .", env=LANE_ENV, cwd=str(FIXTURE)): Allow(),
        Input(command="git status", env=LANE_ENV, cwd=str(FIXTURE)): Allow(),
    },
)
def note_compile_checks(evt: BaseHookEvent) -> None:
    if in_lane(evt) and (found := [check for call in evt.command.calls() for check in compiles(evt, call)]):
        record(evt, found)


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command="go -C go/sand vet ./...", env=SATURATED, cwd=str(FIXTURE)): Allow(),
        Input(command="go -C go/sand vet ./...", env=HEALTHY, cwd=str(FIXTURE)): Allow(),
        Input(command="yarn tsc:api", env=SATURATED, cwd=str(FIXTURE)): Allow(),
    },
)
def note_attempted_checks(evt: BaseHookEvent) -> None:
    gated = [call for call in evt.command.calls() if call.name == "go" and is_local_build(call)]
    if not in_lane(evt) or not gated:
        return
    record(evt, [check for call in gated for check in compiles(evt, call)])


def record(evt: BaseHookEvent, found: list[tuple[str, Path, bool]]) -> None:
    with CompileChecks.mutate(evt) as state:
        state.checks += [Check(kind=kind, root=str(root), recursive=recursive, at=time.time()) for kind, root, recursive in found]


def edited_at(path: Path) -> float | None:
    existing = next((candidate for candidate in (path, path.parent) if candidate.exists()), None)
    return existing.stat().st_mtime if existing else None


def covered(checks: list[Check], kind: str, target: Path, edited: float) -> bool:
    return any(
        check.kind == kind and check.at > edited and (Path(check.root) == target or check.recursive and Path(check.root) in target.parents)
        for check in checks
    )


def ancestor_with(name: str, start: Path, top: Path) -> Path | None:
    return next((folder for folder in (start, *start.parents) if (folder / name).is_file() and (folder == top or top in folder.parents)), None)


def latest(paths: list[Path], key) -> dict[Path, float]:
    grouped: dict[Path, float] = {}
    for path in paths:
        if (edited := edited_at(path)) is not None and (group := key(path)) is not None:
            grouped[group] = max(grouped.get(group, 0.0), edited)
    return grouped


def go_commands(evt: BaseHookEvent, top: Path, changed: list[Path]) -> list[str] | str:
    files = [path for path in changed if path.suffix == ".go" and not NOT_PACKAGES & set(path.relative_to(top).parts)]
    if not (packages := latest(files, lambda path: path.parent)):
        return []
    if not (goroot := run_quietly(["go", "env", "GOROOT"], str(top))) or goroot.returncode:
        return "no Go toolchain answers `go env GOROOT`"
    checks = CompileChecks.load(evt).checks
    missing: dict[tuple[Path, str], list[str]] = defaultdict(list)
    for package, edited in sorted(packages.items()):
        if (module := ancestor_with("go.mod", package, top)) is None:
            return f"`{package.relative_to(top)}` has no `go.mod` in this checkout"
        for kind in GO_KINDS:
            if not covered(checks, kind, package, edited):
                missing[module, kind].append(f"./{package.relative_to(module)}".removesuffix("/."))
    return [f"cd {module} && go {kind} {' '.join(names)}" for (module, kind), names in missing.items()]


def typecheck_command(top: Path, project: Path) -> str:
    runner = "yarn" if (top / "yarn.lock").is_file() else "npm run"
    root, scripts = scripts_at(top)
    name = next((name for name, line in scripts.items() if name.startswith("tsc:") and name != "tsc:all" and project in projects(root, {}, line.split())), None)
    if name:
        return f"{runner} {name}"
    return f"npx tsc --noEmit -p {project}"


def ts_commands(evt: BaseHookEvent, top: Path, changed: list[Path]) -> list[str]:
    files = [path for path in changed if path.suffix in TS_SUFFIXES and "node_modules" not in path.parts]
    projects_edited = latest(files, lambda path: ancestor_with("tsconfig.json", path.parent, top))
    checks = CompileChecks.load(evt).checks
    return [typecheck_command(top, project) for project, edited in sorted(projects_edited.items()) if not covered(checks, TYPECHECK, project, edited)]


def branch_diff(evt: BaseHookEvent, call: Call, *flags: str) -> tuple[Path, str] | str:
    if not (shown := run_quietly(["git", "rev-parse", "--show-toplevel"], str(Path(evt.cwd, call.cwd or ".")))) or shown.returncode or not shown.stdout.strip():
        return "the checkout's top level did not read"
    top = Path(shown.stdout.strip())
    diffed = run_quietly(["git", "diff", "--src-prefix=a/", "--dst-prefix=b/", *flags, "--merge-base", "origin/HEAD"], str(top))
    if not diffed or diffed.returncode:
        return "the diff against `origin/HEAD` did not read"
    return top, diffed.stdout


def unchecked(evt: BaseHookEvent, call: Call) -> list[str] | str:
    if isinstance(diffed := branch_diff(evt, call, "--name-only"), str):
        return diffed
    top, names = diffed
    sparse = run_quietly(["git", "config", "--bool", "--default", "false", "core.sparseCheckout"], str(top))
    if not sparse or sparse.returncode or sparse.stdout.strip() == "true":
        return "sparse checkout can't compile; TypeScript: `node_modules/.bin/tsgo --noEmit -p <project> --preserveSymlinks false --composite false`, your files only"
    changed = [top / line for line in names.splitlines() if line.strip()]
    go = go_commands(evt, top, changed)
    return go if isinstance(go, str) else go + ts_commands(evt, top, changed)


@on(  # capt-hook: no-escape the owner ordered a hook rather than goodwill, and every misread fails open with a warning
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command=SHIP, env=LANE_ENV, cwd=str(FIXTURE), commands=GO_DIFF): Block(pattern=r"^A lane runs `go build` and `go vet`[\s\S]*cd \S+/go/sand && go build \./internal/vm\ncd \S+/go/sand && go vet \./internal/vm\n"),
        Input(command="git push origin HEAD", env=LANE_ENV, cwd=str(FIXTURE), commands=GO_DIFF, state=[fresh("vet", root="go/sand")]): Block(pattern=r"cd \S+ && go build \./internal/vm\n```$"),
        Input(command="gt submit --stack", env=LANE_ENV, cwd=str(FIXTURE), commands=GO_DIFF, state=[fresh("build", "vet", root="go/sand", at=0.0)]): Block(),
        Input(command=SHIP, env=LANE_ENV, cwd=str(FIXTURE), commands=TS_DIFF): Block(pattern=r"\nyarn tsc:api\n"),
        Input(command=SHIP, env=LANE_ENV, cwd=str(FIXTURE), commands=OWN_TS_DIFF): Block(pattern=r"\nnpx tsc --noEmit -p \S+/tools/own\n"),
        Input(command=SHIP, env=LANE_ENV, cwd=str(FIXTURE), commands=OWN_TS_DIFF, state=[fresh(TYPECHECK, root="tools/own", recursive=False)]): Allow(),
        Input(command=SHIP, env=LANE_ENV, cwd=str(FIXTURE), commands=GO_DIFF, state=[fresh("build", "vet", root="go/sand")]): Allow(),
        Input(command=SHIP, env=LANE_ENV, cwd=str(FIXTURE), commands=GO_DIFF, state=[fresh("build", "vet", root="go/sand/internal/vm", recursive=False)]): Allow(),
        Input(command=SHIP, env=LANE_ENV, cwd=str(FIXTURE), commands=TS_DIFF, state=[fresh(TYPECHECK, root="api", recursive=False)]): Allow(),
        Input(command=SHIP, env=LANE_ENV, cwd=str(FIXTURE), commands=REPO | {"git diff": "docs/vm.md\n.github/workflows/ci.yml\n"}): Allow(),
        Input(command=SHIP, env=LANE_ENV, cwd=str(FIXTURE), commands=GO_DIFF | {"go env GOROOT": (127, "go: not found")}): Warn(pattern=r"^Warning, nothing was refused: no Go toolchain"),
        Input(command=SHIP, env=LANE_ENV, cwd=str(FIXTURE), commands=GO_DIFF | {"git config --bool --default false core.sparseCheckout": "true\n"}): Warn(pattern=r"sparse checkout"),
        Input(command=SHIP, env=LANE_ENV, cwd=str(FIXTURE), commands=REPO | {"git diff": (128, "fatal: bad revision 'origin/HEAD'")}): Warn(pattern=r"`origin/HEAD` did not read"),
        Input(command=SHIP, env=LANE_ENV, cwd=str(FIXTURE), commands=REPO | {"git diff": "tools/loose/main.go\n"}): Warn(pattern=r"has no `go.mod`"),
        Input(command="git push --dry-run", env=LANE_ENV, cwd=str(FIXTURE), commands=GO_DIFF): Allow(),
        Input(command=SHIP, env={"CLAUDE_LONG_RUNNING_LANE": ROOT_LANE}, cwd=str(FIXTURE), commands=GO_DIFF): Allow(),
        Input(command=SHIP, env={"CLAUDE_LONG_RUNNING_LANE": COMMUNICATOR_LANE}, cwd=str(FIXTURE), commands=GO_DIFF): Allow(),
        Input(command=SHIP, env={}, cwd=str(FIXTURE), commands=GO_DIFF): Allow(),
    },
)
def lanes_vet_before_they_push(evt: BaseHookEvent) -> HookResult | None:
    if not in_lane(evt) or not (calls := [call for call in evt.command.calls() if pushes(call)]):
        return None
    commands: list[str] = []
    for call in calls:
        if isinstance(found := unchecked(evt, call), str):
            return evt.context(UNREAD.format(why=found))
        commands += found
    return evt.block(REFUSAL.format(commands="\n".join(dict.fromkeys(commands)))) if commands else None
