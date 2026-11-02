from __future__ import annotations

import json
import re
import subprocess

from captain_hook import (
    Allow,
    BaseHookEvent,
    Event,
    FromSubagent,
    HookResult,
    Input,
    Rewrite,
    Tool,
    Warn,
    WorkflowState,
    on,
    workflow_state,
)
from captain_hook.util import reqenv

from . import session_tree
from .compaction_handoff import ROOT_LANE, CompactionState, bus_name, drive_entry
from .nudges import stopped_lane
from .tests.register_fixtures import DRIVE_CONFIG
from .tests.rotation_fixtures import ROOT

MENTIONS_STAND_DOWN = re.compile(r"\bstand[\s-]?down\b", re.IGNORECASE)
REPLACES = re.compile(r"^Replaces: `?([\w.-]+)`?\s*$", re.MULTILINE)
NUMBERED = re.compile(r"(.+)-(\d+)")
HANDOFF = "-handoff"
SUCCESSOR_OF = "cci who --successor-of"
LIVE_LANE = "cci who --lane"
MAIN = "main"
SUCCESSOR_TIMEOUT_SECONDS = 10


@workflow_state("long_running_rotation")
class RotationState(WorkflowState):
    retired: dict[str, str | None] = {}


def predecessors(name: str) -> set[str]:
    if not (numbered := NUMBERED.fullmatch(name)) or (number := int(numbered[2])) < 2:
        return set()
    return {f"{numbered[1]}-{number - 1}"} | ({numbered[1]} if number == 2 else set())


def handed_off(evt: BaseHookEvent) -> set[str]:
    return {
        agent.name.removesuffix(HANDOFF) for agent in session_tree.subagents(evt) if agent.name and agent.name.endswith(HANDOFF)
    }


class DriveActive:
    def check(self, evt: BaseHookEvent) -> bool:
        return CompactionState.load(evt).active


def heir(retired: dict[str, str | None], name: str) -> str | None:
    successor = retired[name]
    while successor in retired:
        successor = retired[successor]
    return successor


def orders_stand_down(message: object) -> bool:
    return isinstance(message, str) and session_tree.orders_stand_down(message)


def mentions_stand_down(message: object) -> bool:
    return isinstance(message, str) and MENTIONS_STAND_DOWN.search(message) is not None


@on(
    Event.PostToolUse,
    only_if=[Tool("Agent", "TaskStop", "SendMessage")],
    skip_if=[FromSubagent()],
    tests={
        Input(tool="TaskStop", tool_input={"task_id": "desk@session-root"}): Allow(),
        Input(tool="Agent", tool_input={"name": "landing-desk-2", "prompt": "go"}, transcript=ROOT): Allow(),
        Input(tool="Agent", tool_input={"name": "token-retry-2", "prompt": "Replaces: token-retry\ngo"}, transcript=ROOT, env=DRIVE_CONFIG, commands={"cci post": "#9"}): Allow(),
        Input(tool="SendMessage", tool_input={"to": "desk", "message": "STAND-DOWN: desk-2 owns it"}): Allow(),
    },
)
def record_retired_lanes(evt: BaseHookEvent) -> HookResult | None:
    raw = evt.input.raw
    if evt.tool_name == "Agent":
        if not (name := raw.get("name")):
            return None
        rotated = (predecessors(name) & handed_off(evt)) | (set(REPLACES.findall(raw.get("prompt") or "")) - {name})
        with RotationState.mutate(evt) as state:
            state.retired.pop(name, None)
            state.retired |= dict.fromkeys(rotated, name)
        for old in sorted(rotated):
            hand_over(evt, old, name)
        return None
    retired = stopped_lane(evt) or (raw.get("to") if orders_stand_down(raw.get("message")) else None)
    if retired:
        with RotationState.mutate(evt) as state:
            state.retired.setdefault(retired, None)
    return None


