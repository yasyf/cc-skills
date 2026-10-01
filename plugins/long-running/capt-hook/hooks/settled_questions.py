from __future__ import annotations

import re
import time
from pathlib import Path

from captain_hook import (
    Allow,
    BaseHookEvent,
    Block,
    Event,
    FileFixture,
    FromSubagent,
    HookResult,
    Input,
    Tool,
    WorkflowState,
    on,
    workflow_state,
)

from .compaction_handoff import CompactionState

BURST_SECONDS = 10 * 60
DECISIONS_CHARS = 3000
DECISIONS = re.compile(r"^(#{2,3}) [^\n]*\bdecisions?\b[^\n]*$", re.IGNORECASE | re.MULTILINE)
BOARD_VERBS = frozenset({"push", "update-block"})
PLAN = "# brook\n\n## Decisions (owner, binding)\n- Every side-feature carries with the same UX.\n\n## Context\nprose\n"


@workflow_state("long_running_settled_questions")
class SettledState(WorkflowState):
    blocked_at: float | None = None


def asks_owner(evt: BaseHookEvent) -> bool:
    if evt.tool_name == "AskUserQuestion":
        return True
    for call in evt.command.calls():
        if Path(call.name).name != "cc-present" or not call.args or "--dry-run" in call.args:
            continue
        verb, *rest = call.args
        if verb in BOARD_VERBS or (verb == "start" and any(arg.split("=")[0] == "--doc" for arg in rest)):
            return True
    return False


def decisions(plan_path: str | None) -> str | None:
    if not plan_path or not (plan := Path(plan_path).expanduser()).is_file():
        return None
    text = plan.read_text()
    if not (heading := DECISIONS.search(text)):
        return None
    following = re.compile(rf"^#{{1,{len(heading[1])}}} ", re.MULTILINE).search(text, heading.end())
    section = text[heading.start() : following.start() if following else len(text)].strip()
    return section[:DECISIONS_CHARS]


def gate_message(plan_path: str | None) -> str:
    settled = decisions(plan_path)
    lines = [
        "Owner-question gate (long-running R19): before this reaches the owner, check every question "
        "against what is already settled. A question the plan, a durable answer, or a memory already "
        "answers is applied and logged, never asked, and never offered as a confirm-or-override card.",
        f"- the plan's decisions{f' ({plan_path})' if plan_path else ''}{', quoted below' if settled else ''}",
        "- `ccn answer list --label scope:durable` and the drive's own answer labels",
        "- this project's feedback memories",
        "Drop or apply every settled question, then re-issue the call with the rest; "
        f"asking calls pass for the next {BURST_SECONDS // 60} minutes.",
    ]
    if settled:
        lines.append(settled)
    return "\n".join(lines)


@on(
    Event.PreToolUse,
    only_if=[Tool("AskUserQuestion", "Bash")],
    skip_if=[FromSubagent()],
    tests={
        Input(
            tool="AskUserQuestion",
            tool_input={"questions": [{"question": "Keep the drops?"}]},
            file=FileFixture(home=True, name="brook.md", content=PLAN),
            state=[CompactionState(active=True, plan_path="~/brook.md")],
        ): Block(pattern=r"(?s)R19.*~/brook\.md.*Every side-feature carries"),
        Input(
            tool="Bash",
            tool_input={"command": "cc-present start --session s --doc owner-board.json --new"},
            state=[CompactionState(active=True)],
        ): Block(pattern=r"scope:durable"),
        Input(
            tool="Bash",
            tool_input={"command": "cc-present update-block card.json --after sec-open --session s"},
            state=[CompactionState(active=True)],
        ): Block(),
        Input(
            tool="AskUserQuestion",
            tool_input={"questions": [{"question": "Keep the drops?"}]},
            state=[CompactionState(active=True), SettledState(blocked_at=time.time())],
        ): Allow(),
        Input(tool="AskUserQuestion", tool_input={"questions": [{"question": "Which?"}]}): Allow(),
        Input(
            tool="Bash",
            tool_input={"command": "cc-present remove-block o1-card --session s"},
            state=[CompactionState(active=True)],
        ): Allow(),
        Input(
            tool="Bash",
            tool_input={"command": "cc-present push --dry-run owner-board.json"},
            state=[CompactionState(active=True)],
        ): Allow(),
        Input(
            tool="Bash",
            tool_input={"command": "cc-present start --session s"},
            state=[CompactionState(active=True)],
        ): Allow(),
        Input(
            tool="AskUserQuestion",
            tool_input={"questions": [{"question": "Keep the drops?"}]},
            agent_id="a1b2c3",
            state=[CompactionState(active=True)],
        ): Allow(),
    },
)
def check_settled_before_asking(evt: BaseHookEvent) -> HookResult | None:
    drive = CompactionState.load(evt)
    if not drive.active or not asks_owner(evt):
        return None
    now = time.time()
    with SettledState.mutate(evt) as state:
        if state.blocked_at is not None and now - state.blocked_at < BURST_SECONDS:
            return None
        state.blocked_at = now
    return evt.block(gate_message(drive.plan_path))
