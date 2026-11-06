from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache, partial
from pathlib import Path

from captain_hook import Allow, Annotated, BaseHookEvent, Block, Call, Event, HookResult, Input, Or, Runs, Tool, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import Bound, drive_entry, run_script, script
from .lane_landing import outside_drive_repo
from .pr_body_frozen import SHIP as SHIP_ARGS
from .session_tree import drive_lane, teammate_lane
from .tests.ledger_fixtures import FIXTURES

OPENERS = (
    ("ccx", "vcs", "ship"),
    ("ccx", "vcs", "stack", "submit"),
    ("ccx", "vcs", "stack", "continue"),
    ("gt", "submit"),
    ("gt", "ss"),
    ("gh", "pr", "create"),
)
URL = r"https://(?:github\.com/(?P<gh>[\w.-]+/[\w.-]+)/pull|app\.graphite\.com/github/pr/(?P<gt>[\w.-]+/[\w.-]+))/(?P<url_pr>\d+)"
SUBMITTED = (
    re.compile(rf"(?:(?:submitted|landed) \S+ → PR|(?:opened|updated) PR) #(?P<pr>\d+)(?: {URL})?"),
    re.compile(rf"^\S+: {URL} \((?:created|updated)\)$", re.MULTILINE),
    re.compile(rf"^{URL}$", re.MULTILINE),
)
SETTLERS = (("cci", "post"), ("gh", "pr", "close"))
PULL_URL = re.compile(r"/pull/(?P<pr>\d+)")
STACK_HEAD = re.compile(r"^(?P<branch>\S+) · #(?P<pr>\d+) · head (?P<head>[0-9a-f]{7,40}) · (?:parent (?P<base>\S+))?", re.MULTILINE)
RESTACKED = re.compile(r'^(?P<branch>\S+) · onto (?P<base>\S+)(?: \(was \S+\))? · .*#(?P<pr>\d+) "(?P<title>.*)"$', re.MULTILINE)
PUBLISHED = re.compile(r"\bpublished (?P<head>[0-9a-f]{7,40})\b")
RECORD_TIMEOUT_SECONDS = 15
SHIP = ("ccx", "vcs", "ship")
SHIP_BODY = "--pr-body-file"
SHIP_OPENS_NOTHING = frozenset({"--no-pr", "--no-push", "--dry-run", "--help", "-h"})
SHIP_NEW_PR = ("--new-branch", "--create", "--new-pr")
NO_PR = "no pull requests found"
PR_VIEW = "url,title,headRefName,baseRefName,headRefOid,body,state"
EDITED_BODY = ("--body", "-b", "--body-file", "-F")
SEEN_RECORDS = "10"
SEEN_BUDGET = str(1 << 20)
ID_HASH_CHARS = 12


@dataclass(frozen=True)
class OpenedPr:
    number: str
    repo: str | None = None
    head: str | None = None
    branch: str | None = None
    base: str | None = None
    title: str | None = None

    @property
    def spec(self) -> str:
        return f"{self.repo + '#' if self.repo else ''}{self.number}{'=' + self.head if self.head else ''}"


def response_text(response: object) -> str:
    match response:
        case str():
            return response
        case {"stdout": stdout, **rest}:
            return f"{stdout}\n{rest.get('stderr') or ''}"
        case _:
            return ""


def opened_prs(output: str) -> list[OpenedPr]:
    submitted = [match for pattern in SUBMITTED for match in pattern.finditer(output)]
    repos = {match.groupdict().get("pr") or match["url_pr"]: match["gh"] or match["gt"] for match in submitted}
    stack = list(STACK_HEAD.finditer(output))
    heads = {match["pr"]: match["head"] for match in stack}
    restacked = list(RESTACKED.finditer(output))
    branches = {match["pr"]: match["branch"] for match in (*stack, *restacked)}
    bases = {match["pr"]: match["base"] for match in (*stack, *restacked) if match["base"]}
    titles = {match["pr"]: match["title"] for match in restacked}
    if len(repos) == 1 and (published := PUBLISHED.findall(output)):
        heads.setdefault(next(iter(repos)), published[-1])
    numbers = dict.fromkeys([*heads, *repos])
    return [OpenedPr(number, repos.get(number), heads.get(number), branches.get(number), bases.get(number), titles.get(number)) for number in numbers]


def opener_cwd(evt: BaseHookEvent) -> Path | None:
    calls = (call for call in evt.cmd.calls() if any((call.name, *call.args)[: len(argv)] == argv for argv in OPENERS))
    return next((call.cwd for call in calls), evt.cwd)


