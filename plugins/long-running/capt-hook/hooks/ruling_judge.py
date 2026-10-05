from __future__ import annotations

import re
import sys
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Warn, WorkflowState, on, workflow_state
from pydantic import BaseModel

from .compaction_handoff import REGISTER_FENCE, RULINGS, CompactionState, rulings
from .register_injection import drive_args

CANDIDATES = 5
CANDIDATE_BYTES = 6000
MOMENT_CHARS = 2000
DECISION = re.compile(r"\b(?:GO|HOLD|DECIDE)\b")
BLOCK_ID = re.compile(r"^- ([0-9a-f]{7}) ", re.MULTILINE)
PR_VERBS = {("vcs", "ship"), ("vcs", "stack"), ("pr", "create")}
SLACK_VERBS = {"send", "reply"}
RULING_KINDS = {"go", "decide"}
PROMPT = """An agent in a long-running drive is about to take this action ({kind}):

{moment}

These durable owner answers are the nearest by semantic search:

{candidates}
Return the id of the one answer that this action clearly bears on or would violate, or "none" when no answer clearly applies."""
CANDIDATE = "- 1984bf6 May a migration ship behind a flag?\n  > No. Delete or replace.\n"
MATCH = {f"{sys.executable} {RULINGS} match": CANDIDATE}
ACTIVE = CompactionState(active=True, slug="brook")


@workflow_state("long_running_ruling_judge")
class JudgedAnswers(WorkflowState):
    injected: list[str] = []


class Verdict(BaseModel):
    answer: str


def flag(args: tuple[str, ...], *names: str) -> str:
    return next((args[i + 1] for i, arg in enumerate(args[:-1]) if arg in names), "")


def bash_moment(evt: BaseHookEvent) -> tuple[str, str] | None:
    for call in evt.command.calls():
        name, args = Path(call.name).name, call.args
        if any(redirect.op == ">>" and "inbox" in Path(redirect.target).parts for redirect in call.redirects):
            if DECISION.search(text := " ".join(args)):
                return "a GO, HOLD or DECIDE line", text
        if name == "cci" and args[:1] and args[0] == "post" and flag(args, "--kind") in RULING_KINDS:
            return "a GO or DECIDE record", flag(args, "--text")
        if name == "cc-slack" and args[:1] and args[0] in SLACK_VERBS:
            return "a Slack write", flag(args, "--text")
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
        case str(name) if "slack" in name and any(verb in name for verb in SLACK_VERBS):
            return "a Slack write", evt._tool_input.get("text") or evt._tool_input.get("message") or ""
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
        Input(command="cci post --drive brook --lane root --kind go --to walker --text 'release api'", commands=MATCH, llm={"answer": "1984bf6"}, state=[ACTIVE]): Warn(
            pattern=r"^Durable owner answer `1984bf6`"
        ),
        Input(command="cci post --drive brook --lane root --kind note --text 'release api'", commands=MATCH, llm={"answer": "1984bf6"}, state=[ACTIVE]): Allow(),
        Input(command="echo 'R9 orca-desk: HOLD walker' >> ~/scratch/brook/inbox/orca-desk.md", commands=MATCH, llm={"answer": "1984bf6"}, state=[ACTIVE]): Warn(
            pattern=r"^Durable owner answer `1984bf6`"
        ),
        Input(command="cc-slack reply --url C0B/p1 --text 'Shipping the skip flag'", commands=MATCH, llm={"answer": "1984bf6"}, state=[ACTIVE]): Warn(
            pattern=r"^Durable owner answer `1984bf6`"
        ),
        Input(command="ls", commands=MATCH, llm={"answer": "1984bf6"}, state=[ACTIVE]): Allow(),
        Input(tool="Agent", tool_input={"prompt": "Add a --skip-tenant flag."}, commands=MATCH, llm={"answer": "1984bf6"}): Allow(),
    },
)
def judge_key_moment(evt: BaseHookEvent) -> HookResult | None:
    if not (found := moment(evt)) or not found[1].strip() or not (which := drive_args(evt)):
        return None
    kind, text = found
    candidates = rulings(str(evt.cwd), "match", *which, "-k", str(CANDIDATES), "--budget", str(CANDIDATE_BYTES), stdin=text[:MOMENT_CHARS])
    if not (ids := BLOCK_ID.findall(candidates)):
        return None
    verdict = evt.llm(PROMPT.format(kind=kind, moment=text[:MOMENT_CHARS], candidates=candidates), Verdict, size="small")
    if verdict is None or (answer := verdict.answer.strip()[:7]) not in ids:
        return None
    key = f"{evt.agent_id or 'main'}:{answer}"
    with JudgedAnswers.mutate(evt) as state:
        if key in state.injected:
            return None
        state.injected.append(key)
    block = next(block for block in re.split(r"(?m)^(?=- [0-9a-f]{7} )", candidates) if block.startswith(f"- {answer} "))
    return evt.context(f"Durable owner answer `{answer}` bears on this call and binds it.", f"{REGISTER_FENCE}\n{block}{REGISTER_FENCE}")
