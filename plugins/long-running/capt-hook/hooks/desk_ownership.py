from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Block, Event, Input, Tool, hook
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState, Communicator, communicator_tests

ACTIVE = [CompactionState(active=True, communicator=True)]
LANE_ENV = "CLAUDE_LONG_RUNNING_LANE"
STANDING = re.compile(r"(?:landing-desk|fidelity-desk|fidelity-sweep|red-sweep|alerts-desk|drive-watchdog|board-refresher)(?:-\d+)?")
STARTS = {"lr": (("relay", "run"),)}


class StartsRunnerOrDesk:
    """A command starting a runner or standing desk from a window its launcher named no lane; a launched lane, such as the drive's installer, acts for the coordinator."""

    def check(self, evt: BaseHookEvent) -> bool:
        if reqenv.getenv(LANE_ENV):
            return False
        starts_runner = any(call.args[: len(verb)] == verb for call in evt.command.calls() for verb in STARTS.get(call.name, ()))
        lane_launches = [call.args for call in evt.command.calls("desk-launch.py")] + [call.args[1:] for call in evt.command.calls("lr") if call.args[:1] == ("launch",)]
        return starts_runner or any(args[:1] == ("lane",) and STANDING.fullmatch("".join(args[1:2])) for args in lane_launches)


hook(  # capt-hook: no-escape the coordinator is the one owner of every runner and desk, and the message names the record that reaches it
    Event.PreToolUse,
    block=True,
    only_if=[Tool("Bash"), Communicator(), StartsRunnerOrDesk()],
    message="The coordinator owns the relay and every desk, so the communicator starts none of them. Post it to the coordinator: `cci post --lane owner-relay --kind ask --to root --text 'start <desk>'`.",
    tests=communicator_tests({
        Input(command="lr relay run --config lr.json", state=ACTIVE): Block(pattern=r"^The coordinator owns the relay and every desk, .*--to root"),
        Input(command="/plugin/bin/lr relay run --config lr.json", state=ACTIVE): Block(),
        Input(command="desk-launch.py lane landing-desk opus xhigh b.md", state=ACTIVE): Block(),
        Input(command="desk-launch.py lane drive-watchdog-4 opus xhigh b.md", state=ACTIVE): Block(),
        Input(command="lr launch lane landing-desk opus xhigh b.md", state=ACTIVE): Block(),
        Input(command="/plugin/bin/lr launch lane drive-watchdog-4 opus xhigh b.md", state=ACTIVE): Block(),
        Input(command="lr relay run --config lr.json", state=ACTIVE, env={LANE_ENV: "tooling-runner-1"}): Allow(),
        Input(command="lr install check --config lr.json", state=ACTIVE): Allow(),
        Input(command="lr relay rebind --config lr.json", state=ACTIVE): Allow(),
        Input(command="lr relay run --config lr.json"): Allow(),
        Input(command="desk-launch.py lane signal sonnet low b.md", state=ACTIVE): Allow(),
        Input(command="desk-launch.py lane landing-desk-fix opus xhigh b.md", state=ACTIVE): Allow(),
        Input(command="lr launch lane signal sonnet low b.md", state=ACTIVE): Allow(),
        Input(command="lr launch lane landing-desk-fix opus xhigh b.md", state=ACTIVE): Allow(),
        Input(command="echo lr relay run --config lr.json", state=ACTIVE): Allow(),
    }),
)