def lane_name(evt: BaseHookEvent) -> str:
    return teammate_lane(evt) or drive_lane(evt) or ROOT_LANE


ROOT_LANE = "root"
UNNAMED = f"No named lane ran this ship, so its PRs went in the drive ledger under `root`. Move each to its lane with `{sys.executable} {script('ledger.py')} reassign --ledger <id> --pr <n> --lane <lane>`."
UNRECORDED = "The opened PRs were not recorded in the drive ledger: {reason}. Rerun `{retry}`."
UNSETTLED = (
    "The landed or closed PRs {prs} were not settled in the drive ledger: {reason}. "
    "Rerun `{retry}`; it reads the ledger id and checkout from the drive registry."
)
LANDED = f"{sys.executable} {script('drive.py')} landed"
UNPOSTED = "#{number} has no fresh row in the landing store: {reason}. It stays out of Waiting on you until `ccx vcs ship --no-commit --pr-body-file <body>` posts it."
SHIP_NEEDS_BODY = "This ship opens a PR, and a drive PR's row takes its card from the body: write the body first and pass `--pr-body-file <path>`. `--no-pr` or `--no-push` opens nothing."
SHIP_CLIPPED = "This ship's output was cut before its PR line, so the PR has no board row and no head record. Rerun `ccx vcs ship --no-commit --pr-body-file <body>` with no pipe."
DETACHED_LOG = Path.home() / ".cache" / "ccn-ledger" / "detached-drive.log"
LANE_ENV = {"CLAUDE_LONG_RUNNING_DRIVE": "d", "CLAUDE_LONG_RUNNING_LANE": "lane-a"}
ON_BRANCH = {"git rev-parse --abbrev-ref HEAD": "lane/fix\n"}
NOT_OPEN = ON_BRANCH | {"gh pr view": (1, 'no pull requests found for branch "lane/fix"')}
OPEN_PR = ON_BRANCH | {"gh pr view": "OPEN\n"}
INFERRED_TRUNK = ON_BRANCH | {"gh pr view --json": (1, 'no pull requests found for branch "dev"'), "gh pr view lane/fix": "OPEN\n", "gh pr view dev": (1, 'no pull requests found for branch "dev"')}


def run_drive(argv: list[str], *, detach_on_timeout: bool = False) -> tuple[subprocess.CompletedProcess[str] | None, str]:
    try:
        done = run_script(argv, RECORD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return finish_detached(argv) if detach_on_timeout else (None, f"drive.py timed out after {RECORD_TIMEOUT_SECONDS}s")
    except OSError as failure:
        return None, str(failure)
    if not done.returncode:
        return done, ""
    return done, (done.stderr.strip().splitlines() or [f"drive.py exited {done.returncode}"])[-1]


def finish_detached(argv: list[str]) -> tuple[None, str]:
    try:
        DETACHED_LOG.parent.mkdir(parents=True, exist_ok=True)
        with DETACHED_LOG.open("a") as log:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    except OSError as failure:
        return None, str(failure)
    return None, ""


class Unposted(Exception):
    pass


def tool(argv: list[str], cwd: Path | None, stdin: str | None = None) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=RECORD_TIMEOUT_SECONDS, cwd=cwd, input=stdin, env=dict(reqenv.env_map()))
    except (OSError, subprocess.TimeoutExpired) as failure:
        raise Unposted(f"`{' '.join(argv[:3])}`: {failure}") from failure
    if done.returncode:
        raise Unposted(f"`{' '.join(argv[:3])}` exited {done.returncode}: {(done.stderr or done.stdout).strip()}")
    return done.stdout


def pr_view(pr: OpenedPr, cwd: Path | None) -> dict[str, str]:
    return json.loads(tool(["gh", "pr", "view", pr.number, *(("-R", pr.repo) if pr.repo else ()), "--json", PR_VIEW], cwd))


def full_commit(rev: str, cwd: Path | None) -> str | None:
    try:
        return tool(["git", "rev-parse", "--verify", f"{rev}^{{commit}}"], cwd).strip()
    except Unposted:
        return None


def trunk(cwd: Path | None) -> str | None:
    try:
        return tool(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd).strip().removeprefix("origin/")
    except Unposted:
        return None


def drive_flag() -> list[str]:
    return ["--drive", drive] if (drive := reqenv.getenv("CLAUDE_LONG_RUNNING_DRIVE")) else []