def hand_over(evt: BaseHookEvent, old: str, successor: str) -> None:
    """Record in cci that `successor` replaced `old`, so a record any lane, desk or script addresses to `old` reaches `successor` too, not only this session's messages (cci #235092)."""
    if not (entry := drive_entry(evt)):
        return
    argv = ["cci", "post", "--drive", bus_name(entry), "--lane", ROOT_LANE, "--kind", "stand-down", "--to", old, "--successor", successor, "--text", f"{old} rotated to {successor}; records addressed to {old} reach {successor} too"]
    try:
        subprocess.run(argv, capture_output=True, text=True, timeout=SUCCESSOR_TIMEOUT_SECONDS, env=dict(reqenv.env_map()))
    except (OSError, subprocess.TimeoutExpired):
        return


def stand_down(evt: BaseHookEvent, lane: str) -> tuple[str, str | None]:
    """The cci record that stood `lane` down, empty when none did, and the lane at the end of its successor chain, if any."""
    if not (entry := drive_entry(evt)):
        return "", None
    argv = [*SUCCESSOR_OF.split(), lane, "--drive", bus_name(entry), "--json"]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=SUCCESSOR_TIMEOUT_SECONDS, env=dict(reqenv.env_map()), check=True)
    found = json.loads(done.stdout)
    return (f"cci #{found['record']}" if found.get("record") else "a cci stand-down") if found["stood_down"] else "", found["successor"] or None


def successor_live(evt: BaseHookEvent, lane: str) -> bool:
    """Whether `cci who` lists a live session for `lane`; outside a drive there is no registry to ask, so the session's own rotation stands."""
    if not (entry := drive_entry(evt)):
        return True
    argv = [*LIVE_LANE.split(), lane, "--drive", bus_name(entry), "--json"]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=SUCCESSOR_TIMEOUT_SECONDS, env=dict(reqenv.env_map()), check=True)
    return bool(done.stdout.strip())


def cci_successor(to: str, chain: list[str], live: bool = True) -> dict:
    return {
        SUCCESSOR_OF: json.dumps({"drive": "brook-drive", "lane": to, "stood_down": bool(chain), "successor": chain[-1] if chain else "", "chain": chain}),
        LIVE_LANE: json.dumps({"lane": chain[-1], "live": True}) if chain and live else "",
    }


