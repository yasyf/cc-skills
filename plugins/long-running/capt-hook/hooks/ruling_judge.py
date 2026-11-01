from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Warn, WorkflowState, on, workflow_state
from captain_hook.util import reqenv

from .compaction_handoff import REGISTER_FENCE, CompactionState, rulings, script, script_module
from .judge_metrics import judge_call
from .register_injection import SLICE_HEADER, drive_args
from .tests.register_fixtures import DRIVE_CONFIG
from .tests.rulings_consumers import separate

CANDIDATES = 2
CANDIDATE_BYTES = 6000
MOMENT_CHARS = 2000
BRIEF_CHARS = 6000
MATCH_TIMEOUT_SECONDS = 8
DECISION = re.compile(r"\b(?:GO|HOLD|DECIDE)\b")
BLOCK_ID = re.compile(r"^- ([0-9a-f]{7}) ", re.MULTILINE)
BLOCK_START = re.compile(r"(?m)^(?=- [0-9a-f]{7} )")
PR_VERBS = {("vcs", "ship"), ("vcs", "stack"), ("pr", "create")}
EVIDENCE = "A durable answer of the owner's: the question it answered, then his answer quoted under it.\n{block}"
WARNING = "Warning, nothing was blocked: this call makes the decision durable owner answer `{answer}` settled without carrying all of it."
FINDING = '{sentence}\nThe call: "{artifact_phrase}"\nHis answer: "{evidence_phrase}"\n\n{block}'
CANDIDATE = "- 1984bf6 May a migration ship behind a flag?\n  > No. Delete or replace.\n"
MATCH = {f"{sys.executable} {script('rulings.py')} match": CANDIDATE}
DROPPED = {
    "verdict": "yes",
    "artifact_phrase": "Add a --skip-tenant flag",
    "evidence_phrase": "No. Delete or replace.",
    "sentence": "He said a migration never ships behind a flag, and this adds one.",
}
CARRIED = {**DROPPED, "verdict": "no", "artifact_phrase": "", "evidence_phrase": "", "sentence": "It does not put a migration behind a flag."}
ACTIVE = CompactionState(active=True, slug="brook")
ROOT = "main:"


def answered(verdict: dict) -> dict[str, str]:
    return {"claude -p": json.dumps({"structured_output": verdict, "total_cost_usd": 0.01, "usage": {"input_tokens": 900, "output_tokens": 40}}), **MATCH}


@workflow_state("long_running_ruling_judge")
class JudgedAnswers(WorkflowState):
    injected: list[str] = []


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
    tests=separate("ruling_judge.judge_key_moment", {
        Input(tool="Agent", tool_input={"prompt": "Add a --skip-tenant flag."}, commands=answered(DROPPED), env=DRIVE_CONFIG, state=[ACTIVE]): Warn(
            pattern=r"^Warning, nothing was blocked: this call makes the decision durable owner answer `1984bf6` settled without carrying all of it\.\n~{12}\nHe said a migration never ships"
        ),
        Input(tool="Agent", tool_input={"prompt": "Add a --skip-tenant flag."}, commands=answered(CARRIED), env=DRIVE_CONFIG, state=[ACTIVE]): Allow(),
        Input(
            tool="Agent",
            tool_input={"prompt": "Register slice: lane=lane-a step=1\n### rule/1984bf6 | pick | in-force\n> Add a --skip-tenant flag."},
            commands=answered(DROPPED),
            env=DRIVE_CONFIG,
            state=[ACTIVE],
        ): Allow(),
        Input(
            tool="Agent",
            tool_input={"prompt": "Add a --skip-tenant flag."},
            commands=answered(DROPPED),
            env=DRIVE_CONFIG,
            state=[ACTIVE, JudgedAnswers(injected=["main:1984bf6"])],
        ): Allow(),
        Input(command="echo '- G901 (root) GO walker: release api' >> ~/scratch/brook/inbox/deploy-go.md", commands=answered(DROPPED), env=DRIVE_CONFIG, state=[ACTIVE]): Warn(
            pattern=r"durable owner answer `1984bf6`"
        ),
        Input(command="echo '- G902 (root) note' >> ~/scratch/brook/inbox/deploy-go.md", commands=answered(DROPPED), env=DRIVE_CONFIG, state=[ACTIVE]): Allow(),
        Input(command="cc-slack reply --url C0B/p1 --text 'Shipping the skip flag'", commands=answered(DROPPED), env=DRIVE_CONFIG, state=[ACTIVE]): Allow(),
        Input(command="ls", commands=answered(DROPPED), env=DRIVE_CONFIG, state=[ACTIVE]): Allow(),
        Input(tool="Agent", tool_input={"prompt": "Add a --skip-tenant flag."}, commands=answered(DROPPED)): Allow(),
    }),
)
def judge_key_moment(evt: BaseHookEvent) -> HookResult | None:
    if not (found := moment(evt)) or not (which := drive_args(evt)):
        return None
    kind, text = found
    text = SLICE_HEADER.split(text, maxsplit=1)[0][:BRIEF_CHARS] if kind == "a lane spawn" else text[:MOMENT_CHARS]
    if not text.strip():
        return None
    lane = f"{evt.agent_id}:" if evt.agent_id else ROOT
    excluded = [arg for key in JudgedAnswers.load(evt).injected if key.startswith(lane) for arg in ("--exclude", key.removeprefix(lane))]
    try:
        candidates = rulings(
            str(evt.cwd),
            "match",
            *which,
            "-k",
            str(CANDIDATES),
            "--budget",
            str(CANDIDATE_BYTES),
            *excluded,
            stdin=text,
            timeout=reqenv.clamp_timeout(MATCH_TIMEOUT_SECONDS),
            evt=evt,
        )
    except subprocess.TimeoutExpired:
        return None
    judge, env = script_module("long_running/judge"), reqenv.env_map()
    for block in BLOCK_START.split(candidates):
        if not (cited := BLOCK_ID.match(block)):
            continue
        verdict = judge_call(
            judge,
            "answer",
            EVIDENCE.format(block=block),
            f"{kind}:\n{text}",
            backend=judge.backend_from(env),
            drive=which[1],
            timeout=reqenv.clamp_timeout(judge.JUDGE_SECONDS),
            env=env,
        )
        if verdict is None or not verdict.flagged:
            continue
        key = f"{lane}{(answer := cited[1])}"
        with JudgedAnswers.mutate(evt) as state:
            if key in state.injected:
                continue
            state.injected.append(key)
        return evt.context(WARNING.format(answer=answer), f"{REGISTER_FENCE}\n{FINDING.format(block=block.rstrip(), **asdict(verdict))}\n{REGISTER_FENCE}")
    return None


@on(
    Event.SessionStart,
    skip_if=[FromSubagent()],
    tests=separate("ruling_judge.forget_root_answers_after_compaction", {
        Input(source="compact", state=[JudgedAnswers(injected=["main:1984bf6", "a1b2c3:1984bf6"])]): Allow(),
        Input(source="resume", state=[JudgedAnswers(injected=["main:1984bf6"])]): Allow(),
        Input(source="compact"): Allow(),
    }),
)
def forget_root_answers_after_compaction(evt: BaseHookEvent) -> HookResult | None:
    if evt.source != "compact":
        return None
    with JudgedAnswers.mutate(evt) as state:
        state.injected = [key for key in state.injected if not key.startswith(ROOT)]
    return None