def has_row(ref: str, cwd: Path | None) -> bool:
    lines = tool(["cci", "grep", ".", *drive_flag(), "--json", "--kind", "opened", "--pr", ref, "-n", SEEN_RECORDS, "--budget", SEEN_BUDGET], cwd).splitlines()
    return any(json.loads(line).get("fields", {}).get("attrs", {}).get("repo") for line in lines if line.startswith("{"))


def post_row_record(pr: OpenedPr, body: str | None, cwd: Path | None, lane: str, *, edited: bool = False) -> None:
    viewed = cache(partial(pr_view, pr, cwd))
    repo = pr.repo or "/".join(viewed()["url"].split("/")[3:5])
    ref = f"{repo}#{pr.number}"
    fresh = not has_row(ref, cwd)
    if fresh and (edited or (not pr.branch and viewed()["state"] != "OPEN")):
        return
    branch = pr.branch or viewed()["headRefName"]
    base = pr.base or viewed()["baseRefName"]
    head = viewed()["headRefOid"] if edited else full_commit(pr.head or f"refs/heads/{branch}", cwd) or viewed()["headRefOid"]
    if body is None and (fresh or edited):
        body = viewed()["body"]
    title = pr.title or (viewed()["title"] if fresh else None)
    attrs = {"repo": repo, "branch": branch, "base": base, "head": head} | ({} if base == trunk(cwd) else {"parent": base}) | ({"title": title} if title else {})
    words = ["--kind", "opened", "--id", f"row-opened:{ref}", "--text", f"{ref} on {branch}"]
    if not fresh:
        said = "" if body is None else hashlib.sha256(body.encode()).hexdigest()[:ID_HASH_CHARS]
        words = ["--kind", "head", "--id", f"row-head:{ref}:{head}:{said}", "--head", f"{ref}={head}"]
    argv = ["cci", "post", *drive_flag(), "--lane", lane, *words, "--pr", ref, *(arg for key, value in attrs.items() for arg in ("--attr", f"{key}={value}"))]
    tool([*argv, "--body-file", "-"] if body is not None else argv, cwd, None if body is None else json.dumps({"body": body}))


def ship_calls(evt: BaseHookEvent) -> Iterator[Call]:
    return (call for call in evt.cmd.calls() if (call.name, *call.args)[: len(SHIP)] == SHIP)


def named_bodies(evt: BaseHookEvent) -> dict[str, str]:
    values = (value for call in ship_calls(evt) for value in flag_values(call.args, SHIP_BODY))
    return {branch if path else "": path or branch for branch, _, path in (value.partition("=") for value in values)}


def body_text(path: str | None, cwd: Path | None) -> str | None:
    if not path or path == "-":
        return None
    try:
        return Path(cwd or ".", path).expanduser().read_text()
    except (OSError, ValueError):
        return None


def post_row_records(evt: BaseHookEvent, prs: list[OpenedPr]) -> list[str]:
    cwd, lane, bodies, failed = opener_cwd(evt), lane_name(evt), named_bodies(evt), []
    for pr in prs:
        path = (pr.branch and bodies.get(pr.branch)) or (bodies.get("") if pr is prs[-1] else None)
        try:
            post_row_record(pr, body_text(path, cwd), cwd, lane)
        except (Unposted, ValueError, KeyError) as failure:
            failed.append(UNPOSTED.format(number=pr.number, reason=failure))
    return failed


def ledger_note(evt: BaseHookEvent, prs: list[OpenedPr]) -> str | None:
    argv = [sys.executable, str(script("drive.py")), "record", "--session", evt.session_id, "--lane", lane_name(evt), "--cwd", str(opener_cwd(evt)), *drive_flag()]
    for pr in prs:
        argv += ["--pr", pr.spec]
    done, reason = run_drive(argv, detach_on_timeout=True)
    if reason:
        return UNRECORDED.format(reason=reason, retry=shlex.join(argv))
    if done is None:
        return None
    if done.stdout.strip():
        return UNNAMED if evt.agent_id and not drive_lane(evt) else None
    return done.stderr.strip() if done.stderr.strip() and reqenv.getenv("CLAUDE_LONG_RUNNING_LANE") else None


