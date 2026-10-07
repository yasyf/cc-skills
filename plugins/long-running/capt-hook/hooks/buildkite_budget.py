from __future__ import annotations

import ast
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from urllib.parse import SplitResult, urlsplit

from captain_hook import Allow, Annotated, BaseHookEvent, Block, Call, Cmd, Event, HookResult, Input, Tool, ast_grep, on

FIXTURES = Path(__file__).parent / "tests" / "fixtures" / "buildkite"
REST_HOST = "api.buildkite.com"
HTTP_CLIENTS = frozenset({"curl", "wget", "http", "xh"})
LOCAL_BK = frozenset({"configure", "use", "init", "prompt", "version", "help", "--help", "-h", "--version"})
LOG_LEAVES = frozenset({"log", "log.txt"})
SHELLS = frozenset({"sh", "bash", "zsh"})
SCRIPT_LANGS = {".sh": "bash", ".bash": "bash", ".zsh": "bash", ".py": "py"}
FAN_OUT_WRAPPERS = frozenset({"xargs"})
MAPPERS = frozenset({"map", "imap", "imap_unordered", "starmap", "map_async", "starmap_async"})
LOOP_KINDS = frozenset({"for_statement", "c_style_for_statement", "while_statement"})
POLL_KINDS = frozenset({"c_style_for_statement", "while_statement"})
SLEEP_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
POLL_FLOOR_SECONDS = 30
MAX_SCRIPT_BYTES = 256_000
MAX_DEPTH = 3

LOGS = "logs"
POLL = "poll"

LOGS_MESSAGE = (
    "Dumping Buildkite logs job by job drains the org REST budget Platy's release reads share."
    " Make one build-level call, `bk build view <build> -p <pipeline> --json`, then `bk job log <id>` for the one job you need."
)
POLL_MESSAGE = (
    "Polling Buildkite more often than every 30 s drains the org REST budget Platy's release reads share."
    " Run the poll under `Monitor` or a blocking wait that sleeps 30 s or more between calls."
)


@dataclass(frozen=True)
class Source:
    lang: str
    text: str
    cwd: Path | None
    repeated: bool = False


def url(text: str) -> SplitResult | None:
    try:
        return urlsplit(text if "://" in text or text.startswith("/") else f"https://{text}")
    except ValueError:
        return None


def rest_url(text: str) -> bool:
    return REST_HOST in text and (parts := url(text)) is not None and parts.hostname == REST_HOST


def rest_argv(argv: Sequence[str]) -> bool:
    match argv:
        case ["bk", subcommand, *_]:
            return subcommand not in LOCAL_BK
        case [client, *args] if client in HTTP_CLIENTS:
            return any(rest_url(arg) for arg in args)
    return False


def job_log_path(text: str) -> bool:
    if (parts := url(text)) is None:
        return False
    path = PurePosixPath(parts.path)
    return path.name in LOG_LEAVES and path.parent.parent.name == "jobs"


def job_log_argv(argv: Sequence[str]) -> bool:
    match argv:
        case ["bk", "job", "log", *_]:
            return True
        case ["bk", "api", *args]:
            return any(job_log_path(arg) for arg in args)
        case [client, *args] if client in HTTP_CLIENTS:
            return any(rest_url(arg) and job_log_path(arg) for arg in args)
    return False


def seconds(word: str) -> float | None:
    unit = word[-1:] if word[-1:] in SLEEP_UNITS else ""
    number = word.removesuffix(unit)
    return float(number) * SLEEP_UNITS.get(unit, 1) if number.replace(".", "", 1).isdigit() else None


def sleep_seconds(args: Sequence[str]) -> float | None:
    parts = [seconds(arg) for arg in args]
    return sum(parts) if parts and None not in parts else None


def short_wait(waits: Sequence[float | None]) -> bool:
    return bool(waits) and None not in waits and sum(waits) < POLL_FLOOR_SECONDS


