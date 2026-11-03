from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Block, Event, Input, Tool, hook
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState, Communicator, communicator_tests

ACTIVE = [CompactionState(active=True)]
LANE_ENV = "CLAUDE_LONG_RUNNING_LANE"
STANDING = re.compile(r"(?:landing-desk|fidelity-desk|red-sweep|alerts-desk|drive-watchdog|board-refresher)(?:-\d+)?")
STARTS = {"desk-runner.py": (("run",), ("handoff",), ("supervise", "install")), "desk-creds.py": (("install",),), "lr": (("desk-creds", "install"),)}


class StartsRunnerOrDesk:
    """A command starting a runner or standing desk from a window its launcher named no lane; a launched lane, such as the drive's installer, acts for the coordinator."""

    def check(self, evt: BaseHookEvent) -> bool:
        if reqenv.getenv(LANE_ENV):
            return False
        starts_runner = any(call.args[: len(verb)] == verb for call in evt.command.calls() for verb in STARTS.get(call.name, ()))
        moves_desk = any(call.args[:1] == ("install",) and "--desk" in call.args for call in evt.command.calls("lr"))
        return starts_runner or moves_desk or any(call.args[:1] == ("lane",) and STANDING.fullmatch("".join(call.args[1:2])) for call in evt.command.calls("desk-launch.py"))


hook(  # capt-hook: no-escape the coordinator is the one owner of every runner and desk, and the message names the record that reaches it
    Event.PreToolUse,
    block=True,
    only_if=[Tool("Bash"), Communicator(), StartsRunnerOrDesk()],
    message="The coordinator owns desk-runner, the launch runner and every desk, so the communicator starts none of them. Post it to the coordinator: `cci post --lane owner-relay --kind ask --to root --text 'start <desk>'`.",
    tests=communicator_tests({
        Input(command="desk-runner.py run --config r.json --desk orca", state=ACTIVE): Block(pattern=r"^The coordinator owns desk-runner, .*--to root"),
        Input(command="nohup desk-runner.py run --config r.json --desk landing >> l.log 2>&1 < /dev/null &", state=ACTIVE): Block(),
        Input(command="/plugin/bin/desk-runner.py run --config r.json --desk orca", state=ACTIVE): Block(),
        Input(command="desk-runner.py handoff --config r.json --desk orca", state=ACTIVE): Block(),
        Input(command="desk-runner.py supervise install --config r.json --desk landing", state=ACTIVE): Block(),
        Input(command="desk-creds.py install --config r.json --desk landing", state=ACTIVE): Block(),
        Input(command="lr install roll --runner r.json --desk landing", state=ACTIVE): Block(),
        Input(command="/plugin/bin/lr install 0.7.9 --desk orca --runner r.json", state=ACTIVE): Block(),
        Input(command="lr desk-creds install --config r.json --desk landing", state=ACTIVE): Block(),
        Input(command="desk-launch.py lane landing-desk opus xhigh b.md", state=ACTIVE): Block(),
        Input(command="desk-launch.py lane drive-watchdog-4 opus xhigh b.md", state=ACTIVE): Block(),
        Input(command="lr install roll --runner r.json --desk landing", state=ACTIVE, env={LANE_ENV: "tooling-runner-1"}): Allow(),
        Input(command="lr install check --config lr.json", state=ACTIVE): Allow(),
        Input(command="desk-runner.py run --config r.json --desk orca"): Allow(),
        Input(command="desk-launch.py lane signal sonnet low b.md", state=ACTIVE): Allow(),
        Input(command="desk-launch.py lane landing-desk-fix opus xhigh b.md", state=ACTIVE): Allow(),
        Input(command="desk-runner.py supervise status --config r.json --desk orca", state=ACTIVE): Allow(),
        Input(command="desk-runner.py show --config r.json", state=ACTIVE): Allow(),
        Input(command="echo desk-runner.py run --desk orca", state=ACTIVE): Allow(),
    }),
)
