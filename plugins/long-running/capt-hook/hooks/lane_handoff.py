from __future__ import annotations

import json
import subprocess
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import Bound, bus_name, drive_entry
from .session_tree import ROOT_NAMES, drive_lane
from .tests.register_fixtures import DRIVE_CONFIG

LR = Path(__file__).parents[2] / "bin" / "lr"
HANDOFF_SECONDS = 60
DESK_WAIT = "45s"
READ_SECONDS = 10
THREAD = "root"
LANE_ENV = DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": "fix-lane-1"}
ROOT_ENV = DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": "root"}
WRITE = f"{LR} lanes handoff fix-lane-1"
HANDOFF_RECORD = json.dumps({"kind": "handoff", "refs": {"ccn": "abc1234"}}) + "\n"


def latest_handoff(evt: BaseHookEvent, lane: str) -> str | None:
    if not (entry := drive_entry(evt)):
        return None
    argv = ["cci", "tail", "--drive", bus_name(entry), "--lane", lane, "--kind", "handoff", "--since", "24h", "--json"]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=READ_SECONDS, env=dict(reqenv.env_map()))
    lines = done.stdout.splitlines() if done.returncode == 0 else []
    return json.loads(lines[-1])["refs"]["ccn"] if lines else None


def lane_of(evt: BaseHookEvent) -> str | None:
    lane = drive_lane(evt)
    return None if not lane or lane == THREAD or lane in ROOT_NAMES else lane


@on(
    Event.PreCompact,
    only_if=[Bound()],
    tests={
        Input(env=LANE_ENV, commands={WRITE: "abc1234\n"}): Allow(),
        Input(env=LANE_ENV, commands={WRITE: (1, ""), "cci post": ""}): Warn(pattern=r"^No handoff doc was written before this compaction, and a blocker told root: fix-lane-1 compacted with no handoff: lr lanes handoff exited 1"),
        Input(env=ROOT_ENV): Allow(),
        Input(): Allow(),
    },
)
def lane_writes_its_handoff_before_compacting(evt: BaseHookEvent) -> HookResult | None:
    """`lr lanes handoff` writes the doc with no model call and posts the `handoff` record, or posts the blocker when it cannot; a run that exits non-zero naming no blocker gets one here."""
    if not (lane := lane_of(evt)) or not (entry := drive_entry(evt)):
        return None
    config = Path(entry["state_dir"]) / "lr.json"
    done = subprocess.run([str(LR), "lanes", "handoff", lane, "--config", str(config), "--cwd", evt.cwd, "--transcript", evt._raw.get("transcript_path", ""), "--desk-wait", DESK_WAIT], capture_output=True, text=True, timeout=HANDOFF_SECONDS, env=dict(reqenv.env_map()))
    if done.returncode == 0 or done.stdout.startswith("blocker "):
        return None
    text = f"{lane} compacted with no handoff: lr lanes handoff exited {done.returncode}: {done.stderr.strip()[-400:]}"
    subprocess.run(["cci", "post", "--drive", bus_name(entry), "--lane", lane, "--kind", "blocker", "--to", "root", "--text", text], capture_output=True, text=True, timeout=READ_SECONDS, env=dict(reqenv.env_map()))
    return evt.context(f"No handoff doc was written before this compaction, and a blocker told root: {text}")


@on(
    Event.SessionStart,
    only_if=[Bound()],
    tests={
        Input(source="compact", env=LANE_ENV, commands={"cci tail": HANDOFF_RECORD}): Warn(pattern=r"^You are fix-lane-1 and just compacted\. Read `ccn doc show abc1234`"),
        Input(source="compact", env=LANE_ENV, commands={"cci tail": ""}): Allow(),
        Input(source="startup", env=LANE_ENV): Allow(),
        Input(source="compact", env=ROOT_ENV): Allow(),
    },
)
def lane_reads_its_handoff_after_compacting(evt: BaseHookEvent) -> HookResult | None:
    if evt._raw.get("source") != "compact" or not (lane := lane_of(evt)):
        return None
    doc = latest_handoff(evt, lane)
    return evt.context(f"You are {lane} and just compacted. Read `ccn doc show {doc}`, your handoff, then your brief, and continue the work it names.") if doc else None


@on(
    Event.PreCompact,
    only_if=[Bound()],
    tests={
        Input(env=LANE_ENV, commands={WRITE: "abc1234\n"}): Warn(pattern=r"^In this summary, name other lanes by alias"),
        Input(env=ROOT_ENV): Warn(pattern=r"never a numbered name"),
        Input(): Allow(),
    },
)
def summaries_name_lanes_by_alias(evt: BaseHookEvent) -> HookResult | None:
    return evt.context("In this summary, name other lanes by alias, such as tap-rows, never a numbered name such as tap-rows-1.")


@on(
    Event.SessionStart,
    only_if=[Bound()],
    tests={
        Input(source="compact", env=ROOT_ENV): Warn(pattern=r"^Lanes go by alias"),
        Input(source="resume", env=LANE_ENV): Warn(pattern=r"stays numbered where a tool needs it\.$"),
        Input(source="startup", env=LANE_ENV): Allow(),
        Input(source="compact"): Allow(),
    },
)
def compacted_sessions_name_lanes_by_alias(evt: BaseHookEvent) -> HookResult | None:
    if evt._raw.get("source") not in {"compact", "resume"}:
        return None
    return evt.context("Lanes go by alias: name other lanes as tap-rows, never tap-rows-1, in every record, reply and status line. Your own lane's binding stays numbered where a tool needs it.")
