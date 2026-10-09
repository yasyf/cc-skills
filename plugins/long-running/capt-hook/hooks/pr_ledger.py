from __future__ import annotations

import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Or, Runs, Tool, on
from captain_hook.util import reqenv

from .session_tree import own_name
from .tests.ledger_fixtures import FIXTURES

DRIVE = Path(__file__).parents[2] / "skills" / "long-running" / "scripts" / "drive.py"
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
STACK_HEAD = re.compile(r"^\S+ · #(?P<pr>\d+) · head (?P<head>[0-9a-f]{7,40}) · ", re.MULTILINE)
PUBLISHED = re.compile(r"\bpublished (?P<head>[0-9a-f]{7,40})\b")
RECORD_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class OpenedPr:
    number: str
    repo: str | None = None
    head: str | None = None

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
    heads = {match["pr"]: match["head"] for match in STACK_HEAD.finditer(output)}
    if len(repos) == 1 and (published := PUBLISHED.findall(output)):
        heads.setdefault(next(iter(repos)), published[-1])
    numbers = dict.fromkeys([*heads, *repos])
    return [OpenedPr(number, repos.get(number), heads.get(number)) for number in numbers]


def opener_cwd(evt: BaseHookEvent) -> Path | None:
    calls = (call for call in evt.cmd.calls() if any((call.name, *call.args)[: len(argv)] == argv for argv in OPENERS))
    return next((call.cwd for call in calls if call.cwd), evt.cwd)


def lane_name(evt: BaseHookEvent) -> str:
    named = evt.agent_id and own_name(evt)
    return named or reqenv.getenv("CLAUDE_LONG_RUNNING_LANE") or evt.session_id


UNRECORDED = "The opened PRs were not recorded in the drive ledger: {reason}. Rerun `{retry}`."
UNSETTLED = (
    "The landed or closed PRs {prs} were not settled in the drive ledger: {reason}. "
    "Rerun `{retry}`; it reads the ledger id and checkout from the drive registry."
)
LANDED = f"{sys.executable} {DRIVE} landed"


def run_drive(argv: list[str]) -> tuple[subprocess.CompletedProcess[str] | None, str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=RECORD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return None, f"drive.py timed out after {RECORD_TIMEOUT_SECONDS}s"
    except OSError as failure:
        return None, str(failure)
    if not done.returncode:
        return done, ""
    return done, (done.stderr.strip().splitlines() or [f"drive.py exited {done.returncode}"])[-1]


@on(
    Event.PostToolUse,
    only_if=[
        Tool("Bash"),
        Or(*(Runs(*argv) for argv in OPENERS)),
    ],
    tests={
        Input(
            command="ccx vcs ship -m 'ci: ✨ x'",
            output=(FIXTURES / "ccx-ship-gt.txt").read_text(),
            session_id="900424b6-0000",
            cwd="/",
            commands={f"{sys.executable} {DRIVE} record": "registered deploy-experience #28534"},
        ): Allow(),
        Input(
            command="ccx vcs stack submit",
            output=(FIXTURES / "ccx-stack-submit.txt").read_text(),
            session_id="5e55-0000",
            cwd="/",
            commands={f"{sys.executable} {DRIVE} record": ""},
        ): Allow(),
        Input(command="gh pr view 28534", output="https://github.com/Forge-AI/monorepo/pull/28534"): Allow(),
        Input(command="ccx vcs ship -m 'ci: ✨ x' --no-push", output="committed 1caa098d30 \"ci: ✨ x\""): Allow(),
    },
)
def record_opened_prs(evt: BaseHookEvent) -> HookResult | None:
    if not (prs := opened_prs(response_text(evt.tool_response))):
        return None
    argv = [sys.executable, str(DRIVE), "record", "--session", evt.session_id, "--lane", lane_name(evt), "--cwd", str(opener_cwd(evt))]
    argv += ["--drive", drive] if (drive := reqenv.getenv("CLAUDE_LONG_RUNNING_DRIVE")) else []
    for pr in prs:
        argv += ["--pr", pr.spec]
    done, reason = run_drive(argv)
    if done is None or done.returncode:
        return evt.context(UNRECORDED.format(reason=reason, retry=shlex.join(argv)))
    if done.stdout.strip():
        return None
    return evt.context(done.stderr.strip()) if done.stderr.strip() and reqenv.getenv("CLAUDE_LONG_RUNNING_LANE") else None


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
    only_if=[
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
    argv = [sys.executable, str(DRIVE), "landed", "--session", evt.session_id]
    argv += ["--drive", drive] if (drive := reqenv.getenv("CLAUDE_LONG_RUNNING_DRIVE")) else []
    for pr in prs:
        argv += ["--pr", pr]
    done, reason = run_drive(argv)
    if done is None or done.returncode:
        return evt.context(UNSETTLED.format(prs=", ".join(f"#{pr}" for pr in prs), reason=reason, retry=shlex.join(argv)))
    return None
