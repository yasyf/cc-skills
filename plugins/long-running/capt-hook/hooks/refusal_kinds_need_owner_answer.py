from __future__ import annotations

import ast
import json
import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from captain_hook import CONFIRMED, UNCONFIRMED, Allow, BaseHookEvent, Block, Call, Confirm, Event, HookResult, Input, Tool, on
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState
from .lane_landing import DRIVE_ENV, LANDERS, drive_entry, lane_name, named_repo, without_repo
from .tests.refusal_fixtures import (
    CARD_TERMS,
    CARD_TERMS_KIND,
    CARD_TERMS_SOURCE,
    CITED,
    DIGIT_ID_BODY,
    FAILS_OPEN,
    HEREDOC_BODY,
    LAUNCH_STALLED,
    LOGGED_STATE,
    NAMED_ANSWER,
    NO_DRIVE,
    PLAIN,
    PLAN,
    PLAN_GATES_BODY,
    RELISTED,
    RETIRE_AND_RECONVERT,
    SENTINEL_ANSWER,
    SENTINEL_BODY,
    SENTINEL_ONLY,
    TEST_ONLY,
    TREE,
    UNCITED,
    UNNAMED,
    UPLOAD_BUDGET,
    sources,
)

ACTIVE = [CompactionState(active=True)]
PLANNED = [CompactionState(active=True, plan_path=str(PLAN))]
LANE = "alane-a-0123456789abcdef"
READ_TIMEOUT_SECONDS = 20
SHOWN = 12
RULINGS_SHOWN = 1500
TEXT_SHOWN = 160
SHIP_BODY = ("--pr-body-file",)
SHIP_VALUES = frozenset(
    {
        "-m", "--message", "--bookmark", "--branch", "--budget", "--expect-remote", "--landed", "--new-pr", "--only-hunk",
        "--parent", "--pr-body-file", "--pr-title", "--skip-hunk",
    }
)
SHIP_OPTIONAL_VALUES = frozenset({"--new-branch", "--create"})
GH_HEAD = ("--head", "-H")
RULING_ITEM = re.compile(r"\n(?=\s*(?:[-*]|\d+\.)\s)")
GH_BODY = ("--body", "-b")
GH_BODY_FILE = ("--body-file", "-F")
GH_EDIT_VALUES = frozenset(
    {
        "--body", "-b", "--body-file", "-F", "--title", "-t", "--base", "-B", "--milestone", "-m", "--repo", "-R",
        "--add-label", "--remove-label", "--add-reviewer", "--remove-reviewer", "--add-assignee", "--remove-assignee",
        "--add-project", "--remove-project",
    }
)
TRUNK = ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"]
UNTRACKED = ["git", "ls-files", "--others", "--exclude-standard", "--full-name", "-z"]
TOPLEVEL = ["git", "rev-parse", "--show-toplevel"]
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")
EXEMPT = re.compile(
    r"(?:^|/)(?:tests?|testdata|fixtures|__snapshots__)/|(?:^|/)test_[^/]*$|_test\.\w+$|\.(?:test|spec)\.\w+$"
    r"|\.(?:md|mdx|txt|rst|lock|sum)$|(?:^|/)package-lock\.json$"
)
COMMENT = re.compile(r"^\s*(?:#|//|--\s|/\*|\*\s|\*$)")
KIND = re.compile(r"""["'`](?P<name>[A-Z][A-Z0-9]+(?:-[A-Z][A-Z0-9]+)+)["'`]""")
REFUSAL_NAME = re.compile(r"\b(?P<name>\w*(?:refus|quarantin)\w*)\s*(?::[^=\n]*)?:?=(?!=)", re.IGNORECASE)
RETRY = re.compile(r"\b(?P<name>\w*(?:retr(?:y|ies|ied|ying)|backoff)\w*)", re.IGNORECASE)
CLOCK = re.compile(
    r"(?P<name>\btime\.(?:[Ss]leep|NewTicker|NewTimer|Tick|After|AfterFunc|Duration)\b|\basyncio\.sleep\b|\bthreading\.Timer\b"
    r"|\bset(?:Interval|Timeout)\b|\btimedelta\(|\*\s*time\.(?:Second|Minute|Hour|Millisecond)\b|\bsleep\s+\d)"
)
BUDGET = re.compile(
    r"""\b(?P<name>\w*(?:max|limit|budget|timeout|deadline|ttl|threshold|window|interval|seconds|minutes|hours|attempts|cooldown|grace)\w*)"""
    r"""["']?\s*(?::[^=\n]*)?[:=]\s*-?\d""",
    re.IGNORECASE,
)
REFUSAL_HINT = re.compile(
    r"""\breturn\b[^\n]*\b(?P<sentinel>err[A-Z]\w*)|["'](?P<cause>cause:\w+)"""
    r"""|(?i:\b(?P<name>refus\w*|reject\w*|quarantin\w*|re(?:convert|attempt|queue|dispatch)\w*|read-only|fallback)\b)"""
)
NAME_GROUPS = ("name", "sentinel", "cause")
ASSIGNED = re.compile(r"""^\s*(?:(?:const|var|let|export|final)\s+)*["']?(?P<name>[A-Za-z_]\w+)["']?\s*(?::[^=\n]*)?(?::=|=|:)(?!=)""")
PLAN_REF = re.compile(r"\bplan (?:step |item )?(?P<step>\d+)\.(?P<item>\d+)\b", re.IGNORECASE)
PLAN_STEP = re.compile(r"^#+\s+Step (?P<step>\d+)\b")
PLAN_DOTTED = re.compile(r"^(?P<step>\d+)\.(?P<item>\d+)\s")
PLAN_NUMBERED = re.compile(r"^(?P<item>\d+)\.\s")
PLAN_SHOWN = 400
RULINGS = re.compile(r"^##\s+Rulings\b.*?(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
CCN_NOT_FOUND = 3
DECLARING = frozenset(":,]}")
ANSWER_ID = re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b")
TEST_LITERALS = frozenset({"Input", "Block", "Allow", "Warn", "Ask", "Rewrite"})
UNCITED_KINDS = (
    "A drive lane's PR may not add a refusal kind, quarantine kind, budget, retry, or clock unless its `## Rulings` section cites an owner answer naming it."
    " Cite the answer beside each name below, or post `cci post --kind ask --to root` for one and wait for it."
)
UNNAMED_KINDS = (
    "Each refusal kind, quarantine kind, budget, retry, or clock a drive lane's PR adds needs an owner answer that names it under `## Rulings`."
    " Name each one below beside the answer that covers it, or post `cci post --kind ask --to root` for one."
)
NAMES_IT = Confirm(
    rule="Block only when the PR adds a refusal kind, quarantine kind, budget, retry, or clock that no owner answer or plan item under its"
    " Rulings section names; a matched line that is none of those five things never blocks."
)


@dataclass(frozen=True)
class Finding:
    kind: str
    name: str
    path: str
    line: int
    text: str
    definite: bool

    def render(self) -> str:
        return f"{self.path}:{self.line} {self.kind} {self.name}: {self.text.strip()[:TEXT_SHOWN]}"


@dataclass(frozen=True)
class Request:
    raw: str
    cwd: Path | None
    gh: tuple[str, ...]
    base: str | None
    bodies: tuple[str, ...]
    texts: tuple[str, ...]
    edit: tuple[str, ...] | None
    worktree: bool
    paths: tuple[str, ...] = ()
    head: str | None = None
    stdin: str | None = None


def run(argv: list[str], cwd: Path | None) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=READ_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None


def read(argv: list[str], cwd: Path | None) -> str | None:
    done = run(argv, cwd)
    return done.stdout if done is not None and done.returncode == 0 else None


def parsed(text: str | None) -> dict | None:
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def text_of(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return None


def values(args: tuple[str, ...], flags: tuple[str, ...]) -> list[str]:
    found = [value for flag, value in zip(args, args[1:], strict=False) if flag in flags]
    return found + [arg.split("=", 1)[1] for arg in args for flag in flags if flag.startswith("--") and arg.startswith(f"{flag}=")]


def ship_paths(args: tuple[str, ...]) -> tuple[str, ...]:
    paths, index = [], 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            paths.extend(args[index + 1 :])
            break
        if arg in SHIP_VALUES or (arg in SHIP_OPTIONAL_VALUES and args[index + 1 : index + 2] and not args[index + 1].startswith("-")):
            index += 2
        elif arg.startswith("-"):
            index += 1
        else:
            paths.append(arg)
            index += 1
    return tuple(paths)


def in_scope(req: Request, path: str) -> bool:
    return not req.paths or any(path == scope.rstrip("/") or path.startswith(scope.rstrip("/") + "/") for scope in req.paths)


def selector(args: tuple[str, ...]) -> tuple[str, ...]:
    index = 0
    while index < len(args):
        if args[index] in GH_EDIT_VALUES:
            index += 2
        elif args[index].startswith("-"):
            index += 1
        else:
            return (args[index],)
    return ()


def stdin_file(call: Call) -> str | None:
    return next((redirect.target for redirect in call.redirects if redirect.op == "<" and redirect.fd in (None, 0)), None)


def request(evt: BaseHookEvent, call: Call) -> Request | None:
    cwd = call.cwd or evt.cwd
    if call.name == "ccx" and call.args[:2] == ("vcs", "ship") and "--no-pr" not in call.args:
        parent = values(call.args, ("--parent",))
        return Request(
            str(evt.command), cwd, (), parent[0] if parent else None, tuple(values(call.args, SHIP_BODY)), (), None, True, ship_paths(call.args[2:]),
            stdin=stdin_file(call),
        )
    argv = without_repo((call.name, *call.args)) if call.name == "gh" else ()
    repo = ("-R", found) if (found := named_repo(call)) else ()
    if argv[:3] == ("gh", "pr", "create"):
        base, head = values(argv, ("--base", "-B")), values(argv, GH_HEAD)
        return Request(
            str(evt.command), cwd, repo, base[0] if base else None, tuple(values(argv, GH_BODY_FILE)), tuple(values(argv, GH_BODY)), None, False,
            head=head[0] if head else None, stdin=stdin_file(call),
        )
    if argv[:3] == ("gh", "pr", "edit") and (values(argv, GH_BODY) or values(argv, GH_BODY_FILE)):
        return Request(
            str(evt.command), cwd, repo, None, tuple(values(argv, GH_BODY_FILE)), tuple(values(argv, GH_BODY)), selector(argv[3:]), False,
            stdin=stdin_file(call),
        )
    return None


def lane_prs(evt: BaseHookEvent) -> list[Request]:
    if (lane := lane_name(evt)) is None or LANDERS.match(lane):
        return []
    if evt.agent_id and not reqenv.getenv("CLAUDE_LONG_RUNNING_LANE") and not CompactionState.load(evt).active:
        return []
    return [found for call in evt.cmd.calls() if (found := request(evt, call))]


def added(diff: str) -> Iterator[tuple[str, int, str]]:
    path, line = "", 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].removeprefix("b/")
        elif hunk := HUNK.match(raw):
            line = int(hunk["start"])
        elif raw.startswith("+") and path:
            yield path, line, raw[1:]
            line += 1
        elif raw.startswith(" "):
            line += 1


def removed(diff: str) -> Iterator[tuple[str, str]]:
    path = ""
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].removeprefix("b/")
        elif raw.startswith("-") and not raw.startswith("--- ") and path:
            yield path, raw[1:]


