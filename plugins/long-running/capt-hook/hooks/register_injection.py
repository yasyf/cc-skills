from __future__ import annotations

import json
import re
import sys
import time

from captain_hook import (
    Allow,
    BaseHookEvent,
    Event,
    FromSubagent,
    HookResult,
    Input,
    Tool,
    Warn,
    WorkflowState,
    on,
    workflow_state,
)
from captain_hook.util import reqenv

from .compaction_handoff import REGISTER_FENCE, RULINGS, SHORT, CompactionState, register_of, rulings

DRIVE_ENV = "CLAUDE_LONG_RUNNING_DRIVE"
DEFAULT_AGENT = "general-purpose"
SPAWN_TTL_SECONDS = 60
CONTEXT_CHARS = 9000
MATCH_BYTES = 8000
BLOCK = re.compile(r"^- ", re.MULTILINE)
REGISTER = {"id": "c" * 40, "body": "# Register\n\n1. Pulumi state is the only truth.\n"}
MATCHED = "- 4ffc9a5 Release as it merges?\n  > Yes, every landing releases.\n"
FOUND = {
    f"{sys.executable} {RULINGS} register": json.dumps(REGISTER),
    f"{sys.executable} {RULINGS} match": MATCHED,
}
NONE_FOUND = {f"{sys.executable} {RULINGS} register": "null", f"{sys.executable} {RULINGS} match": ""}
ACTIVE = CompactionState(active=True, slug="brook")


@workflow_state("long_running_lane_rulings")
class LaneRulings(WorkflowState):
    spawns: list[dict] = []
    pending: dict[str, str] = {}
    briefed: bool = False


def drive_args(evt: BaseHookEvent) -> list[str] | None:
    if (drive := CompactionState.load(evt)).active and drive.slug:
        return ["--program", drive.slug]
    if worker := reqenv.getenv(DRIVE_ENV):
        return ["--drive", worker]
    return None


def register_context(evt: BaseHookEvent, register: dict) -> HookResult:
    if len(register["body"]) > CONTEXT_CHARS:
        return evt.context(
            f"Standing rules register `{register['id'][:SHORT]}` is over the injection budget and binds this lane; "
            f"read it in full with `ccn doc show {register['id'][:SHORT]}` before acting."
        )
    return evt.context(
        f"Standing rules register `{register['id'][:SHORT]}`, verbatim; it binds this lane and outranks any brief or summary.",
        f"{REGISTER_FENCE}\n{register['body'].rstrip()}\n{REGISTER_FENCE}",
    )


def union(texts: list[str]) -> str:
    blocks = dict.fromkeys(block for text in texts for block in BLOCK.split(text) if block)
    kept, size = [], 0
    for block in blocks:
        if size + len(f"- {block}".encode()) > MATCH_BYTES:
            break
        kept.append(f"- {block}")
        size += len(kept[-1].encode())
    return "".join(kept)