@on(
    Event.PostToolUse,
    only_if=[Bound(),
        Tool("Bash"),
        Or(*(Runs(*argv) for argv in OPENERS)),
    ],
    tests={
        Input(
            command="ccx vcs ship -m 'ci: ✨ x'",
            output=(FIXTURES / "ccx-ship-gt.txt").read_text(),
            session_id="900424b6-0000",
            cwd="/",
            commands={f"{sys.executable} {script('drive.py')} record": "registered deploy-experience #28534"},
        ): Allow(),
        Input(
            command="ccx vcs stack submit",
            output=(FIXTURES / "ccx-stack-submit.txt").read_text(),
            session_id="5e55-0000",
            cwd="/",
            commands={f"{sys.executable} {script('drive.py')} record": ""},
        ): Allow(),
        Input(command="gh pr view 28534", output="https://github.com/Forge-AI/monorepo/pull/28534"): Allow(),
        Input(command="ccx vcs ship -m 'ci: ✨ x' --no-push", output="committed 1caa098d30 \"ci: ✨ x\""): Allow(),
        Input(command="ccx vcs ship -m 'ci: ✨ x' | tail -3", output="pushes lane/fix", env=LANE_ENV, cwd="/"): Warn(pattern=r"^This ship's output was cut"),
        Input(command="ccx vcs ship -m 'ci: ✨ x' --no-push | tail -3", output="committed 1caa098d30", env=LANE_ENV, cwd="/"): Allow(),
        Input(command="ccx vcs ship -m 'ci: ✨ x'", output="pushes lane/fix", env=LANE_ENV, cwd="/"): Allow(),
    },
)
def record_opened_prs(evt: BaseHookEvent) -> HookResult | None:
    if not (prs := opened_prs(response_text(evt.tool_response))):
        return evt.context(SHIP_CLIPPED) if any(ship_output_clipped(evt, call) for call in ship_calls(evt)) else None
    notes = [note for note in (ledger_note(evt, prs), *post_row_records(evt, prs)) if note]
    return evt.context("\n".join(notes)) if notes else None


def ship_output_clipped(evt: BaseHookEvent, call: Call) -> bool:
    return call.occurrence.piped and not SHIP_OPENS_NOTHING.intersection(call.args) and not outside_drive_repo(evt, call, drive_entry(evt))


def opens_a_pr(call: Call) -> bool:
    if flag_values(call.args, SHIP_BODY) or SHIP_OPENS_NOTHING.intersection(call.args):
        return False
    if any(arg == flag or arg.startswith(f"{flag}=") for arg in call.args for flag in SHIP_NEW_PR):
        return True
    if call.cwd is None:
        return False
    try:
        branch = next(iter(SHIP_ARGS.bind(call).values.get("branch", ())), None) or tool(["git", "rev-parse", "--abbrev-ref", "HEAD"], call.cwd).strip()
        state = tool(["gh", "pr", "view", str(branch), "--json", "state", "--jq", ".state"], call.cwd).strip()
    except Unposted as failure:
        return NO_PR in str(failure)
    return state != "OPEN"


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash"), Runs(*SHIP)],
    skip_if=[Annotated("raw")],
    tests={
        Input(command="ccx vcs ship -m 'x: ✨ y'  # ccx:raw", env=LANE_ENV, commands=NOT_OPEN): Allow(),
        Input(command="ccx vcs ship -m 'x: ✨ y'", env=LANE_ENV, cwd="/", commands=NOT_OPEN): Block(pattern=r"^This ship opens a PR"),
        Input(command=". /env.sh /lr.json; cd $A; ccx vcs ship --no-commit", env=LANE_ENV, cwd="/", commands=NOT_OPEN): Allow(),
        Input(command="ccx vcs ship -m 'x: ✨ y' --new-branch lane/fix", env=LANE_ENV, commands={"gh pr view": '{"number":9}'}): Block(pattern=r"--pr-body-file <path>"),
        Input(command="ccx vcs ship -m 'x: ✨ y' --pr-body-file /b.md", env=LANE_ENV, commands=NOT_OPEN): Allow(),
        Input(command="ccx vcs ship -m 'x: ✨ y' --no-pr", env=LANE_ENV, commands=NOT_OPEN): Allow(),
        Input(command="ccx vcs ship -m 'x: ✨ y' --no-push", env=LANE_ENV, commands=NOT_OPEN): Allow(),
        Input(command="ccx vcs ship --help", env=LANE_ENV, commands=NOT_OPEN): Allow(),
        Input(command="ccx vcs ship -m 'x: 🐛 z'", env=LANE_ENV, cwd="/", commands=OPEN_PR): Allow(),
        Input(command="ccx vcs ship -m 'x: 🐛 z'", env=LANE_ENV, cwd="/", commands=ON_BRANCH | {"gh pr view": (1, "HTTP 502: Bad Gateway")}): Allow(),
        Input(command="ccx vcs ship --no-commit --tip-only", env=LANE_ENV, cwd="/", commands=INFERRED_TRUNK): Allow(),
        Input(command="ccx vcs ship --branch lane/fix --tip-only --no-commit", env=LANE_ENV, cwd="/", commands=INFERRED_TRUNK | {"git rev-parse --abbrev-ref HEAD": "dev\n"}): Allow(),
        Input(command="ccx vcs ship --no-commit --tip-only", env=LANE_ENV, cwd="/", commands=ON_BRANCH | {"gh pr view": "MERGED\n"}): Block(pattern=r"^This ship opens a PR"),
    },
)
def ships_that_open_a_pr_name_its_body(evt: BaseHookEvent) -> HookResult | None:
    if any(opens_a_pr(call) for call in ship_calls(evt)):
        return evt.block(SHIP_NEEDS_BODY)
    return None


