from __future__ import annotations

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
    CITED,
    HEREDOC_BODY,
    LAUNCH_STALLED,
    LOGGED_STATE,
    NO_DRIVE,
    PLAIN,
    RETIRE_AND_RECONVERT,
    TEST_ONLY,
    UNCITED,
    UNNAMED,
    UPLOAD_BUDGET,
    sources,
)

ACTIVE = [CompactionState(active=True)]
LANE = "alane-a-0123456789abcdef"
READ_TIMEOUT_SECONDS = 20
SHOWN = 12
RULINGS_SHOWN = 1500
TEXT_SHOWN = 160
SHIP_BODY = ("--pr-body-file",)
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
UNTRACKED = ["git", "ls-files", "--others", "--exclude-standard", "-z"]
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
    r"""\breturn\b[^\n]*\berr[A-Z]\w*|["']cause:\w+|\b(?P<name>refus\w*|reject\w*|quarantin\w*|re(?:convert|attempt|queue|dispatch)\w*|read-only|fallback)\b""",
    re.IGNORECASE,
)
ASSIGNED = re.compile(r"""^\s*(?:(?:const|var|let|export|final)\s+)*["']?(?P<name>[A-Za-z_]\w+)["']?\s*(?::[^=\n]*)?(?::=|=|:)(?!=)""")
RULINGS = re.compile(r"^##\s+Rulings\b.*?(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
UNREADABLE = ""
CCN_NOT_FOUND = 3
DECLARING = frozenset(":,]}")
ANSWER_ID = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
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


def run(argv: list[str], cwd: Path | None) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=READ_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None


def read(argv: list[str], cwd: Path | None) -> str | None:
    done = run(argv, cwd)
    return done.stdout if done is not None and done.returncode == 0 else None


def parsed(text: str | None) -> dict:
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def text_of(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return None


def values(args: tuple[str, ...], flags: tuple[str, ...]) -> list[str]:
    found = [value for flag, value in zip(args, args[1:], strict=False) if flag in flags]
    return found + [arg.split("=", 1)[1] for arg in args for flag in flags if flag.startswith("--") and arg.startswith(f"{flag}=")]


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


def request(evt: BaseHookEvent, call: Call) -> Request | None:
    cwd = call.cwd or evt.cwd
    if call.name == "ccx" and call.args[:2] == ("vcs", "ship") and "--no-pr" not in call.args:
        parent = values(call.args, ("--parent",))
        return Request(str(evt.command), cwd, (), parent[0] if parent else None, tuple(values(call.args, SHIP_BODY)), (), None, True)
    argv = without_repo((call.name, *call.args)) if call.name == "gh" else ()
    repo = ("-R", found) if (found := named_repo(call)) else ()
    if argv[:3] == ("gh", "pr", "create"):
        base = values(argv, ("--base", "-B"))
        return Request(str(evt.command), cwd, repo, base[0] if base else None, tuple(values(argv, GH_BODY_FILE)), tuple(values(argv, GH_BODY)), None, False)
    if argv[:3] == ("gh", "pr", "edit") and (values(argv, GH_BODY) or values(argv, GH_BODY_FILE)):
        return Request(str(evt.command), cwd, repo, None, tuple(values(argv, GH_BODY_FILE)), tuple(values(argv, GH_BODY)), selector(argv[3:]), False)
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


def untracked(cwd: Path | None) -> Iterator[tuple[str, int, str]]:
    for name in filter(None, (read(UNTRACKED, cwd) or "").split("\0")):
        if not EXEMPT.search(name) and (content := text_of(Path(cwd or ".") / name)) is not None:
            yield from ((name, number, text) for number, text in enumerate(content.splitlines(), start=1))


def pr_view(req: Request) -> dict:
    return parsed(read(["gh", "pr", "view", *req.gh, "--json", "baseRefName,body"], req.cwd))


def diff_lines(req: Request, view: dict) -> Iterator[tuple[str, int, str]]:
    if req.edit is not None:
        yield from added(read(["gh", "pr", "diff", *req.edit, *req.gh], req.cwd) or "")
        return
    base = f"origin/{branch}" if (branch := req.base or view.get("baseRefName")) else (read(TRUNK, req.cwd) or "").strip()
    if not base:
        return
    span = ["--merge-base", base] if req.worktree else [f"{base}...HEAD"]
    yield from added(read(["git", "diff", "--no-color", "--no-ext-diff", "--unified=0", *span], req.cwd) or "")
    if req.worktree:
        yield from untracked(req.cwd)


def named(text: str, match: re.Match[str]) -> str:
    return found["name"] if (found := ASSIGNED.match(text)) else (match.group("name") or match.group(0)).strip("\"'` ")


def kind_of(name: str) -> str:
    return "quarantine kind" if "quarantin" in name.casefold() else "refusal kind"


def declared(text: str, match: re.Match[str]) -> bool:
    return text[match.end() :].lstrip()[:1] in DECLARING or bool(ASSIGNED.match(text))


def classify(path: str, line: int, text: str) -> list[Finding]:
    if EXEMPT.search(path) or COMMENT.match(text):
        return []
    if kinds := [Finding(kind_of(match["name"]), match["name"], path, line, text, declared(text, match)) for match in KIND.finditer(text)]:
        return kinds
    if match := REFUSAL_NAME.search(text):
        return [Finding(kind_of(match["name"]), match["name"], path, line, text, True)]
    for kind, pattern in (("retry", RETRY), ("clock", CLOCK), ("budget", BUDGET)):
        if match := pattern.search(text):
            return [Finding(kind, named(text, match), path, line, text, True)]
    if match := REFUSAL_HINT.search(text):
        return [Finding("refusal or retry", named(text, match), path, line, text, False)]
    return []


def body_file(req: Request, value: str) -> str | None:
    if value == "-":
        return req.raw
    candidates = (Path(req.cwd or ".") / part for part in dict.fromkeys((value, value.split("=", 1)[-1])))
    return next((text for path in candidates if path.is_file() and (text := text_of(path)) is not None), None)


def body_text(req: Request, view: dict) -> str | None:
    if not (req.bodies or req.texts):
        return str(view.get("body") or "")
    files = [body_file(req, value) for value in req.bodies]
    return None if None in files else "\n".join([*req.texts, *files])


def owner_answer(evt: BaseHookEvent, req: Request, rulings: str) -> str | None:
    if (entry := drive_entry(evt)) is None:
        return UNREADABLE
    for answer in dict.fromkeys(ANSWER_ID.findall(rulings)):
        done = run(["ccn", "-R", entry["checkout"], "answer", "show", answer, "--json"], req.cwd)
        if done is None or done.returncode not in (0, CCN_NOT_FOUND):
            return UNREADABLE
        if done.returncode == 0 and parsed(done.stdout).get("id"):
            return answer
    return None


def evidence(findings: list[Finding], rulings: str) -> str:
    shown = [finding.render() for finding in findings[:SHOWN]]
    more = [f"... {len(findings) - SHOWN} more"] if len(findings) > SHOWN else []
    cited = rulings.strip()[:RULINGS_SHOWN] or "no ## Rulings section"
    return "\n".join(["```", *shown, *more, "", cited, "```"])


def verdict(evt: BaseHookEvent, req: Request) -> HookResult | None:
    view = pr_view(req) if req.edit is None and (req.base is None or not (req.bodies or req.texts)) else {}
    findings = [finding for path, line, text in diff_lines(req, view) for finding in classify(path, line, text)]
    if not findings or (body := body_text(req, view)) is None:
        return None
    rulings = found.group(0) if (found := RULINGS.search(body)) else ""
    if (answer := owner_answer(evt, req, rulings) if rulings else None) == UNREADABLE:
        return None
    folded = rulings.casefold()
    open_findings = [finding for finding in findings if not (answer and finding.name.casefold() in folded)]
    if not open_findings:
        return None
    if answer is None and any(finding.definite for finding in open_findings):
        return evt.block(f"{UNCITED_KINDS}\n\n{evidence(open_findings, rulings)}")
    return evt.block(f"{UNNAMED_KINDS}\n\n{evidence(open_findings, rulings)}", confirm=NAMES_IT)


def ship(command: str, **extra: object) -> Input:
    return Input(command=command, agent_id=LANE, state=ACTIVE, env=DRIVE_ENV, **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        ship(f"ccx vcs ship -m 'long-running: page a stalled launch' --pr-body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Block(
            pattern=r"^A drive lane's PR may not add a refusal kind[\s\S]*desk-runner\.py:369 refusal kind LAUNCH-STALLED[\s\S]*STALL_SECONDS"
        ),
        ship(f"ccx vcs ship --pr-body-file {CITED}", commands=sources(LAUNCH_STALLED)): Allow(),
        ship(f"ccx vcs ship --pr-body-file p1-hooks/stall={CITED}", commands=sources(LAUNCH_STALLED)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {CITED}", commands=sources(LAUNCH_STALLED, answer="")): Block(pattern=r"cites an owner answer"),
        ship(HEREDOC_BODY, commands=sources(LAUNCH_STALLED)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNNAMED}", commands=sources(UPLOAD_BUDGET), decide=CONFIRMED): Block(
            pattern=r"upload\.go:41 clock uploadBudget[\s\S]*upload\.go:44 clock ticker"
        ),
        ship(f"ccx vcs ship --pr-body-file {UNNAMED}", commands=sources(UPLOAD_BUDGET), decide=UNCONFIRMED): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNNAMED}", commands=sources(LAUNCH_STALLED), decide=CONFIRMED): Block(pattern=r"needs an owner answer that names it"),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(LOGGED_STATE), decide=UNCONFIRMED): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(LOGGED_STATE), decide=CONFIRMED): Block(pattern=r"UPLOAD-STARTED"),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}.missing", commands=sources(LAUNCH_STALLED)): Allow(),
        ship(f"gh pr edit 1 --body-file {CITED} && gh pr edit 2 --body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Block(pattern=r"cites an owner answer"),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(UPLOAD_BUDGET)): Block(pattern=r"cites an owner answer"),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(RETIRE_AND_RECONVERT), decide=CONFIRMED): Block(
            pattern=r"values\.go:\d+ refusal or retry"
        ),
        ship(f"ccx vcs ship --pr-body-file {UNNAMED}", commands=sources(RETIRE_AND_RECONVERT), decide=UNCONFIRMED): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(TEST_ONLY)): Allow(),
        ship(f"ccx vcs ship --pr-body-file {UNCITED}", commands=sources(PLAIN)): Allow(),
        ship(f"ccx vcs ship --no-pr --pr-body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Allow(),
        ship("ccx vcs ship -m 'long-running: page a stalled launch'", commands=sources(LAUNCH_STALLED, pr='{"baseRefName": "main", "body": ""}')): Block(),
        ship(f"gh pr create --base main --title 'page a stalled launch' --body-file {UNCITED}", commands=sources(LAUNCH_STALLED)): Block(
            pattern=r"LAUNCH-STALLED"
        ),
        ship(f"gh pr edit 41464 -R Forge-AI/monorepo --body-file {UNCITED}", commands=sources(RETIRE_AND_RECONVERT), decide=CONFIRMED): Block(),
        ship("gh pr edit 41464 --add-label docs", commands=sources(RETIRE_AND_RECONVERT), decide=CONFIRMED): Allow(),
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
