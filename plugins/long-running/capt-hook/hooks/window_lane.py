from __future__ import annotations

import dataclasses
import json
import subprocess

from captain_hook import Allow, Ask, BaseHookEvent, Event, FromSubagent, HookResult, Input, Tool, WorkflowState, on
from captain_hook.util import reqenv

LANE_ENV = "CLAUDE_LONG_RUNNING_LANE"
ROOT_LANES = frozenset({"root", "main"})
RELAY_LANE = "owner-relay"
BINDING_VERBS = frozenset({("announce",), ("subscribe",), ("drive", "use")})
CCI_TIMEOUT_SECONDS = 5
WHO = "cci who --session"
BOUND_RELAY = {WHO: '{"agent":"a1b2c3","lane":"bridge-split-1"}\n{"lane":"owner-relay"}\n'}
BOUND_NONE = {WHO: '{"agent":"a1b2c3","lane":"bridge-split-1"}\n'}


class WindowLane(WorkflowState):
    lane: str | None = None


def window_lane(evt: BaseHookEvent) -> str | None:
    """The lane this window is bound to: the `CLAUDE_LONG_RUNNING_LANE` its launcher named, else the cci binding of its root thread as last read; None for a window bound to no lane."""
    return reqenv.getenv(LANE_ENV) or WindowLane.load(evt).lane


class Coordinator:
    """The window the drive's root hooks act on: the root thread of a window bound `root`, or of a window bound to no lane, a drive whose one session never split into a bridge and a coordinator."""

    def check(self, evt: BaseHookEvent) -> bool:
        return evt.agent_id is None and window_lane(evt) in (None, *ROOT_LANES)


class OwnerBridge:
    """The window the owner types in: the root thread of a window bound `owner-relay`, or of a window bound to no lane."""

    def check(self, evt: BaseHookEvent) -> bool:
        return evt.agent_id is None and window_lane(evt) in (None, RELAY_LANE)


def bound(case: Input, lane: str) -> Input:
    states = case.state if isinstance(case.state, list) else [case.state] if case.state else []
    return dataclasses.replace(case, state=[*states, WindowLane(lane=lane)])


def fired(expected: object) -> bool:
    return not isinstance(expected, Ask) and (not isinstance(expected, Allow) or expected.system_message is not None)


def lane_tests(tests: dict, fires: str, quiet: str) -> dict:
    """`tests` plus its first root-thread case, the first that fires when one does, once in a window bound `fires`, with the same outcome, and once in a window bound `quiet`, where the hook returns nothing."""
    cases = [(case, expected) for case, expected in tests.items() if isinstance(case, Input) and not case.agent_id]
    case, expected = next(((case, expected) for case, expected in cases if fired(expected)), cases[0])
    return tests | {bound(case, fires): expected, bound(case, quiet): Ask()}


def coordinator_tests(tests: dict) -> dict:
    return lane_tests(tests, "root", RELAY_LANE)


def bridge_tests(tests: dict) -> dict:
    return lane_tests(tests, RELAY_LANE, "root")


def read_window_lane(evt: BaseHookEvent) -> None:
    try:
        done = subprocess.run(["cci", "who", "--session", evt.session_id, "--json", "--fields", "agent,lane"], capture_output=True, text=True, timeout=CCI_TIMEOUT_SECONDS, env=dict(reqenv.env_map()))
    except (OSError, subprocess.TimeoutExpired):
        return
    if done.returncode != 0:
        return
    rows = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
    with WindowLane.mutate(evt) as state:
        state.lane = next((row.get("lane") or None for row in rows if not row.get("agent")), None)


@on(
    Event.SessionStart,
    skip_if=[FromSubagent()],
    tests={
        Input(source="startup", commands=BOUND_RELAY): Allow(),
        Input(source="startup", commands=BOUND_NONE): Allow(),
    },
)
def read_window_lane_at_start(evt: BaseHookEvent) -> HookResult | None:
    read_window_lane(evt)
    return None


@on(
    Event.PostToolUse,
    only_if=[Tool("Bash")],
    skip_if=[FromSubagent()],
    tests={
        Input(command="cci subscribe --drive brook --reader owner-relay --kind report", commands=BOUND_RELAY): Allow(),
        Input(command="cci announce --lane root", commands=BOUND_NONE): Allow(),
        Input(command="git status"): Allow(),
    },
)
def read_window_lane_after_a_binding(evt: BaseHookEvent) -> HookResult | None:
    """A `cci announce`, `subscribe` or `drive use` can move this window's binding, and a first `cci post` can make one, so each rereads it; a post once the window is bound moves nothing."""
    verbs = [tuple(call.args[: len(verb)]) for call in evt.command.calls("cci") for verb in (*BINDING_VERBS, ("post",))]
    if any(verb in BINDING_VERBS for verb in verbs) or ("post",) in verbs and WindowLane.load(evt).lane is None:
        read_window_lane(evt)
    return None