def toplevel(req: Request) -> Path | None:
    found = (read(TOPLEVEL, req.cwd) or "").strip() if req.worktree else ""
    return Path(found) if found else None


def untracked(cwd: Path | None, root: Path) -> Iterator[tuple[str, int, str]]:
    for name in filter(None, (read(UNTRACKED, cwd) or "").split("\0")):
        if not EXEMPT.search(name) and (content := text_of(root / name)) is not None:
            yield from ((name, number, text) for number, text in enumerate(content.splitlines(), start=1))


def pr_view(req: Request) -> dict | None:
    found = read(["gh", "pr", "view", *req.gh, "--json", "baseRefName,body"], req.cwd)
    return None if found is None else parsed(found)


def diff_text(req: Request, view: dict) -> str | None:
    if req.edit is not None:
        return read(["gh", "pr", "diff", *req.edit, *req.gh], req.cwd) or ""
    base = f"origin/{branch}" if (branch := req.base or view.get("baseRefName")) else (read(TRUNK, req.cwd) or "").strip()
    if not base:
        return None
    span = ["--merge-base", base] if req.worktree else [f"{base}...{req.head or 'HEAD'}"]
    return read(["git", "diff", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/", "--unified=0", *span], req.cwd) or ""


def revision(req: Request) -> str | None:
    if req.edit is None:
        return req.head or "HEAD"
    return (read(["gh", "pr", "view", *req.edit, *req.gh, "--json", "headRefOid", "--jq", ".headRefOid"], req.cwd) or "").strip() or None


def new_sides(req: Request, root: Path | None, paths: set[str]) -> dict[str, str | None]:
    if req.worktree:
        return {path: text_of(root / path) if root else None for path in paths}
    rev = revision(req) if paths else None
    return {path: read(["git", "show", f"{rev}:{path}"], req.cwd) if rev else None for path in paths}


def callee(func: ast.expr) -> str | None:
    match func:
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return name
    return None


def fixture_nodes(tree: ast.AST) -> Iterator[ast.expr]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if callee(node.func) in TEST_LITERALS:
                yield node
            yield from (keyword.value for keyword in node.keywords if keyword.arg == "tests")


def fixture_lines(text: str | None) -> frozenset[int]:
    try:
        tree = ast.parse(text or "")
    except SyntaxError:
        return frozenset()
    return frozenset(line for node in fixture_nodes(tree) for line in range(node.lineno, (node.end_lineno or node.lineno) + 1))


def new_findings(req: Request, view: dict) -> list[Finding]:
    if (diff := diff_text(req, view)) is None:
        return []
    root = toplevel(req)
    lines = [*added(diff), *(untracked(req.cwd, root) if root else ())]
    kept = {(path, finding.name) for path, text in removed(diff) for finding in classify(path, 0, text)}
    found = [finding for path, line, text in lines if in_scope(req, path) for finding in classify(path, line, text) if (path, finding.name) not in kept]
    sides = new_sides(req, root, {finding.path for finding in found if finding.path.endswith(".py")})
    fixtures = {path: fixture_lines(text) for path, text in sides.items()}
    return [finding for finding in found if finding.line not in fixtures.get(finding.path, ())]


def named(text: str, match: re.Match[str]) -> str:
    if found := ASSIGNED.match(text):
        return found["name"]
    groups = match.groupdict()
    return next((value for key in NAME_GROUPS if (value := groups.get(key))), match.group(0)).strip("\"'` ")


def kind_of(name: str) -> str:
    return "quarantine kind" if "quarantin" in name.casefold() else "refusal kind"


def declared(text: str, match: re.Match[str]) -> bool:
    return text[match.end() :].lstrip()[:1] in DECLARING


def classify(path: str, line: int, text: str) -> list[Finding]:
    if EXEMPT.search(path) or COMMENT.match(text):
        return []
    if kinds := [Finding(kind_of(match["name"]), match["name"], path, line, text, declared(text, match)) for match in KIND.finditer(text)]:
        return kinds
    if match := REFUSAL_NAME.search(text):
        return [Finding(kind_of(match["name"]), match["name"], path, line, text, True)]
    for kind, pattern, definite in (("retry", RETRY, False), ("clock", CLOCK, True), ("budget", BUDGET, True)):
        if match := pattern.search(text):
            return [Finding(kind, named(text, match), path, line, text, definite)]
    if match := REFUSAL_HINT.search(text):
        return [Finding("refusal or retry", named(text, match), path, line, text, False)]
    return []


def body_file(req: Request, value: str) -> str | None:
    if value == "-":
        if req.stdin:
            return text_of(Path(req.cwd or ".") / req.stdin)
        return req.raw if "<<" in req.raw else None
    candidates = (Path(req.cwd or ".") / part for part in dict.fromkeys((value, value.split("=", 1)[-1])))
    return next((text for path in candidates if path.is_file() and (text := text_of(path)) is not None), None)


def body_text(req: Request, view: dict) -> str | None:
    if not (req.bodies or req.texts):
        return str(view.get("body") or "")
    files = [body_file(req, value) for value in req.bodies]
    return None if None in files else "\n".join([*req.texts, *files])


def answer_texts(evt: BaseHookEvent, req: Request, ids: list[str]) -> dict[str, str] | None:
    if not ids:
        return {}
    if (entry := drive_entry(evt)) is None:
        return None
    found = {}
    for answer in ids:
        done = run(["ccn", "-R", entry["checkout"], "answer", "show", answer, "--json"], req.cwd)
        if done is None or done.returncode not in (0, CCN_NOT_FOUND):
            return None
        if done.returncode == CCN_NOT_FOUND:
            continue
        if (record := parsed(done.stdout)) is None:
            return None
        if record.get("id") and not record.get("superseded_by"):
            found[answer] = f"{record.get('title') or ''}\n{record.get('body') or ''}"
    return found


def mentions(text: str, name: str) -> bool:
    return bool(re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text, re.IGNORECASE))


def plan_items(evt: BaseHookEvent) -> dict[str, str]:
    path = CompactionState.load(evt).plan_path
    text = text_of(Path(path).expanduser()) if path else None
    items: dict[str, str] = {}
    step = key = None
    for line in (text or "").splitlines():
        if heading := PLAN_STEP.match(line):
            step, key = heading["step"], None
        elif line.startswith("#"):
            step = key = None
        elif (dotted := PLAN_DOTTED.match(line)) or (step and (dotted := PLAN_NUMBERED.match(line))):
            key = f"{dotted.groupdict().get('step') or step}.{dotted['item']}"
            items[key] = line
        elif key and (not line.strip() or line[:1].isspace()):
            items[key] += "\n" + line
        else:
            key = None
    return items


def plan_refs(item: str) -> list[str]:
    return [f"{ref['step']}.{ref['item']}" for ref in PLAN_REF.finditer(item)]


def coverage(finding: Finding, items: list[str], answers: dict[str, str], plan: dict[str, str]) -> str:
    naming = [item for item in items if mentions(item, finding.name)]
    cited = [answers[answer] for item in naming for answer in ANSWER_ID.findall(item) if answer in answers]
    if any(mentions(text, finding.name) for text in cited):
        return "named"
    approved = [item for item in naming if any(mentions(answers[answer], "plan") for answer in ANSWER_ID.findall(item) if answer in answers)]
    if any(mentions(plan.get(ref, ""), finding.name) for item in approved for ref in plan_refs(item)):
        return "named"
    return "cited" if cited else "open"


def evidence(findings: list[Finding], rulings: str, plan: dict[str, str]) -> str:
    shown = [finding.render() for finding in findings[:SHOWN]]
    more = [f"... {len(findings) - SHOWN} more"] if len(findings) > SHOWN else []
    cited = rulings.strip()[:RULINGS_SHOWN] or "no ## Rulings section"
    quoted = [f"plan {ref}: {plan[ref].strip()[:PLAN_SHOWN]}" for ref in dict.fromkeys(plan_refs(rulings)) if ref in plan]
    return "\n".join(["```", *shown, *more, "", cited, *quoted, "```"])


def verdict(evt: BaseHookEvent, req: Request) -> HookResult | None:
    view = pr_view(req) if req.edit is None and (req.base is None or not (req.bodies or req.texts)) else {}
    if view is None and not (req.bodies or req.texts):
        return None
    view = view or {}
    findings = new_findings(req, view)
    if not findings or (body := body_text(req, view)) is None:
        return None
    rulings = found.group(0) if (found := RULINGS.search(body)) else ""
    if (answers := answer_texts(evt, req, list(dict.fromkeys(ANSWER_ID.findall(rulings))))) is None:
        return None
    items = RULING_ITEM.split(rulings)
    plan = plan_items(evt) if PLAN_REF.search(rulings) else {}
    states = {finding: coverage(finding, items, answers, plan) for finding in findings}
    open_findings = [finding for finding in findings if states[finding] != "named"]
    if not open_findings:
        return None
    if any(finding.definite and states[finding] == "open" for finding in open_findings):
        return evt.block(f"{UNCITED_KINDS}\n\n{evidence(open_findings, rulings, plan)}")
    return evt.block(f"{UNNAMED_KINDS}\n\n{evidence(open_findings, rulings, plan)}", confirm=NAMES_IT)


def ship(command: str, **extra: object) -> Input:
    return Input(**({"command": command, "agent_id": LANE, "state": ACTIVE, "env": DRIVE_ENV, "cwd": str(TREE)} | extra))


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        ship(f"ccx vcs ship -m 'long-running: page a stalled launch' --pr-body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Block(
            pattern=r"^A drive lane's PR may not add a refusal kind[\s\S]*desk-runner\.py:369 refusal kind LAUNCH-STALLED[\s\S]*STALL_SECONDS"
        ),
        ship(f"ccx vcs ship --pr-body-file {CITED}", commands=sources(LAUNCH_STALLED, answer=NAMED_ANSWER)): Allow(),
        ship(f"ccx vcs ship --pr-body-file p1-hooks/stall={CITED}", commands=sources(LAUNCH_STALLED, answer=NAMED_ANSWER)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {CITED}", commands=sources(LAUNCH_STALLED), decide=CONFIRMED): Block(pattern=r"needs an owner answer that names it"),
        ship(f"ccx vcs ship --pr-body-file {CITED}", commands=sources(LAUNCH_STALLED), decide=UNCONFIRMED): Allow(),
        ship(f"ccx vcs ship --pr-body-file {CITED}", commands=sources(LAUNCH_STALLED), state=PLANNED): Allow(),
        ship(f"ccx vcs ship --pr-body-file {CITED}", commands=sources(LAUNCH_STALLED, answer=SENTINEL_ANSWER), state=PLANNED, decide=CONFIRMED): Block(),
        ship(PLAN_GATES_BODY, commands=sources(LAUNCH_STALLED), state=PLANNED, decide=CONFIRMED): Block(
            pattern=r"needs an owner answer that names it[\s\S]*plan 1\.2: 2\. \*\*Landing gates"
        ),
        ship(f"ccx vcs ship --pr-body-file {CITED}", commands=sources(LAUNCH_STALLED, answer="not json")): Allow(),
        ship(f"ccx vcs ship --pr-body-file {CITED}", commands=sources(LAUNCH_STALLED, answer=NAMED_ANSWER[:-1] + ', "superseded_by": ["b7c0d2e900"]}')): Block(
            pattern=r"cites an owner answer"
        ),
        ship(f"gh pr edit 1 --body-file - < {CITED}", commands=sources(LAUNCH_STALLED, answer=NAMED_ANSWER)): Allow(),
        ship(f"gh pr edit 1 --body-file - < {UNCITED}", commands=sources(LAUNCH_STALLED, answer=NAMED_ANSWER)): Block(pattern=r"cites an owner answer"),
        ship(f"ccx vcs ship --pr-body-file {CITED}", commands=sources(LAUNCH_STALLED, answer="")): Block(pattern=r"cites an owner answer"),
        ship(f"ccx vcs ship --pr-body-file {CITED}", commands=sources(LAUNCH_STALLED, answer=(3, "not-found: entity not found"))): Block(
            pattern=r"cites an owner answer"
        ),
        ship(f"ccx vcs ship --pr-body-file {CITED}", commands=sources(LAUNCH_STALLED, answer=(2, "cc-notes: not a git repository"))): Allow(),
        ship(HEREDOC_BODY, commands=sources(LAUNCH_STALLED, answer=NAMED_ANSWER)): Allow(),
        ship(DIGIT_ID_BODY, commands=sources(LAUNCH_STALLED, answer=NAMED_ANSWER)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNNAMED}", commands=sources(UPLOAD_BUDGET)): Block(
            pattern=r"cites an owner answer[\s\S]*upload\.go:41 clock uploadBudget[\s\S]*upload\.go:44 clock ticker"
        ),
        ship(f"ccx vcs ship --pr-body-file {UNNAMED}", commands=sources(LAUNCH_STALLED)): Block(pattern=r"cites an owner answer naming it"),
        ship(SENTINEL_BODY, commands=sources(SENTINEL_ONLY, answer=SENTINEL_ANSWER), decide=CONFIRMED): Block(pattern=r"view\.go:11 .* errReadOnly"),
        ship(SENTINEL_BODY.replace("errReadOnlyView", "errReadOnly"), commands=sources(SENTINEL_ONLY, answer=SENTINEL_ANSWER.replace("errReadOnlyView", "errReadOnly"))): Allow(),
        ship(f"ccx vcs ship -m 'drive: digest' plugins/long-running/skills/long-running/scripts/drive.py --pr-body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Allow(),
        ship(f"ccx vcs ship -m 'runner: page' plugins/long-running/skills/long-running/scripts --pr-body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Block(),
        ship("ccx vcs ship -m 'long-running: page a stalled launch'", commands=sources(LAUNCH_STALLED, pr="not json")): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(LOGGED_STATE), decide=UNCONFIRMED): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(LOGGED_STATE), decide=CONFIRMED): Block(pattern=r"UPLOAD-STARTED"),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}.missing", commands=sources(LAUNCH_STALLED)): Allow(),
        ship(f"gh pr edit 1 --body-file {CITED} && gh pr edit 2 --body-file {UNCITED}", commands=sources(LAUNCH_STALLED, answer=NAMED_ANSWER)): Block(
            pattern=r"cites an owner answer"
        ),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(UPLOAD_BUDGET)): Block(pattern=r"cites an owner answer"),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(RETIRE_AND_RECONVERT), decide=CONFIRMED): Block(
            pattern=r"open\.go:\d+ refusal or retry"
        ),
        ship(f"ccx vcs ship --pr-body-file {UNNAMED}", commands=sources(RETIRE_AND_RECONVERT), decide=UNCONFIRMED): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(TEST_ONLY)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(CARD_TERMS), decide=CONFIRMED): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(CARD_TERMS_KIND), decide=CONFIRMED): Block(
            pattern=r"\A(?![\s\S]*card_terms\.py:1[34] )[\s\S]*card_terms\.py:3 refusal kind CARD-STALE"
        ),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(CARD_TERMS), cwd=str(TREE / "hooks"), decide=CONFIRMED): Allow(),
        ship(
            f"gh pr create --base main --title 'card terms' --body-file {UNCITED}",
            commands=sources(CARD_TERMS, show=("HEAD:hooks/card_terms.py", CARD_TERMS_SOURCE)),
            decide=CONFIRMED,
        ): Allow(),
        ship(
            f"gh pr create --base main --head card-terms --title 'card terms' --body-file {UNCITED}",
            commands=sources(CARD_TERMS, show=("card-terms:hooks/card_terms.py", CARD_TERMS_SOURCE)),
            decide=CONFIRMED,
        ): Allow(),
        ship(
            f"gh pr edit 7 --body-file {UNCITED}", commands=sources(CARD_TERMS, pr="9f1c2e3\n", show=("9f1c2e3:hooks/card_terms.py", CARD_TERMS_SOURCE)), decide=CONFIRMED
        ): Allow(),
        ship(
            f"gh pr edit 7 --body-file {UNCITED}", commands=sources(CARD_TERMS, pr="9f1c2e3\n", show=("HEAD:hooks/card_terms.py", CARD_TERMS_SOURCE)), decide=CONFIRMED
        ): Block(pattern=r"card_terms\.py:13 refusal kind FINDER-PENDING"),
        ship(f"gh pr create --base main --title 'card terms' --body-file {UNCITED}", commands=sources(CARD_TERMS), decide=CONFIRMED): Block(
            pattern=r"card_terms\.py:13 refusal kind FINDER-PENDING"
        ),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(PLAIN)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(RELISTED)): Block(
            pattern=r"\A(?![\s\S]*refusal kind (?:UNBOUND|LAUNCH-STALLED))[\s\S]*refusal kind LAUNCH-DUPLICATE"
        ),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(FAILS_OPEN), decide=CONFIRMED): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(RETIRE_AND_RECONVERT), decide=CONFIRMED): Block(pattern=r"open\.go:127 .* errReadOnly"),
        ship(f"ccx vcs ship --no-pr --pr-body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Allow(),
        ship("ccx vcs ship -m 'long-running: page a stalled launch'", commands=sources(LAUNCH_STALLED, pr=(1, "GraphQL: API rate limit exceeded"))): Allow(),
        ship("ccx vcs ship -m 'long-running: page a stalled launch'", commands=sources(LAUNCH_STALLED, pr='{"baseRefName": "main", "body": ""}')): Block(),
        ship(f"gh pr create --base main --title 'page a stalled launch' --body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Block(
            pattern=r"LAUNCH-STALLED"
        ),
        ship(f"gh pr edit 101 -R acme/service --body-file {UNCITED}", commands=sources(RETIRE_AND_RECONVERT), decide=CONFIRMED): Block(),
        ship("gh pr edit 101 --add-label docs", commands=sources(RETIRE_AND_RECONVERT), decide=CONFIRMED): Allow(),
        ship(f"git diff && echo {UNCITED}", commands=sources(LAUNCH_STALLED)): Allow(),
        Input(command=f"ccx vcs ship --pr-body-file {UNCITED}", env={"CLAUDE_LONG_RUNNING_LANE": "p1-hooks"}, commands=sources(LAUNCH_STALLED)): Block(),
        Input(command=f"ccx vcs ship --pr-body-file {UNCITED}", agent_id=LANE, commands=sources(LAUNCH_STALLED)): Allow(),
        Input(command=f"ccx vcs ship --pr-body-file {UNNAMED}", env=NO_DRIVE, commands=sources(LAUNCH_STALLED)): Allow(),
        Input(command=f"ccx vcs ship --pr-body-file {UNCITED}", state=ACTIVE, commands=sources(LAUNCH_STALLED)): Allow(),
        Input(command=f"ccx vcs ship --pr-body-file {UNCITED}", agent_id="alanding-desk-0123456789abcdef", state=ACTIVE, commands=sources(LAUNCH_STALLED)): Allow(),
    },
)
def refusal_kinds_need_owner_answer(evt: BaseHookEvent) -> HookResult | None:
    return next((result for req in lane_prs(evt) if (result := verdict(evt, req)) is not None), None)
