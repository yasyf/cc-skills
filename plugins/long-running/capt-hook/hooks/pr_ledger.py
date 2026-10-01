from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Or, Runs, Tool, Warn, on
from captain_hook.util import reqenv

DRIVE = Path(__file__).parents[2] / "skills" / "long-running" / "scripts" / "drive.py"
FIXTURES = Path(__file__).parent / "tests" / "fixtures" / "pr_ledger"
PR_URL = re.compile(r"https://(?:github\.com/(?P<gh>[\w.-]+/[\w.-]+)/pull|app\.graphite\.com/github/pr/(?P<gt>[\w.-]+/[\w.-]+))/(?P<pr>\d+)")
SHIPPED = re.compile(r"(?:(?:submitted|landed) \S+ → PR|(?:opened|updated) PR) #(?P<pr>\d+)")
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
    repos = {match["pr"]: match["gh"] or match["gt"] for match in PR_URL.finditer(output)}
    heads = {match["pr"]: match["head"] for match in STACK_HEAD.finditer(output)}
    shipped = [match["pr"] for match in SHIPPED.finditer(output)]
    if len(set(shipped)) == 1 and (published := PUBLISHED.findall(output)):
        heads.setdefault(shipped[0], published[-1])
    numbers = dict.fromkeys([*heads, *shipped, *repos])
    return [OpenedPr(number, repos.get(number), heads.get(number)) for number in numbers]


def lane_name(evt: BaseHookEvent) -> str:
    if evt.agent_id and evt.transcript_path:
        meta = evt.transcript_path.with_suffix("") / "subagents" / f"agent-{evt.agent_id}.meta.json"
        if meta.is_file() and (name := json.loads(meta.read_text()).get("name")):
            return name
    return reqenv.getenv("LONG_RUNNING_LANE") or evt.session_id


@on(
    Event.PostToolUse,
    only_if=[
        Tool("Bash"),
        Or(
            Runs("ccx", "vcs", "ship"),
            Runs("ccx", "vcs", "stack", "submit"),
            Runs("ccx", "vcs", "stack", "continue"),
            Runs("gt", "submit"),
            Runs("gt", "ss"),
            Runs("gh", "pr", "create"),
        ),
    ],
    tests={
        Input(
            command="ccx vcs ship -m 'ci: ✨ x'",
            output=(FIXTURES / "ccx-ship-gt.txt").read_text(),
            session_id="900424b6-0000",
            cwd="/",
            commands={f"{sys.executable} {DRIVE} record": "registered deploy-experience #28534"},
        ): Warn(pattern=r"^Drive ledger: registered deploy-experience #28534$"),
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
    argv = [sys.executable, str(DRIVE), "record", "--session", evt.session_id, "--lane", lane_name(evt), "--cwd", str(evt.cwd)]
    argv += ["--drive", drive] if (drive := reqenv.getenv("LONG_RUNNING_DRIVE")) else []
    for pr in prs:
        argv += ["--pr", pr.spec]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=RECORD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return evt.context(
            f"PRs {', '.join('#' + pr.number for pr in prs)} were not recorded in the drive ledger: drive.py record "
            f"timed out after {RECORD_TIMEOUT_SECONDS}s — run `ledger.py register` for each by hand."
        )
    if done.returncode:
        reason = done.stderr.strip().splitlines()[-1:] or [f"drive.py record exited {done.returncode}"]
        return evt.context(reason[0])
    return evt.context(f"Drive ledger: {done.stdout.strip()}") if done.stdout.strip() else None
