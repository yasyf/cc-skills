from __future__ import annotations

import subprocess
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Warn, WorkflowState, on
from captain_hook.util import reqenv

from .compaction_handoff import drive_entry
from .session_tree import ROOT_NAMES, drive_lane
from .tests.register_fixtures import DRIVE_CONFIG

LR = Path(__file__).parents[2] / "bin" / "lr"
HANDOFF_SECONDS = 60
THREAD = "root"
LANE_ENV = DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": "fix-lane-1"}
ROOT_ENV = DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": "root"}
WRITE = f"{LR} lanes handoff fix-lane-1"


class LaneHandoffs(WorkflowState):
    docs: dict[str, str] = {}


def lane_of(evt: BaseHookEvent) -> str | None:
    lane = drive_lane(evt)
    return None if not lane or lane == THREAD or lane in ROOT_NAMES else lane


@on(
    Event.PreCompact,
    tests={
        Input(env=LANE_ENV, commands={WRITE: "abc1234\n"}): Allow(),
        Input(env=LANE_ENV, commands={WRITE: (1, "the blocker is posted")}): Allow(),
        Input(env=ROOT_ENV): Allow(),
        Input(): Allow(),
    },
)
def lane_writes_its_handoff_before_compacting(evt: BaseHookEvent) -> HookResult | None:
    """`lr lanes handoff` writes the doc with no model call and posts the `handoff` record, or posts the blocker when it cannot."""
    if not (lane := lane_of(evt)) or not (entry := drive_entry(evt)):
        return None
    config = Path(entry["state_dir"]) / "lr.json"
    done = subprocess.run([str(LR), "lanes", "handoff", lane, "--config", str(config), "--cwd", evt.cwd, "--transcript", evt._raw.get("transcript_path", "")], capture_output=True, text=True, timeout=HANDOFF_SECONDS, env=dict(reqenv.env_map()))
    if done.returncode == 0:
        with LaneHandoffs.mutate(evt) as state:
            state.docs[evt.agent_id or THREAD] = done.stdout.strip()
    return None


@on(
    Event.SessionStart,
    tests={
        Input(source="compact", env=LANE_ENV, state=[LaneHandoffs(docs={THREAD: "abc1234"})]): Warn(pattern=r"^You are fix-lane-1 and just compacted\. Read `ccn doc show abc1234`"),
        Input(source="compact", env=LANE_ENV): Allow(),
        Input(source="startup", env=LANE_ENV, state=[LaneHandoffs(docs={THREAD: "abc1234"})]): Allow(),
        Input(source="compact", env=ROOT_ENV, state=[LaneHandoffs(docs={THREAD: "abc1234"})]): Allow(),
    },
)
def lane_reads_its_handoff_after_compacting(evt: BaseHookEvent) -> HookResult | None:
    if evt._raw.get("source") != "compact" or not (lane := lane_of(evt)):
        return None
    doc = LaneHandoffs.load(evt).docs.get(evt.agent_id or THREAD)
    return evt.context(f"You are {lane} and just compacted. Read `ccn doc show {doc}`, your handoff, then your brief, and continue the work it names.") if doc else None
