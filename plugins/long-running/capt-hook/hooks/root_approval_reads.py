from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Input, Not, ReadFile, T, Tool, Warn

from .cci_mcp_posts import POST_TOOL
from .lane_rotation import ROOT
from .meta_records import posts
from .root_context import root_warning
from .tests.root_fixtures import ACTIVE

APPROVING_KINDS = frozenset({"go", "decide", "answer"})
APPROVES_RECORD = re.compile(r"\b(?:finder|fidelity)-override\b|\bgo #\d+ at\b|\boverrides?\b[^.\n]*\badopted\b", re.IGNORECASE)
APPROVES_PLAN = re.compile(r"\bplan\b[^.\n]*\bapproved\b|\bapproved?\b[^.\n]*\bplan\b", re.IGNORECASE)
NUMBER = re.compile(r"\b\d+\b")
PLANS = "**/*plan*"
RECORD = "cci post --drive d --lane root --kind decide --text 'finder-override #47755 at abc1234: 1234'"
PLAN = "cci post --drive d --lane root --kind answer --text 'plan approved, build it'"


def root_approvals(evt: BaseHookEvent, approves: re.Pattern[str]) -> list[str]:
    return [text for lane, _, kind, text, _, _ in posts(evt) if lane == ROOT and kind in APPROVING_KINDS and approves.search(text)]


def read_it(evt: BaseHookEvent, text: str) -> bool:
    return any(evt.ctx.t.has_command(*reader, number, subagents=False) for number in NUMBER.findall(text) for reader in (("cci", "show"), ("gh", "pr", "diff")))


class ApprovesAnUnreadRecord:
    def check(self, evt: BaseHookEvent) -> bool:
        return any(not read_it(evt, text) for text in root_approvals(evt, APPROVES_RECORD))


class ApprovesAPlan:
    def check(self, evt: BaseHookEvent) -> bool:
        return bool(root_approvals(evt, APPROVES_PLAN))


def earlier(*calls) -> list:
    return [T.user("land what is ready"), T.assistant(*calls)]


root_warning(
    message="Warning, nothing was blocked: Root reads what it approves. Run `cci show <seq>` on the verdict or `gh pr diff <n>` on the change before this post.",
    only_if=[Tool("Bash", POST_TOOL), ApprovesAnUnreadRecord()],
    tests={
        Input(command=RECORD, state=ACTIVE, transcript=earlier()): Warn(pattern=r"reads what it approves"),
        Input(command="cci post --drive d --lane root --kind go --text 'go #49730 at e718a74'", state=ACTIVE, transcript=earlier(T.tool("Bash", command="gh pr view 49730"))): Warn(),
        Input(command="cci post --drive d --lane root --kind answer --text 'Grader overrides for #50083 at 7fe9503 are adopted'", state=ACTIVE, transcript=earlier()): Warn(),
        Input(command=RECORD, state=ACTIVE, transcript=earlier(T.tool("Bash", command="cci show 1234"))): Allow(),
        Input(command=RECORD, state=ACTIVE, transcript=earlier(T.tool("Bash", command="gh pr diff 47755"))): Allow(),
        Input(command="cci post --drive d --lane root --kind decide --text 'hold the release'", state=ACTIVE, transcript=earlier()): Allow(),
        Input(command="cci post --drive d --lane lane-a --kind decide --text 'finder-override #47755 at abc1234: 1234'", state=ACTIVE, transcript=earlier()): Allow(),
        Input(command=RECORD, transcript=earlier()): Allow(),
        Input(command=RECORD, agent_id="a1b2c3", state=ACTIVE, transcript=earlier()): Allow(),
    },
)

root_warning(
    message="Warning, nothing was blocked: Root reads what it approves. Read the plan file before this post.",
    only_if=[Tool("Bash", POST_TOOL), ApprovesAPlan(), Not(ReadFile(PLANS))],
    tests={
        Input(command=PLAN, state=ACTIVE, transcript=earlier()): Warn(pattern=r"Read the plan file"),
        Input(command=PLAN, state=ACTIVE, transcript=earlier(T.tool("Read", file_path="/drive/lane-a/plan.md"))): Allow(),
        Input(command="cci post --drive d --lane root --kind answer --text 'the release plan waits on him'", state=ACTIVE, transcript=earlier()): Allow(),
    },
)