def argvs(cmd: Cmd) -> list[tuple[str, ...]]:
    return [(call.name, *call.args) for call in cmd.calls()]


def script_path(word: str, cwd: Path | None) -> Path | None:
    path = Path.home() / word[2:] if word.startswith("~/") else Path(word)
    path = path if path.is_absolute() or cwd is None else cwd / path
    return path if path.suffix in SCRIPT_LANGS and os.path.isfile(path) else None


def read_script(path: Path, cwd: Path | None, repeated: bool) -> Source | None:
    if path.stat().st_size > MAX_SCRIPT_BYTES:
        return None
    return Source(SCRIPT_LANGS[path.suffix], path.read_text(errors="replace"), cwd, repeated)


def inline_payload(call: Call) -> str | None:
    shell = call.name in SHELLS
    for at, arg in enumerate(call.args):
        if arg == "-c" or (shell and arg.startswith("-") and not arg.startswith("--") and "c" in arg):
            return call.args[at + 1] if at + 1 < len(call.args) else None
        if not arg.startswith("-"):
            return None
    return None


def executed_words(call: Call) -> list[str]:
    words = [call.command.executable]
    if call.name in SHELLS or call.name.startswith("python"):
        words += [next((arg for arg in call.args if not arg.startswith("-")), "")]
    if "--" in call.args:
        words += call.args[call.args.index("--") + 1 : call.args.index("--") + 2]
    return [word for word in words if word]


def repeated_calls(cmd: Cmd, text: str) -> set[tuple[str, ...]]:
    looped = {
        (call.name, *call.args)
        for loop in ast_grep.find_kinds(text, "bash", LOOP_KINDS)
        if (body := Cmd.parse(loop.text)) is not None
        for call in body.calls()
    }
    return looped | {(call.name, *call.args) for call in cmd.calls() if under_fan_out(cmd, call)}


def under_fan_out(cmd: Cmd, call: Call) -> bool:
    by_index = {each.occurrence.index: each for each in cmd.calls()}
    hop: Call | None = call
    while hop is not None:
        if FAN_OUT_WRAPPERS & set(hop.wrappers):
            return True
        hop = by_index.get(hop.occurrence.host.index) if hop.occurrence.host is not None else None
    return False


def heredoc_sources(text: str, cwd: Path | None) -> Iterator[Source]:
    for statement in ast_grep.parse(text, "bash").descendants():
        if statement.kind != "redirected_statement":
            continue
        parts = list(statement.descendants())
        bodies = [part.text for part in parts if part.kind == "heredoc_body"]
        head = next((part for part in parts if part.kind == "command"), None)
        if not bodies or head is None or (cmd := Cmd.parse(head.text)) is None or not (calls := cmd.calls()):
            continue
        targets = [
            word.text
            for redirect in parts
            if redirect.kind == "file_redirect"
            for word in redirect.descendants()
            if word.kind == "word"
        ]
        name = calls[0].name
        writes = [*targets, *(calls[0].args if name == "tee" else ())]
        if name.startswith("python"):
            lang = "py"
        elif name in SHELLS:
            lang = "bash"
        else:
            lang = next((SCRIPT_LANGS[suffix] for word in writes if (suffix := PurePosixPath(word).suffix) in SCRIPT_LANGS), None)
        if lang is not None:
            yield from (Source(lang, body, cwd) for body in bodies)


def shell_children(cmd: Cmd, source: Source) -> Iterator[Source]:
    repeated = repeated_calls(cmd, source.text)
    for call in cmd.calls():
        cwd = call.cwd or source.cwd
        again = source.repeated or (call.name, *call.args) in repeated
        if (payload := inline_payload(call)) is not None:
            if call.name in SHELLS:
                yield Source("bash", payload, cwd, again)
            elif call.name.startswith("python"):
                yield Source("py", payload, cwd, again)
        for word in executed_words(call):
            if (path := script_path(word, cwd)) is not None and (script := read_script(path, cwd, again)) is not None:
                yield script
    yield from heredoc_sources(source.text, source.cwd)