@on(
    Event.PostToolUse,
    only_if=[Bound(), Tool("Bash"), Runs("gh", "pr", "edit")],
    tests={
        Input(command="gh pr edit 9 --title 'x: y'", output="https://github.com/o/r/pull/9", env=LANE_ENV): Allow(),
        Input(
            command="gh pr edit 9 --body-file /b.md",
            output="https://github.com/o/r/pull/9",
            env=LANE_ENV,
            commands={"cci grep": ""},
        ): Allow(),
    },
)
def record_edited_bodies(evt: BaseHookEvent) -> HookResult | None:
    if not any(flag_values(call.args, flag) for call in evt.command.calls("gh") for flag in EDITED_BODY):
        return None
    failed = []
    for pr in opened_prs(response_text(evt.tool_response)):
        try:
            post_row_record(pr, None, opener_cwd(evt), lane_name(evt), edited=True)
        except (Unposted, ValueError, KeyError) as failure:
            failed.append(UNPOSTED.format(number=pr.number, reason=failure))
    return evt.context("\n".join(failed)) if failed else None


def flag_values(args: tuple[str, ...], name: str) -> list[str]:
    values = []
    for at, arg in enumerate(args):
        if arg == name and at + 1 < len(args):
            values.append(args[at + 1])
        elif arg.startswith(f"{name}="):
            values.append(arg.removeprefix(f"{name}="))
    return values


def closed_pr(argv: tuple[str, ...]) -> list[str]:
    target = argv[3] if len(argv) > 3 else ""
    match = PULL_URL.search(target)
    return [match["pr"] if match else target]


def settled_prs(evt: BaseHookEvent) -> list[str]:
    prs = []
    for call in evt.cmd.calls():
        argv = call.verb_argv
        if argv[:2] == SETTLERS[0] and "landed" in flag_values(call.args, "--kind"):
            prs += [pr for value in flag_values(call.args, "--pr") for pr in value.split(",")]
        elif argv[:3] == SETTLERS[1]:
            prs += closed_pr(argv)
    return list(dict.fromkeys(pr for pr in prs if pr.isdigit()))


@on(
    Event.PostToolUse,
    only_if=[Bound(),
        Tool("Bash"),
        Or(*(Runs(*argv) for argv in SETTLERS)),
    ],
    tests={
        Input(
            command="cci post --drive release-v3 --lane landing-sweep-30 --kind landed --pr 31390 --to main --text 'LANDED #31390'",
            output="#31386",
            session_id="900424b6-0000",
            commands={LANDED: "landed #31390 as f2f8393f0 on dev at 2026-10-07T03:53:00Z"},
        ): Allow(),
        Input(
            command="gh pr close https://github.com/Forge-AI/monorepo/pull/31159 --comment superseded",
            output="✓ Closed pull request Forge-AI/monorepo#31159",
            session_id="900424b6-0000",
            commands={LANDED: "#31159 is closed-without-squash on dev"},
        ): Allow(),
        Input(command="cci post --lane x --kind opened --pr 31390 --text 'OPENED #31390'", output="#31361", session_id="900424b6-0000"): Allow(),
    },
)
def settle_landed_prs(evt: BaseHookEvent) -> HookResult | None:
    if not (prs := settled_prs(evt)):
        return None
    argv = [sys.executable, str(script("drive.py")), "landed", "--session", evt.session_id]
    argv += ["--drive", drive] if (drive := reqenv.getenv("CLAUDE_LONG_RUNNING_DRIVE")) else []
    for pr in prs:
        argv += ["--pr", pr]
    _, reason = run_drive(argv)
    if reason:
        return evt.context(UNSETTLED.format(prs=", ".join(f"#{pr}" for pr in prs), reason=reason, retry=shlex.join(argv)))
    return None
