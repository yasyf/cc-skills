from __future__ import annotations

from captain_hook import Allow, Annotated, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, T, Tool, on
from captain_hook.annotations import dispatch_pairs

from .compaction_handoff import CompactionState
from .lane_rotation import DriveActive
from .window_lane import Coordinator, coordinator_tests

LANE_TASKS = frozenset({"subagent", "teammate", "workflow", "cloud session"})
LANE = {"type": "subagent", "status": "running", "description": "spot-pool lane"}
SPAWNED = [T.user("keep going"), T.assistant(T.tool("Agent", name="spot-pool", prompt="Raise the spot quota.", run_in_background=True))]
WATCH = {"type": "monitor", "status": "running", "description": "cci watch"}
PARKED = (
    "A drive root never parks on `AskUserQuestion` while lanes run. Post it with `cc-present ask <card>` or as a chat line naming a default, "
    "proceed on that default, and apply the answer when it arrives; add a `ccx: ask=blocking` line only when a lane is truly blocked on it."
)


def question(text: str, **call: object) -> Input:
    asked = {"questions": [{"question": text, "header": "Ask", "options": [{"label": "Go"}, {"label": "Hold"}], "multiSelect": False}]}
    return Input(tool="AskUserQuestion", tool_input=asked, **call)


def live(*background: dict) -> list[CompactionState]:
    return [CompactionState(active=True, background=list(background))]


def lanes_live(evt: BaseHookEvent) -> bool:
    background = CompactionState.load(evt).background
    return any(task.get("type") in LANE_TASKS for task in background) or bool(evt.ctx.turn.tool_calls.named("Agent").items)


def declared_blocking(evt: BaseHookEvent) -> bool:
    texts = [asked.get("question", "") for asked in evt._tool_input.get("questions") or []]
    return any(key == "ask" and value == "blocking" for text in texts for key, value in dispatch_pairs(text))


@on(
    Event.PreToolUse,
    only_if=[Coordinator(), Tool("AskUserQuestion"), DriveActive()],
    skip_if=[FromSubagent(), Annotated("raw")],
    tests=coordinator_tests({
        question("Carry a fourth Dagger patch?", state=live(LANE, WATCH)): Block(pattern=r"^A drive root never parks on `AskUserQuestion` while lanes run\..*`cc-present ask <card>`"),
        question("Raise the spot quota?", state=live(), transcript=SPAWNED): Block(pattern="never parks"),
        question("Carry a fourth Dagger patch?", state=live(WATCH)): Allow(),
        question("Carry a fourth Dagger patch?", state=live(LANE), agent_id="a1b2c3"): Allow(),
        question("Carry a fourth Dagger patch?", state=[CompactionState(background=[LANE])]): Allow(),
        question("Carry a fourth Dagger patch?", state=live(LANE), env={"CAPT_HOOK_CCX_RAW": "1"}): Allow(),
        question("Which spot pool takes the quota?\nccx: ask=blocking", state=live(LANE)): Allow(),
    }),
)
def drive_roots_never_park_on_asks(evt: BaseHookEvent) -> HookResult | None:
    if not lanes_live(evt) or declared_blocking(evt):
        return None
    return evt.block(PARKED)