def shell_findings(cmd: Cmd, source: Source) -> set[str]:
    logs = [call for call in cmd.calls() if job_log_argv((call.name, *call.args))]
    found = {LOGS} if logs and (source.repeated or any(under_fan_out(cmd, call) for call in logs)) else set()
    for loop in ast_grep.find_kinds(source.text, "bash", LOOP_KINDS):
        if (body := Cmd.parse(loop.text)) is None:
            continue
        calls = argvs(body)
        if any(job_log_argv(argv) for argv in calls):
            found.add(LOGS)
    for loop in ast_grep.find_kinds(source.text, "bash", POLL_KINDS):
        if (body := Cmd.parse(loop.text)) is None:
            continue
        calls = argvs(body)
        waits = [sleep_seconds(argv[1:]) for argv in calls if argv[0] == "sleep"]
        if any(rest_argv(argv) for argv in calls) and short_wait(waits):
            found.add(POLL)
    return found


def literal(node: ast.AST) -> str | None:
    match node:
        case ast.Constant(value=str() as value):
            return value
        case ast.JoinedStr(values=values):
            return "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in values)
    return None


def py_argv(node: ast.AST) -> tuple[str, ...] | None:
    match node:
        case ast.List(elts=elts) | ast.Tuple(elts=elts) if elts and literal(elts[0]) in ("bk", *HTTP_CLIENTS):
            return tuple(literal(elt) or "{}" for elt in elts)
        case ast.Constant(value=str()) | ast.JoinedStr() if (text := literal(node)) and text.split(" ", 1)[0] in ("bk", *HTTP_CLIENTS):
            return next(iter(argvs(cmd)), None) if (cmd := Cmd.parse(text)) is not None else None
    return None


def py_rest(node: ast.AST) -> bool:
    return ((argv := py_argv(node)) is not None and rest_argv(argv)) or rest_url(literal(node) or "")


def py_job_log(node: ast.AST) -> bool:
    if (argv := py_argv(node)) is not None:
        return job_log_argv(argv)
    return rest_url(text := literal(node) or "") and job_log_path(text)


def py_waits(nodes: Sequence[ast.AST]) -> list[float | None]:
    waits: list[float | None] = []
    for node in nodes:
        match node:
            case ast.Call(func=ast.Name(id="sleep") | ast.Attribute(attr="sleep"), args=[ast.Constant(value=int() | float() as wait), *_]):
                waits.append(float(wait))
            case ast.Call(func=ast.Name(id="sleep") | ast.Attribute(attr="sleep")):
                waits.append(None)
    return waits


def mapped(call: ast.AST, wanted: set[str]) -> bool:
    match call:
        case ast.Call(func=ast.Name(id="map"), args=[first, *_]) if names(first, wanted):
            return True
        case ast.Call(func=ast.Attribute(attr=attr), args=[first, *_]) if names(first, wanted):
            return attr in MAPPERS
    return False


def py_loops(tree: ast.AST, *, polls: bool) -> Iterator[ast.AST]:
    for node in ast.walk(tree):
        match node:
            case ast.While():
                yield node
            case ast.For(iter=ast.Call(func=ast.Name(id="range") | ast.Attribute(attr="count"))) | ast.AsyncFor(
                iter=ast.Call(func=ast.Name(id="range") | ast.Attribute(attr="count"))
            ) if polls:
                yield node
            case ast.For() | ast.AsyncFor() | ast.ListComp() | ast.SetComp() | ast.DictComp() | ast.GeneratorExp() if not polls:
                yield node


def callers(tree: ast.AST, predicate: object) -> set[str]:
    return {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and any(predicate(node) for node in ast.walk(fn))
    }


def names(node: ast.AST, wanted: set[str]) -> bool:
    return isinstance(node, ast.Name) and node.id in wanted


