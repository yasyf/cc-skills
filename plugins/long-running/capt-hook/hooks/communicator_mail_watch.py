from __future__ import annotations

from captain_hook import Allow, BaseHookEvent, Event, Input, Or, Runs, Tool, Warn, hook
from captain_hook.cmd import Cmd

from .compaction_handoff import Communicator, communicator_tests
from .owner_facing import CCI_READ
from .urgent_lines_name_the_harm import ACTIVE

MAIL_WATCH = "cci watch --drive d --reader owner-relay --lane root --lane meta-desk --lane signal --cursor owner-relay-mail"
LANES_ONLY = "cci watch --drive d --lane owner-relay --lane root --lane meta-desk --since 513300"


class WatchesWithoutReader:
    def check(self, evt: BaseHookEvent) -> bool:
        command = evt.command if evt.tool_name == "Bash" else Cmd.parse(str(evt.input.raw.get("command") or ""))
        return command is not None and any(
            (values := CCI_READ.bind(call).values).get("words", ())[:1] == ("watch",) and not values.get("reader") and not values.get("to")
            for call in command.calls("cci")
        )


def monitor(command: str) -> Input:
    return Input(tool="Monitor", tool_input={"description": "owner-relay mail", "timeout_ms": 1800000, "command": command}, state=ACTIVE)


hook(
    Event.PreToolUse,
    message="Warning, nothing was blocked: `cci watch` with no `--reader` keeps only what the `--lane` lanes posted, so a record another lane addresses to you never arrives. Add `--reader owner-relay` and keep each `--lane` for its broadcasts.",
    only_if=[Communicator(), Or(Tool("Monitor"), Runs("cci", "watch")), WatchesWithoutReader()],
    tests=communicator_tests({
        monitor(LANES_ONLY): Warn(pattern=r"^Warning, nothing was blocked: `cci watch` with no `--reader`[\s\S]*`--reader owner-relay`"),
        Input(command=LANES_ONLY, state=ACTIVE): Warn(pattern=r"`--reader owner-relay`"),
        monitor(MAIL_WATCH): Allow(),
        Input(command=MAIL_WATCH, state=ACTIVE): Allow(),
        monitor("cci watch --drive d --to owner-relay --cursor owner-relay-mail"): Allow(),
        monitor("lr owner watch"): Allow(),
        Input(command="cci tail --drive d --lane root --since 513300", state=ACTIVE): Allow(),
        Input(command=LANES_ONLY, state=ACTIVE, agent_id="a1b2c3"): Allow(),
    }),
)
