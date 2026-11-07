from __future__ import annotations

from captain_hook import Allow, Annotated, BaseHookEvent, Block, Event, HookResult, Input, Tool, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .lane_rotation import alias_of, route, routed
from .meta_records import post_records
from .teammate_posts import BRIEFED, TEAMMATE
from .teammate_posts import DONE as TEAMMATE_DONE
from .tests.register_fixtures import DRIVE_CONFIG

DESK_DONE = "A desk never posts `--kind done`, since done retires the lane and the drive loses its desk. Post a finished PR as `cci post --kind landed --pr <n>` and keep the desk running."
OBS_DONE = "cci post --drive d --lane obs-desk --kind done --to meta-desk --text 'merged #3465'"


def is_desk(evt: BaseHookEvent, lane: str) -> bool:
    return alias_of(lane).endswith("-desk") or bool((routed(evt, lane) or {}).get("standing"))


class DeskPostsDone:
    def check(self, evt: BaseHookEvent) -> bool:
        return any("done" in post.kind.lower().split(",") and post.lane and is_desk(evt, post.lane) for post in post_records(evt))


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", POST_TOOL), DeskPostsDone()],
    skip_if=[Annotated("raw")],
    tests={
        Input(command=OBS_DONE, env=DRIVE_CONFIG, commands=route("obs-desk", "obs-desk", "desired", meta=True)): Block(pattern=r"^A desk never posts `--kind done`[\s\S]*`cci post --kind landed --pr <n>`"),
        Input(command=OBS_DONE.replace("--lane obs-desk ", ""), env=DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": "obs-desk"}, commands=route("obs-desk", "obs-desk", "desired", meta=True)): Block(),
        Input(command=OBS_DONE.replace("obs-desk", "meta-desk-3"), env=DRIVE_CONFIG, commands=route("meta-desk-3", "meta-desk-3", "desired", meta=True)): Block(),
        Input(command=TEAMMATE_DONE, env=TEAMMATE, commands=route("meta-desk-3", "meta-desk-3", "desired", meta=True)): Block(),
        Input(command=TEAMMATE_DONE, env=TEAMMATE, agent_id="a1b2c3", transcript=BRIEFED, commands=route("live-tasks-parse", "live-tasks-parse", "desired")): Allow(),
        Input(command=OBS_DONE.replace("obs-desk", "board-refresher-7"), env=DRIVE_CONFIG, commands=route("board-refresher-7", "board-refresher-7", "desired", standing="refresh the board")): Block(),
        Input(tool=POST_TOOL, tool_input={"lane": "obs-desk", "kind": "done", "to": ["meta-desk"], "text": "merged #3465"}, env=DRIVE_CONFIG, commands=route("obs-desk", "obs-desk", "desired", meta=True)): Block(),
        Input(command="cci post --drive d --lane live-claim-scope --kind done --to meta-desk --text 'merged #3470'", env=DRIVE_CONFIG, commands=route("live-claim-scope", "root", "desired", meta=True)): Allow(),
        Input(command="cci post --drive d --lane obs-desk --kind landed --pr yasyf/cc-skills#3465 --to meta-desk --text 'merged #3465'", env=DRIVE_CONFIG, commands=route("obs-desk", "obs-desk", "desired", meta=True)): Allow(),
        Input(command=f"{OBS_DONE}  # ccx:raw", env=DRIVE_CONFIG, commands=route("obs-desk", "obs-desk", "desired", meta=True)): Allow(),
        Input(command=OBS_DONE): Allow(),
    },
)
def desks_never_post_done(evt: BaseHookEvent) -> HookResult:
    return evt.block(DESK_DONE)
