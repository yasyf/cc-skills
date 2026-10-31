from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Tool, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import CCN_TIMEOUT_SECONDS, CompactionState
from .lane_landing import lane_name
from .lane_rotation import DriveActive

ACTIVE = [CompactionState(active=True)]
LANE = "atooling-misc-3-0123456789abcdef"
SPAWN = {"name": "hook-fix", "description": "fix a hook", "prompt": "Fix the hook."}
REFUSAL = (
    "A lane never spawns a sub-worker with Agent, since a teammate cannot background the call and `{lane}` would wait inside it. "
    "Write the brief to a file, post `cci post --lane {lane} --kind ask --to root --text 'spawn <name> brief=<path> model=<opus|sonnet>'`, and keep working."
)


@on(  # capt-hook: no-escape root spawns every sub-worker, root #235843
    Event.PreToolUse,
    only_if=[Tool("Agent"), DriveActive()],
    tests={
        Input(tool="Agent", tool_input=SPAWN, agent_id=LANE, state=ACTIVE): Block(pattern=r"^A lane never spawns a sub-worker with Agent, since .* `tooling-misc-3` would wait inside it\..*--lane tooling-misc-3 --kind ask --to root"),
        Input(tool="Agent", tool_input=SPAWN, env={"CLAUDE_LONG_RUNNING_LANE": "orca-lane-2"}, state=ACTIVE): Block(pattern=r"`orca-lane-2` would wait"),
        Input(tool="Agent", tool_input=SPAWN, state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input=SPAWN, agent_id="a0123456789abcdef", state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input=SPAWN, agent_id=LANE): Allow(),
    },
)
def lanes_never_spawn(evt: BaseHookEvent) -> HookResult | None:
    if not (lane := lane_name(evt)):
        return None
    return evt.block(REFUSAL.format(lane=lane))


LIVE_WINDOW = timedelta(minutes=10)
ALREADY_LIVE = (
    "Warning, nothing was blocked: `{name}` already runs in the session at {where}, seen {ago} min ago, last saying `{said}`. "
    "A second copy repeats its work; message it or wait for its report unless it stopped."
)
COPY_LIMIT = 300
WHO = "cci who --json"
FAULTRUN = {"name": "p8a7-eviction-faultrun-2", "description": "rerun the eviction fault run", "prompt": "Run it."}


def peer(lane: str, minutes_ago: int, **fields: object) -> str:
    seen = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")
    return json.dumps({"session": "5c4f0b9e-1111-2222-3333-444455556666", "drive": "brook", "lane": lane, "worktree": "/Users/dev/.orca/workspaces/monorepo/amberjack", "seen_at": seen, "live": True} | fields) + "\n"


LAST = json.dumps({"seq": 256001, "kind": "state", "at": "2026-10-31T15:10:00Z", "text": "fault run 2 of 3 on the VM, next state after run 3"}) + "\n"


def run(argv: list[str]) -> str | None:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=CCN_TIMEOUT_SECONDS, env=dict(reqenv.env_map()))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None if done.returncode else done.stdout


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent"), DriveActive()],
    tests={
        Input(tool="Agent", tool_input=FAULTRUN, state=ACTIVE, commands={WHO: peer("p8a7-eviction-faultrun-2", 3), "cci tail": LAST}): Warn(
            pattern=r'^Warning, nothing was blocked: `p8a7-eviction-faultrun-2` already runs in the session at amberjack, seen 3 min ago, last saying `fault run 2 of 3'
        ),
        Input(tool="Agent", tool_input=FAULTRUN, state=ACTIVE, commands={WHO: peer("p8a7-eviction-faultrun-2", 25), "cci tail": LAST}): Allow(),
        Input(tool="Agent", tool_input=FAULTRUN, state=ACTIVE, commands={WHO: peer("p8a7-eviction-faultrun-2", 3, stood_down=True), "cci tail": LAST}): Allow(),
        Input(tool="Agent", tool_input=FAULTRUN, state=ACTIVE, commands={WHO: peer("p8a7-eviction-faultrun-1", 3), "cci tail": LAST}): Allow(),
        Input(tool="Agent", tool_input=FAULTRUN, commands={WHO: peer("p8a7-eviction-faultrun-2", 3), "cci tail": LAST}): Allow(),
    },
)
def spawn_names_a_live_lane(evt: BaseHookEvent) -> HookResult | None:
    if not (name := evt.input.raw.get("name")) or (who := run(WHO.split())) is None:
        return None
    now = datetime.now(UTC)
    for line in who.splitlines():
        if not line.strip():
            continue
        found = json.loads(line)
        seen = datetime.fromisoformat(found["seen_at"].replace("Z", "+00:00"))
        if found.get("lane") != name or not found.get("live") or found.get("stood_down") or now - seen > LIVE_WINDOW:
            continue
        tail = run(["cci", "tail", "--drive", found["drive"], "--lane", name, "--since", "0", "--limit", "1", "--json"])
        said = " ".join(json.loads(tail.splitlines()[-1])["text"].replace("`", "'").split()) if tail and tail.strip() else "nothing readable"
        where = Path(found["worktree"]).name if found.get("worktree") else "another worktree"
        fields = {"name": name, "where": where, "ago": int((now - seen).total_seconds() // 60)}
        room = COPY_LIMIT - len(ALREADY_LIVE.format(said="", **fields))
        return evt.context(ALREADY_LIVE.format(said=said if len(said) <= room else said[: room - 1] + "…", **fields))
    return None
