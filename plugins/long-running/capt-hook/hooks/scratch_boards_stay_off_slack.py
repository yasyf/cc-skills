from __future__ import annotations

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on

from .compaction_handoff import Bound

LANE = {"CLAUDE_LONG_RUNNING_LANE": "lane-a"}
STARTS_A_BOARD = "cc-present start --doc card.json"
SCRATCH = (
    "Warning, nothing was blocked: `cc-present start --doc` and `push` post a one-question card to his Slack DM by themselves."
    ' On a scratch board set `"mirror": false` on the card, or put the choice at the top level, and check with `cc-present push --dry-run`.'
)


class WritesABoard:
    def check(self, evt: BaseHookEvent) -> bool:
        return any(
            call.name == "cc-present" and "--dry-run" not in call.args and (call.args[:1] == ("push",) or call.args[:1] == ("start",) and "--doc" in call.args)
            for call in evt.command.calls()
        )


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash"), WritesABoard()],
    tests={
        Input(command=STARTS_A_BOARD, env=LANE): Warn(pattern=r"^Warning, nothing was blocked: `cc-present start --doc`"),
        Input(command="cc-present push card.json", env=LANE): Warn(),
        Input(command="cc-present push card.json --dry-run", env=LANE): Allow(),
        Input(command="cc-present start --title Notes", env=LANE): Allow(),
        Input(command="cc-present status", env=LANE): Allow(),
        Input(command=STARTS_A_BOARD): Allow(),
    },
)
def scratch_boards_stay_off_slack(evt: BaseHookEvent) -> HookResult:
    return evt.context(SCRATCH)