@on(
    Event.PreToolUse,
    only_if=[Tool("SendMessage")],
    tests={
        Input(tool="SendMessage", tool_input={"to": "desk", "message": "status?"}, state=[RotationState(retired={"desk": "desk-2"})]): Rewrite(None, to="desk-2"),
        Input(
            tool="SendMessage", tool_input={"to": "desk", "message": "STAND-DOWN: desk-2 owns it"}, state=[RotationState(retired={"desk": "desk-2"})]
        ): Warn(pattern=r"^Warning, nothing was blocked: `desk` is already stood down for `desk-2`.*`TaskStop` `desk` instead\.$"),
        Input(
            tool="SendMessage",
            tool_input={"to": "desk", "message": "done: #12 merged"},
            agent_id="a1b2c3",
            state=[RotationState(retired={"desk": "desk-2", "desk-2": "desk-3"})],
        ): Rewrite(None, to="desk-3"),
        Input(
            tool="SendMessage", tool_input={"to": "desk", "message": "done: #12 merged"}, agent_id="a1b2c3", env=DRIVE_CONFIG, commands=cci_successor("desk", ["desk-2"])
        ): Rewrite(None, to="desk-2"),
        Input(tool="SendMessage", tool_input={"to": "desk", "message": "done: #12 merged"}, agent_id="a1b2c3", env=DRIVE_CONFIG, commands=cci_successor("desk", [])): Allow(),
        Input(
            tool="SendMessage", tool_input={"to": "flock-race-1", "message": "pick A"}, env=DRIVE_CONFIG, commands=cci_successor("flock-race-1", ["flock-race-2"], live=False)
        ): Warn(pattern=r"^Warning, nothing was blocked: `flock-race-1` is stood down for `flock-race-2`, which `cci who` lists no live session for, so this message goes to `flock-race-1` as addressed\.$"),
        Input(
            tool="SendMessage",
            tool_input={"to": "desk", "message": "done: #12 merged"},
            agent_id="a1b2c3",
            env=DRIVE_CONFIG,
            commands={SUCCESSOR_OF: json.dumps({"drive": "brook-drive", "lane": "desk", "stood_down": True, "successor": "", "chain": []}), LIVE_LANE: ""},
        ): Rewrite(None, to="main"),
        Input(
            tool="SendMessage",
            tool_input={"to": "desk", "message": "status?"},
            env=DRIVE_CONFIG,
            commands={SUCCESSOR_OF: json.dumps({"drive": "brook-drive", "lane": "desk", "stood_down": True, "successor": "", "chain": []}), LIVE_LANE: ""},
        ): Warn(pattern=r"^Warning, nothing was blocked: `desk` is stood down with no successor by a cci stand-down"),
        Input(
            tool="SendMessage",
            tool_input={"to": "board-refresher-6", "message": "status?"},
            env=DRIVE_CONFIG,
            commands={SUCCESSOR_OF: json.dumps({"drive": "brook-drive", "lane": "board-refresher-6", "stood_down": True, "successor": "", "chain": [], "record": 299412}), LIVE_LANE: ""},
        ): Warn(pattern=r"^Warning, nothing was blocked: `board-refresher-6` is stood down with no successor by cci #299412, and this message resumes it"),
        Input(
            tool="SendMessage",
            tool_input={"to": "release-5", "message": "status?"},
            env=DRIVE_CONFIG,
            commands={SUCCESSOR_OF: json.dumps({"drive": "brook-drive", "lane": "release-5", "stood_down": True, "successor": "", "chain": []}), LIVE_LANE: json.dumps({"lane": "release-5", "live": True})},
        ): Allow(),
        Input(
            tool="SendMessage",
            tool_input={"to": "release-5", "message": "status?"},
            agent_id="a1b2c3",
            env=DRIVE_CONFIG,
            commands={SUCCESSOR_OF: json.dumps({"drive": "brook-drive", "lane": "release-5", "stood_down": True, "successor": "", "chain": []}), LIVE_LANE: json.dumps({"lane": "release-5", "live": True})},
        ): Allow(),
        Input(tool="SendMessage", tool_input={"to": "desk", "message": "status?"}, agent_id="a1b2c3", state=[RotationState(retired={"desk": None})]): Rewrite(None, to="main"),
        Input(tool="SendMessage", tool_input={"to": "desk", "message": "status?"}, state=[RotationState(retired={"desk": None})]): Allow(),
        Input(tool="SendMessage", tool_input={"to": "desk-2", "message": "status?"}, state=[RotationState(retired={"desk": "desk-2"})]): Allow(),
    },
)
def redirect_retired_lanes(evt: BaseHookEvent) -> HookResult | None:
    raw = evt.input.raw
    if not (to := raw.get("to")):
        return None
    retired = RotationState.load(evt).retired
    recorded, successor = stand_down(evt, to)
    successor = successor or (heir(retired, to) if to in retired else None)
    if successor and not successor_live(evt, successor):
        return evt.context(f"Warning, nothing was blocked: `{to}` is stood down for `{successor}`, which `cci who` lists no live session for, so this message goes to `{to}` as addressed.")
    if successor and mentions_stand_down(raw.get("message")):
        return evt.context(f"Warning, nothing was blocked: `{to}` is already stood down for `{successor}`, and this message resumes it on its stale brief. `TaskStop` `{to}` instead.")
    if successor:
        stop = f" `TaskStop` `{to}` if it still runs." if evt.agent_id is None else ""
        return evt.rewrite(raw | {"to": successor}, note=f"`{to}` is stood down, so this message went to its successor `{successor}`, which owns its work.{stop}")
    if recorded and successor_live(evt, to):
        return None
    if evt.agent_id is not None and (recorded or to in retired):
        return evt.rewrite(raw | {"to": MAIN}, note=f"`{to}` is stood down with no successor by {recorded or 'the rotation in this session'}, so this message went to `{MAIN}` instead of resuming it on its stale brief.")
    if recorded:
        return evt.context(f"Warning, nothing was blocked: `{to}` is stood down with no successor by {recorded}, and this message resumes it on its stale brief.")
    return None
