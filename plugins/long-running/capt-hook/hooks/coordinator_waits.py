from __future__ import annotations

from captain_hook import Allow, Annotated, BaseHookEvent, Event, FromSubagent, Input, Or, Runs, Tool, Warn, hook

from .compaction_handoff import Coordinator, coordinator_tests
from .lane_rotation import DriveActive
from .tests.root_fixtures import ACTIVE

NEXT = "lr coordinator next --drive d --cursor /drive/root/next.cursor"
NEXT_TIMEOUT_MS = 590_000


class TailsRoot:
    def check(self, evt: BaseHookEvent) -> bool:
        return any(call.args[:1] == ("tail",) and ("--reader=root" in call.args or ("--reader", "root") in zip(call.args, call.args[1:])) for call in evt.command.calls("cci"))


class WaitCutShort:
    def check(self, evt: BaseHookEvent) -> bool:
        return int(evt.input.raw.get("timeout") or 0) < NEXT_TIMEOUT_MS


hook(
    Event.PreToolUse,
    message="Warning, nothing was blocked: Every wake re-reads the coordinator's whole context, so it waits one way. Run `lr coordinator next`, which returns one batch of records.",
    only_if=[Coordinator(), DriveActive(), Or(Tool("Monitor"), Runs("cci", "watch"), TailsRoot())],
    skip_if=[FromSubagent(), Annotated("raw")],
    tests=coordinator_tests({
        Input(tool="Monitor", tool_input={"description": "the coordinator's records", "timeout_ms": 1800000, "command": "cci watch --drive d --reader root"}, state=ACTIVE): Warn(pattern=r"lr coordinator next"),
        Input(command="cci watch --drive d --reader root --cursor c", state=ACTIVE): Warn(),
        Input(command="cci tail --drive d --reader root --since 9", state=ACTIVE): Warn(),
        Input(command="cci tail --drive d --lane release-5 --since 9", state=ACTIVE): Allow(),
        Input(command=NEXT, state=ACTIVE): Allow(),
        Input(command="cci watch --drive d --reader root # ccx:raw", state=ACTIVE): Allow(),
        Input(command="cci watch --drive d --reader root", agent_id="a1b2c3", state=ACTIVE): Allow(),
        Input(command="cci watch --drive d --reader root"): Allow(),
    }),
)

hook(
    Event.PreToolUse,
    message="Warning, nothing was blocked: `lr coordinator next` waits up to 570 seconds and the default Bash timeout kills it first. Run it with `timeout: 590000`.",
    only_if=[Coordinator(), DriveActive(), Runs("lr", "coordinator", "next"), WaitCutShort()],
    skip_if=[FromSubagent()],
    tests=coordinator_tests({
        Input(command=NEXT, state=ACTIVE): Warn(pattern=r"timeout: 590000"),
        Input(tool="Bash", tool_input={"command": NEXT, "timeout": NEXT_TIMEOUT_MS}, state=ACTIVE): Allow(),
        Input(command="lr lanes --help", state=ACTIVE): Allow(),
        Input(command=NEXT): Allow(),
    }),
)
