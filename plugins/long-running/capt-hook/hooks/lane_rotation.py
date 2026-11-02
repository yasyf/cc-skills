from __future__ import annotations

import itertools
import json
import re
import subprocess
import sys
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Rewrite, Tool, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState, drive_entry, script
from .tests.register_fixtures import DRIVE_CONFIG

MENTIONS_STAND_DOWN = re.compile(r"\bstand[\s-]?down\b", re.IGNORECASE)
MAIN = "main"
ROOT = "root"
ROUTE = f"{sys.executable} {script('drive_supervisor.py')} route"
ROUTE_TIMEOUT_SECONDS = 10


class DriveActive:
    def check(self, evt: BaseHookEvent) -> bool:
        return CompactionState.load(evt).active


def route(lane: str, to: str, state: str = "retired") -> dict:
    return {ROUTE: json.dumps({"lane": lane, "route": to, "state": state})}


def routed(evt: BaseHookEvent, lane: str) -> dict | None:
    """The lane supervisor's registry answer for `lane`: where its mail goes and its row's state; None outside a drive, where no registry exists."""
    if not (entry := drive_entry(evt)):
        return None
    argv = [sys.executable, str(script("drive_supervisor.py")), "route", lane, "--config", str(Path(entry["state_dir"]) / "lr.json")]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=ROUTE_TIMEOUT_SECONDS, env=dict(reqenv.env_map()), check=True)
    return json.loads(done.stdout)


def mentions_stand_down(message: object) -> bool:
    return isinstance(message, str) and MENTIONS_STAND_DOWN.search(message) is not None


@on(
    Event.PreToolUse,
    only_if=[Tool("SendMessage")],
    tests={
        Input(tool="SendMessage", tool_input={"to": "desk", "message": "status?"}, env=DRIVE_CONFIG, commands=route("desk", "restack-desk")): Rewrite(None, to="restack-desk"),
        Input(tool="SendMessage", tool_input={"to": "desk", "message": "done: #12 merged"}, agent_id="a1b2c3", env=DRIVE_CONFIG, commands=route("desk", "desk-3")): Rewrite(None, to="desk-3"),
        Input(tool="SendMessage", tool_input={"to": "desk", "message": "done: #12 merged"}, agent_id="a1b2c3", env=DRIVE_CONFIG, commands=route("desk", ROOT)): Rewrite(None, to=MAIN),
        Input(tool="SendMessage", tool_input={"to": "desk", "message": "status?"}, env=DRIVE_CONFIG, commands=route("desk", ROOT)): Warn(pattern=r"^Warning, nothing was blocked: `desk` is stood down with no successor"),
        Input(tool="SendMessage", tool_input={"to": "desk", "message": "STAND-DOWN: desk-2 owns it"}, env=DRIVE_CONFIG, commands=route("desk", "desk-2")): Warn(pattern=r"`TaskStop` `desk` instead\.$"),
        Input(tool="SendMessage", tool_input={"to": "desk-2", "message": "status?"}, env=DRIVE_CONFIG, commands=route("desk-2", "desk-2", "desired")): Allow(),
        Input(tool="SendMessage", tool_input={"to": "desk", "message": "status?"}): Allow(),
    },
)
def redirect_retired_lanes(evt: BaseHookEvent) -> HookResult | None:
    raw = evt.input.raw
    if not (to := raw.get("to")) or not (found := routed(evt, to)) or found["route"] == to:
        return None
    target = MAIN if found["route"] == ROOT else found["route"]
    if mentions_stand_down(raw.get("message")):
        return evt.context(f"Warning, nothing was blocked: `{to}` is already stood down{f' for `{target}`' if target != MAIN else ''}, and this message resumes it on its stale brief. `TaskStop` `{to}` instead.")
    if target == MAIN and evt.agent_id is None:
        return evt.context(f"Warning, nothing was blocked: `{to}` is stood down with no successor in the lane supervisor's registry, and this message resumes it on its stale brief.")
    stop = f" `TaskStop` `{to}` if it still runs." if evt.agent_id is None else ""
    went = f"its successor `{target}`, which owns its work" if target != MAIN else f"`{MAIN}` instead of resuming it on its stale brief"
    return evt.rewrite(raw | {"to": target}, note=f"`{to}` is stood down in the lane supervisor's registry, so this message went to {went}.{stop}")


def stands_down_another(evt: BaseHookEvent) -> str | None:
    """The lane a `cci post --kind stand-down` names that is not the poster's own, or None."""
    for call in evt.command.calls("cci"):
        args = list(call.args)
        if args[:1] != ["post"]:
            continue
        flags = dict(itertools.pairwise(args))
        posted = {arg.split("=", 1)[0]: arg.split("=", 1)[1] for arg in args if arg.startswith("--") and "=" in arg}
        if (flags.get("--kind") or posted.get("--kind")) != "stand-down":
            continue
        poster = flags.get("--lane") or posted.get("--lane") or ROOT
        named = [value for flag, value in itertools.pairwise(args) if flag == "--to"] + [value for flag, value in posted.items() if flag == "--to"]
        if others := [lane for target in named for lane in target.split(",") if lane.split("@", 1)[0] != poster]:
            return others[0]
    return None


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        Input(tool="Bash", tool_input={"command": "cci post --lane root --kind stand-down --to conflicts-2 --successor conflicts-3 --text 'done'"}): Block(pattern=r"^Only the lane supervisor stands a lane down"),
        Input(tool="Bash", tool_input={"command": "cci post --lane conflicts-2 --kind stand-down --to root --text 'standing down'"}): Block(pattern=r"`root`"),
        Input(tool="Bash", tool_input={"command": "cci post --lane conflicts-2 --kind stand-down --to conflicts-2 --text 'standing down'"}): Allow(),
        Input(tool="Bash", tool_input={"command": "cci post --lane root --kind decide --to supervisor --attr verb=stand-down --attr lane=conflicts-2 --text 'x'"}): Allow(),
    },
)
def stand_downs_go_through_the_registry(evt: BaseHookEvent) -> HookResult | None:
    if not (lane := stands_down_another(evt)):
        return None
    return evt.block(f"Only the lane supervisor stands a lane down, so its registry and cci's mail routing agree. Ask it for `{lane}`: `drive_supervisor.py request stand-down <lane> --config <state dir>/lr.json --last-read <seq> [--successor <lane>]`. A lane ending its own work posts `--kind done`.")
