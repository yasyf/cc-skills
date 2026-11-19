from __future__ import annotations

from captain_hook import (
    Allow,
    And,
    BaseHookEvent,
    Call,
    CustomCondition,
    Event,
    FromSubagent,
    Input,
    Or,
    Tool,
    ToolInput,
    Warn,
    hook,
)

from . import common
from .tests.teammate_fixtures import MATE, TEAMMATE

POLL_UNIT_MS = 60_000
WAIT_ARGS = frozenset({"watch", "wait", "await", "--watch", "--wait", "--await"})
MAIL_WAIT = "desk-wait.sh"


def wakes_on_mail(call: Call) -> bool:
    waits = call.name == MAIL_WAIT or (call.name == "lr" and call.args[:1] == ("wait",))
    sources = call.args[: call.args.index("--")] if "--" in call.args else call.args
    return waits and any("=" in source or source.startswith("cci:") for source in sources[1:])


class InProcessTeammate(CustomCondition):
    def check(self, evt: BaseHookEvent) -> bool:
        return common.in_process_teammate(evt)


class PrWait(CustomCondition):
    def check(self, evt: BaseHookEvent) -> bool:
        calls = list(evt.command.calls())
        reads = [call for call in calls if call.name == "gh" and call.args[:2] in {("pr", "checks"), ("pr", "view")}]
        return any("--watch" in call.args for call in reads) or bool(reads and any(call.name == "sleep" for call in calls))


class LongPoll(CustomCondition):
    def check(self, evt: BaseHookEvent) -> bool:
        timeout = evt.input.raw.get("timeout")
        polls = any(
            call.name == "sleep" or WAIT_ARGS.intersection(call.args)
            for call in evt.command.calls()
            if not wakes_on_mail(call)
        )
        return polls and (timeout is None or int(timeout) > POLL_UNIT_MS)


hook(
    Event.PreToolUse,
    only_if=[
        Or(Tool("Monitor"), And(Tool("Bash"), ToolInput(run_in_background="true"))),
        FromSubagent(),
        InProcessTeammate(),
    ],
    skip_if=[PrWait()],
    message=(
        "An in-process teammate is never woken by a Monitor event or a background Bash completion. Run the wait as "
        f"foreground Bash with `timeout: {POLL_UNIT_MS}` and rerun it until it reports completion."
    ),
    max_fires=1,
    tests={
        Input(command="sleep 600", tool_input={"command": "sleep 600", "run_in_background": True}): Allow(),
        Input(
            command="make build",
            agent_id="a266d86820f8d3efd",
            tool_input={"command": "make build", "run_in_background": True},
        ): Allow(),
        Input(tool="Monitor", agent_id="ae4bfcfed12c8f26b", tool_input={"command": "tail -f log"}): Allow(),
        Input(
            command="gh pr checks 12 --watch",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "gh pr checks 12 --watch", "run_in_background": True},
        ): Allow(),
        Input(command="make build", agent_id="aworker-1a2b3c4d5e6f7a8b"): Allow(),
        Input(
            tool="Monitor",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "cc-slack watch --session s1"},
        ): Warn(pattern=r"never woken"),
    },
)

hook(
    Event.PreToolUse,
    only_if=[Tool("Bash"), FromSubagent(), InProcessTeammate(), LongPoll()],
    skip_if=[PrWait()],
    message=(
        "A foreground wait past 60 seconds holds your parent's messages undelivered. Rerun it as foreground Bash with "
        f"`timeout: {POLL_UNIT_MS}` or less until it reports completion."
    ),
    max_fires=1,
    tests={
        Input(
            command="cc-slack watch --session s1",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "cc-slack watch --session s1", "timeout": 590000},
        ): Warn(pattern=r"timeout: 60000"),
        Input(
            command="until gh pr checks 12; do sleep 30; done",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "until gh pr checks 12; do sleep 30; done"},
        ): Allow(),
        Input(
            command="cc-slack watch --session s1",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "cc-slack watch --session s1", "timeout": 60000},
        ): Allow(),
        Input(
            command="make build",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "make build", "timeout": 600000},
        ): Allow(),
        Input(
            command="desk-wait.sh 540 /h/.claude/teams/s/inboxes/mate.json=/h/mate.cursor -- bk build watch 4292",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={
                "command": "desk-wait.sh 540 /h/.claude/teams/s/inboxes/mate.json=/h/mate.cursor -- bk build watch 4292",
                "timeout": 570000,
            },
        ): Allow(),
        Input(
            command="cd /w && desk-wait.sh 560 cci:drive:mate -- stack-enqueue 33185 --watch --fast",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "cd /w && desk-wait.sh 560 cci:drive:mate -- stack-enqueue 33185 --watch --fast", "timeout": 570000},
        ): Allow(),
        Input(
            command="lr wait 540 mailbox:mate=/h/mate.cursor -- bk build watch 4292",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "lr wait 540 mailbox:mate=/h/mate.cursor -- bk build watch 4292", "timeout": 570000},
        ): Allow(),
        Input(
            command="/h/.claude/long-running/current/bin/lr wait 540 cci:drive:mate",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "/h/.claude/long-running/current/bin/lr wait 540 cci:drive:mate", "timeout": 570000},
        ): Allow(),
        Input(
            command="lr wait 540 -- bk build watch 4292",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "lr wait 540 -- bk build watch 4292", "timeout": 570000},
        ): Warn(pattern="parent's messages"),
        Input(
            command="desk-wait.sh 540 -- bk build watch 4292",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "desk-wait.sh 540 -- bk build watch 4292", "timeout": 570000},
        ): Warn(pattern="parent's messages"),
        Input(
            command="desk-wait.sh 540 /h/mate.json=/h/mate.cursor -- true; sleep 600",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "desk-wait.sh 540 /h/mate.json=/h/mate.cursor -- true; sleep 600", "timeout": 600000},
        ): Warn(pattern="parent's messages"),
        Input(
            command="cc-slack watch --session s1",
            agent_id="a266d86820f8d3efd",
            tool_input={"command": "cc-slack watch --session s1", "timeout": 590000},
        ): Allow(),
        Input(command="sleep 300", tool_input={"command": "sleep 300", "timeout": 600000}): Allow(),
    },
)

hook(
    Event.PreToolUse,
    only_if=[Tool("Bash"), FromSubagent(), InProcessTeammate(), PrWait()],
    message=(
        "A teammate never waits on a PR itself. Hand the watch to `open-pr:pr-watcher` with `reply_to:` set to your"
        " own name, as open-pr's teammate handoff says, and end your turn: its `SendMessage` wakes you."
    ),
    max_fires=1,
    tests={
        Input(
            command="until gh pr checks 12; do sleep 30; done",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "until gh pr checks 12; do sleep 30; done"},
        ): Warn(pattern="reply_to"),
        Input(
            command="timeout 55 gh pr checks 12 --watch --interval 20",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "timeout 55 gh pr checks 12 --watch --interval 20", "timeout": 60000},
        ): Warn(pattern="pr-watcher"),
        Input(
            command="gh pr checks 12",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "gh pr checks 12"},
        ): Allow(),
        Input(
            command="gh pr checks 12 --watch",
            agent_id="a266d86820f8d3efd",
            tool_input={"command": "gh pr checks 12 --watch", "timeout": 60000},
        ): Allow(),
    },
)