def rulings_context(evt: BaseHookEvent, matched: str) -> HookResult:
    return evt.context(
        "Durable owner answers matched to this lane's brief, verbatim; they bind it alongside the register.",
        f"{REGISTER_FENCE}\n{matched}{REGISTER_FENCE}",
    )


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent")],
    tests={
        Input(tool="Agent", tool_input={"prompt": "Retire the valkey stacks."}, commands=FOUND, state=[ACTIVE]): Allow(),
        Input(tool="Agent", tool_input={"prompt": "Retire the valkey stacks."}): Allow(),
    },
)
def match_lane_brief(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    matched = rulings(str(evt.cwd), "match", *which, stdin=evt._tool_input.get("prompt") or "")
    spawn = {"type": evt._tool_input.get("subagent_type") or DEFAULT_AGENT, "at": time.time(), "rulings": matched}
    with LaneRulings.mutate(evt) as state:
        state.spawns = [entry for entry in state.spawns if spawn["at"] - entry["at"] < SPAWN_TTL_SECONDS] + [spawn]
    return None


def claim(evt: BaseHookEvent) -> None:
    now = time.time()
    with LaneRulings.mutate(evt) as state:
        state.spawns = [entry for entry in state.spawns if now - entry["at"] < SPAWN_TTL_SECONDS]
        mine = union([entry["rulings"] for entry in state.spawns if entry["type"] == (evt.agent_type or DEFAULT_AGENT)])
        if mine and evt.agent_id:
            state.pending[evt.agent_id] = mine


@on(
    Event.SubagentStart,
    skip_planning_agents=False,
    tests={
        Input(agent_type="general-purpose", agent_id="a1b2c3", commands=FOUND, state=[ACTIVE]): Warn(
            pattern=r"^Standing rules register `ccccccc`, verbatim; it binds this lane and outranks any brief or summary\.\n"
            r"~{12}\n# Register\n\n1\. Pulumi state is the only truth\.\n~{12}$"
        ),
        Input(agent_type="general-purpose", agent_id="a1b2c3", commands=NONE_FOUND, state=[ACTIVE]): Allow(),
        Input(agent_type="general-purpose", agent_id="a1b2c3"): Allow(),
    },
)
def brief_subagent(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    claim(evt)
    register = register_of(str(evt.cwd), *which)
    return register_context(evt, register) if register else None


@on(
    Event.PostToolUse,
    tests={
        Input(
            tool="Bash",
            tool_input={"command": "ls"},
            agent_id="a1b2c3",
            state=[ACTIVE, LaneRulings(pending={"a1b2c3": MATCHED})],
        ): Warn(
            pattern=r"^Durable owner answers matched to this lane's brief, verbatim; they bind it alongside the register\.\n"
            r"~{12}\n- 4ffc9a5 Release as it merges\?\n  > Yes, every landing releases\.\n~{12}$"
        ),
        Input(tool="Bash", tool_input={"command": "ls"}, agent_id="d4e5f6", state=[ACTIVE, LaneRulings(pending={"a1b2c3": MATCHED})]): Allow(),
        Input(tool="Bash", tool_input={"command": "ls"}, state=[ACTIVE, LaneRulings(pending={"a1b2c3": MATCHED})]): Allow(),
    },
)
def deliver_lane_rulings(evt: BaseHookEvent) -> HookResult | None:
    if not evt.agent_id or evt.agent_id not in LaneRulings.load(evt).pending:
        return None
    with LaneRulings.mutate(evt) as state:
        matched = state.pending.pop(evt.agent_id, "")
    return rulings_context(evt, matched) if matched else None


@on(
    Event.SessionStart,
    skip_if=[FromSubagent()],
    tests={
        Input(source="startup", env={DRIVE_ENV: "900424b6"}, commands=FOUND): Warn(pattern=r"^Standing rules register `ccccccc`"),
        Input(source="startup", env={DRIVE_ENV: "900424b6"}, commands=NONE_FOUND): Allow(),
        Input(source="startup"): Allow(),
        Input(source="compact", env={DRIVE_ENV: "900424b6"}, commands=FOUND, state=[ACTIVE]): Allow(),
    },
)
def brief_drive_worker(evt: BaseHookEvent) -> HookResult | None:
    if CompactionState.load(evt).active or not (worker := reqenv.getenv(DRIVE_ENV)):
        return None
    register = register_of(str(evt.cwd), "--drive", worker)
    return register_context(evt, register) if register else None


@on(
    Event.UserPromptSubmit,
    skip_if=[FromSubagent()],
    tests={
        Input(prompt="Lane brief: retire the valkey stacks.", env={DRIVE_ENV: "900424b6"}, commands=FOUND): Warn(
            pattern=r"^Durable owner answers matched to this lane's brief"
        ),
        Input(
            prompt="continue", env={DRIVE_ENV: "900424b6"}, commands=FOUND, state=[LaneRulings(briefed=True)]
        ): Allow(),
        Input(prompt="Lane brief: retire the valkey stacks.", commands=FOUND): Allow(),
        Input(prompt="Lane brief.", env={DRIVE_ENV: "900424b6"}, commands=FOUND, state=[ACTIVE]): Allow(),
    },
)
def match_worker_brief(evt: BaseHookEvent) -> HookResult | None:
    if CompactionState.load(evt).active or not (worker := reqenv.getenv(DRIVE_ENV)) or LaneRulings.load(evt).briefed:
        return None
    with LaneRulings.mutate(evt) as state:
        if state.briefed:
            return None
        state.briefed = True
    matched = rulings(str(evt.cwd), "match", "--drive", worker, stdin=evt.user_prompt or "")
    return rulings_context(evt, matched) if matched else None