def py_findings(source: Source) -> set[str]:
    try:
        tree = ast.parse(source.text)
    except SyntaxError:
        return set()
    loggers, pollers = callers(tree, py_job_log), callers(tree, py_rest)
    found: set[str] = set()
    for loop in py_loops(tree, polls=False):
        if any(py_job_log(node) or names(node, loggers) for node in ast.walk(loop)):
            found.add(LOGS)
    if any(mapped(call, loggers) for call in ast.walk(tree)):
        found.add(LOGS)
    if source.repeated and (loggers or any(py_job_log(node) for node in ast.walk(tree))):
        found.add(LOGS)
    for loop in py_loops(tree, polls=True):
        nodes = list(ast.walk(loop))
        if any(py_rest(node) or names(node, pollers) for node in nodes) and short_wait(py_waits(nodes)):
            found.add(POLL)
    return found


def findings(source: Source, depth: int = 0) -> set[str]:
    if source.lang == "py":
        return py_findings(source)
    if (parsed := Cmd.parse(source.text)) is None:
        return set()
    cmd = replace(parsed, cwd=source.cwd)
    found = shell_findings(cmd, source)
    if depth < MAX_DEPTH:
        for child in shell_children(cmd, source):
            found |= findings(child, depth + 1)
    return found


def command_findings(evt: BaseHookEvent) -> set[str]:
    command = evt.input.raw.get("command")
    return findings(Source("bash", command, evt.cwd)) if isinstance(command, str) and command.strip() else set()


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash", "Monitor")],
    skip_if=[Annotated("raw")],
    tests={
        Input(
            command=(
                "jq -r '.jobs[].id' build.json | xargs -P 6 -I{} zsh -c"
                " \"bk api '/pipelines/$p/builds/$b/jobs/{}/log' | jq -r .content > $out/{}.log\""
            )
        ): Block(pattern="bk build view"),
        Input(command="for id in $(jq -r '.jobs[].id' b.json); do bk job log $id > $id.log; done"): Block(),
        Input(command="jq -r '.jobs[].id' b.json | while read id; do bk api \"/pipelines/test/builds/9/jobs/$id/log\"; done"): Block(),
        Input(command=f"cd /tmp && python3 {FIXTURES}/job_logs.py 595"): Block(pattern="bk job log"),
        Input(command=f"python3 {FIXTURES}/job_logs_pool.py"): Block(),
        Input(
            command=(
                "python3 - <<'EOF'\nimport subprocess\nfor j in open('ids').read().split():\n"
                "    subprocess.run(['bk', 'api', f'/pipelines/p/builds/1/jobs/{j}/log'])\nEOF"
            )
        ): Block(),
        Input(command="bash -c 'for id in a b; do bk job log $id; done'"): Block(),
        Input(command="bash -lc 'for id in a b; do bk job log $id; done'"): Block(),
        Input(command=f"cd {FIXTURES} && python3 job_logs.py 595"): Block(),
        Input(command=f"cat ids | xargs -n 1 python3 {FIXTURES}/one_job_log.py 595"): Block(),
        Input(command=f"for id in a b; do python3 {FIXTURES}/one_job_log.py 595 $id; done"): Block(),
        Input(
            command=(
                "for id in $(cat ids); do curl -sS -H \"Authorization: Bearer $T\""
                " https://api.buildkite.com/v2/organizations/forge/pipelines/test/builds/9/jobs/$id/log; done"
            )
        ): Block(),
        Input(tool="Monitor", tool_input={"command": "for id in a b; do bk job log $id; done", "description": "logs"}): Block(),
        Input(command="bk job log 0190046e-e199-453b-a302-a21a4d649d31 --agent"): Allow(),
        Input(command="bk build view 595 -p release-pr-check --json"): Allow(),
        Input(command="for b in 14399 14396; do bk build view $b -p test --json; done"): Allow(),
        Input(command=f"python3 {FIXTURES}/one_job_log.py 595 0190046e"): Allow(),
        Input(command="gh pr create --body-file - <<'EOF'\nfor id in $ids; do bk job log $id; done\nEOF"): Allow(),
        Input(command="echo 'for id in a b; do bk job log $id; done'"): Allow(),
        Input(command="python3 /nonexistent/dump_logs.py"): Allow(),
        Input(command='python3 -c "for job in"'): Allow(),
        Input(command=f"cat {FIXTURES}/job_logs.py"): Allow(),
        Input(
            command=(
                "python3 -c \"import subprocess\nfrom concurrent.futures import ThreadPoolExecutor\n"
                "def fetch(j):\n    subprocess.run(['bk', 'job', 'log', j])\n"
                "ThreadPoolExecutor().submit(fetch, 'one')\""
            )
        ): Allow(),
        Input(command="for id in a b; do bk job log $id; done  # ccx:raw"): Allow(),
    },
)
def no_per_job_buildkite_log_dumps(evt: BaseHookEvent) -> HookResult | None:
    return evt.block(LOGS_MESSAGE) if LOGS in command_findings(evt) else None


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash", "Monitor")],
    skip_if=[Annotated("raw")],
    tests={
        Input(
            command=(
                "until s=$(bk api /pipelines/release-pr-check/builds/626 2>/dev/null | jq -r .state);"
                ' [ "$s" = passed ] || [ "$s" = failed ]; do sleep 20; done; echo $s'
            )
        ): Block(pattern="Monitor"),
        Input(
            tool="Monitor",
            tool_input={"command": "while true; do bk build view 1 -p test --json | jq -r .state; sleep 10; done", "description": "b"},
        ): Block(),
        Input(command=f"{FIXTURES}/poll_fast.sh 45159"): Block(),
        Input(
            command=(
                "python3 -c \"import subprocess, time\nwhile True:\n"
                "    subprocess.run(['bk', 'build', 'view', '1', '--json'])\n    time.sleep(5)\""
            )
        ): Block(),
        Input(
            command=(
                "python3 -u -c \"import subprocess, time\nwhile True:\n"
                "    subprocess.run(['bk', 'build', 'view', '1', '--json'])\n    time.sleep(5)\""
            )
        ): Block(),
        Input(
            command=(
                "while true; do curl -sS https://api.buildkite.com/v2/organizations/forge/pipelines/test/builds/1"
                " | jq -r .state; sleep 15s; done"
            )
        ): Block(),
        Input(
            command=(
                "cat > /tmp/wait.sh <<'EOF'\nwhile true; do bk api /pipelines/test/builds/1; sleep 10; done\nEOF\n"
                "chmod +x /tmp/wait.sh && /tmp/wait.sh"
            )
        ): Block(),
        Input(
            command=(
                "until s=$(bk api /pipelines/release-pr-check/builds/626 2>/dev/null | jq -r .state);"
                ' [ "$s" = passed ]; do sleep 30; done'
            )
        ): Allow(),
        Input(command=f"{FIXTURES}/poll_slow.sh 45159 45164"): Allow(),
        Input(command="while true; do gh pr checks 1; sleep 5; done"): Allow(),
        Input(command="while true; do bk build view 1; sleep 15; sleep 15; done"): Allow(),
        Input(command="while true; do curl https://example.com -o api.buildkite.com.json; sleep 5; done"): Allow(),
        Input(command=f"cat {FIXTURES}/poll_fast.sh"): Allow(),
        Input(command="for b in 1 2; do bk build view $b -p test --json; sleep 1; done"): Allow(),
        Input(command="sleep 5 && bk build view 1 -p test --json"): Allow(),
        Input(command="while true; do bk build view 1; sleep $INTERVAL; done"): Allow(),
        Input(command="cat > notes.md <<'EOF'\nwhile true; do bk build view 1; sleep 5; done\nEOF"): Allow(),
        Input(command="while true; do bk build view 1; sleep 5; done  # ccx:raw"): Allow(),
    },
)
def no_fast_buildkite_polls(evt: BaseHookEvent) -> HookResult | None:
    return evt.block(POLL_MESSAGE) if POLL in command_findings(evt) else None
