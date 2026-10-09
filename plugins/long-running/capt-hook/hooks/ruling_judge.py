from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Literal

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Warn, WorkflowState, on, workflow_state
from pydantic import BaseModel, create_model

from .compaction_handoff import REGISTER_FENCE, RULINGS, CompactionState, rulings
from .register_injection import drive_args

CANDIDATES = 5
CANDIDATE_BYTES = 6000
MOMENT_CHARS = 2000
DECISION = re.compile(r"\b(?:GO|HOLD|DECIDE)\b")
BLOCK_ID = re.compile(r"^- ([0-9a-f]{7}) ", re.MULTILINE)
PR_VERBS = {("vcs", "ship"), ("vcs", "stack"), ("pr", "create")}
PROMPT = """An agent in a long-running drive is about to take this action ({kind}):

{moment}

These durable owner answers are the nearest by semantic search:

{candidates}
Return the id of the one answer that this action clearly bears on or would violate, or "none" when no answer clearly applies.
Sharing a word, a system, or a topic is not enough: the action must make the very decision that answer settled, or contradict it."""
CANDIDATE = "- 1984bf6 May a migration ship behind a flag?\n  > No. Delete or replace.\n"
MATCH = {f"{sys.executable} {RULINGS} match": CANDIDATE}
ACTIVE = CompactionState(active=True, slug="brook")


@workflow_state("long_running_ruling_judge")
class JudgedAnswers(WorkflowState):
    injected: list[str] = []


def verdict_model(ids: list[str]) -> type[BaseModel]:
    return create_model("Verdict", answer=(Literal[("none", *ids)], ...))


def bash_moment(evt: BaseHookEvent) -> tuple[str, str] | None:
    for call in evt.command.calls():
        name, args = Path(call.name).name, call.args
        if any(redirect.op == ">>" and "inbox" in Path(redirect.target).parts for redirect in call.redirects):
            if DECISION.search(text := " ".join(args)):
                return "a GO, HOLD or DECIDE line", text
        if name in ("ccx", "gh") and tuple(args[:2]) in PR_VERBS:
            return "a pull request", " ".join(args)
        if name == "orca-launch.sh" and args and Path(args[-1]).expanduser().is_file():
            return "a lane spawn", Path(args[-1]).expanduser().read_text()
    return None


def moment(evt: BaseHookEvent) -> tuple[str, str] | None:
    match evt.tool_name:
        case "Agent":
            return "a lane spawn", evt._tool_input.get("prompt") or ""
        case "Bash":
            return bash_moment(evt)
        case "Write" | "Edit" | "MultiEdit" if evt.file_matches("**/.claude/plans/*.md"):
            return "a plan step", evt.content or ""
    return None


@on(
    Event.PreToolUse,
    tests={
        Input(
            tool="Agent", tool_input={"prompt": "Add a --skip-tenant flag."}, commands=MATCH, llm={"answer": "1984bf6"}, state=[ACTIVE]
        ): Warn(pattern=r"^Durable owner answer `1984bf6` bears on this call and binds it\.\n~{12}\n- 1984bf6 May a migration"),
        Input(
            tool="Agent", tool_input={"prompt": "Add a --skip-tenant flag."}, commands=MATCH, llm={"answer": "none"}, state=[ACTIVE]
        ): Allow(),
        Input(
            tool="Agent",
            tool_input={"prompt": "Add a --skip-tenant flag."},
            commands=MATCH,
            llm={"answer": "1984bf6"},
            state=[ACTIVE, JudgedAnswers(injected=["main:1984bf6"])],
        ): Allow(),
        Input(command="echo '- G901 (root) GO walker: release api' >> ~/scratch/brook/inbox/deploy-go.md", commands=MATCH, llm={"answer": "1984bf6"}, state=[ACTIVE]): Warn(
            pattern=r"^Durable owner answer `1984bf6`"
        ),
        Input(command="echo '- G902 (root) note' >> ~/scratch/brook/inbox/deploy-go.md", commands=MATCH, llm={"answer": "1984bf6"}, state=[ACTIVE]): Allow(),
        Input(command="cc-slack reply --url C0B/p1 --text 'Shipping the skip flag'", commands=MATCH, llm={"answer": "1984bf6"}, state=[ACTIVE]): Allow(),
        Input(command="ls", commands=MATCH, llm={"answer": "1984bf6"}, state=[ACTIVE]): Allow(),
        Input(tool="Agent", tool_input={"prompt": "Add a --skip-tenant flag."}, commands=MATCH, llm={"answer": "1984bf6"}): Allow(),
    },
)
def judge_key_moment(evt: BaseHookEvent) -> HookResult | None:
    if not (found := moment(evt)) or not found[1].strip() or not (which := drive_args(evt)):
        return None
    kind, text = found
    lane = f"{evt.agent_id or 'main'}:"
    excluded = [arg for key in JudgedAnswers.load(evt).injected if key.startswith(lane) for arg in ("--exclude", key.removeprefix(lane))]
    candidates = rulings(
        str(evt.cwd), "match", *which, "-k", str(CANDIDATES), "--budget", str(CANDIDATE_BYTES), *excluded, stdin=text[:MOMENT_CHARS]
    )
    if not (ids := BLOCK_ID.findall(candidates)):
        return None
    verdict = evt.llm(PROMPT.format(kind=kind, moment=text[:MOMENT_CHARS], candidates=candidates), verdict_model(ids), size="small")
    if verdict is None or (answer := verdict.answer) not in ids:
        return None
    key = f"{lane}{answer}"
    with JudgedAnswers.mutate(evt) as state:
        if key in state.injected:
            return None
        state.injected.append(key)
    block = next(block for block in re.split(r"(?m)^(?=- [0-9a-f]{7} )", candidates) if block.startswith(f"- {answer} "))
    return evt.context(f"Durable owner answer `{answer}` bears on this call and binds it.", f"{REGISTER_FENCE}\n{block}{REGISTER_FENCE}")
