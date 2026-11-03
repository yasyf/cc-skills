from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Block, Event, Input, Or, Runs, Tool, hook

from .window_lane import RELAY_LANE, bound, window_lane

STANDING = re.compile(r"(?:landing-desk|fidelity-desk|red-sweep|alerts-desk|drive-watchdog|board-refresher)(?:-\d+)?")


class CommunicatorWindow:
    """A window bound `owner-relay`, its subagents included; a window bound to no lane never split, so its one session still owns the desks."""

    def check(self, evt: BaseHookEvent) -> bool:
        return window_lane(evt) == RELAY_LANE


class LaunchesStandingDesk:
    def check(self, evt: BaseHookEvent) -> bool:
        return any(call.args[:1] == ("lane",) and STANDING.fullmatch("".join(call.args[1:2])) for call in evt.command.calls("desk-launch.py"))


hook(  # capt-hook: no-escape the coordinator is the one owner of every runner and desk, and the message names the record that reaches it
    Event.PreToolUse,
    block=True,
    only_if=[
        Tool("Bash"),
        CommunicatorWindow(),
        Or(
            Runs("desk-runner.py", "run"),
            Runs("desk-runner.py", "handoff"),
            Runs("desk-runner.py", "supervise", "install"),
            Runs("desk-creds.py", "install"),
            Runs("desk-creds.py", "roll"),
            LaunchesStandingDesk(),
        ),
    ],
    message="The coordinator owns desk-runner, the launch runner and every desk, so the communicator starts none of them. Post it to the coordinator: `cci post --lane owner-relay --kind ask --to root --text 'start <desk>'`.",
    tests={
        bound(Input(command="desk-runner.py run --config r.json --desk orca"), RELAY_LANE): Block(pattern=r"^The coordinator owns desk-runner, .*--to root"),
        bound(Input(command="nohup desk-runner.py run --config r.json --desk landing >> l.log 2>&1 < /dev/null &"), RELAY_LANE): Block(),
        bound(Input(command="desk-runner.py handoff --config r.json --desk orca"), RELAY_LANE): Block(),
        bound(Input(command="desk-runner.py supervise install --config r.json --desk landing"), RELAY_LANE): Block(),
        bound(Input(command="desk-creds.py install --config r.json --desk landing"), RELAY_LANE): Block(),
        bound(Input(command="desk-creds.py roll --config r.json --desk landing"), RELAY_LANE): Block(),
        bound(Input(command="desk-launch.py lane landing-desk opus xhigh b.md"), RELAY_LANE): Block(),
        bound(Input(command="desk-launch.py lane drive-watchdog-4 opus xhigh b.md"), RELAY_LANE): Block(),
        bound(Input(command="desk-runner.py run --config r.json --desk orca", agent_id="a1b2c3"), RELAY_LANE): Block(),
        Input(command="desk-creds.py install --config r.json --desk landing", env={"CLAUDE_LONG_RUNNING_LANE": RELAY_LANE}): Block(),
        bound(Input(command="desk-runner.py run --config r.json --desk orca"), "root"): Allow(),
        bound(Input(command="desk-creds.py install --config r.json --desk landing"), "root"): Allow(),
        bound(Input(command="desk-launch.py lane landing-desk opus xhigh b.md"), "root"): Allow(),
        Input(command="desk-runner.py run --config r.json --desk orca"): Allow(),
        bound(Input(command="desk-launch.py lane signal sonnet low b.md"), RELAY_LANE): Allow(),
        bound(Input(command="desk-launch.py lane landing-desk-fix opus xhigh b.md"), RELAY_LANE): Allow(),
        bound(Input(command="desk-runner.py supervise status --config r.json --desk orca"), RELAY_LANE): Allow(),
        bound(Input(command="desk-runner.py show --config r.json"), RELAY_LANE): Allow(),
        bound(Input(command="echo desk-runner.py run --desk orca"), RELAY_LANE): Allow(),
    },
)
