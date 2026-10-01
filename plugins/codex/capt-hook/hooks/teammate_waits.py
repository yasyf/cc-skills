from __future__ import annotations

from pathlib import Path

from captain_hook import (
    Allow,
    And,
    BaseHookEvent,
    Block,
    CustomCondition,
    Event,
    FromSubagent,
    Input,
    Or,
    Tool,
    ToolInput,
    hook,
)

from . import common

POLL_UNIT_MS = 60_000
WAIT_ARGS = frozenset({"watch", "wait", "await", "--watch", "--wait", "--await"})
TEAMMATE = Path(__file__).parent / "tests" / "fixtures" / "teammate" / "sess.jsonl"
MATE = "amate-1a2b"


class InProcessTeammate(CustomCondition):
    def check(self, evt: BaseHookEvent) -> bool:
        return common.in_process_teammate(evt)


class LongPoll(CustomCondition):
    def check(self, evt: BaseHookEvent) -> bool:
        timeout = evt.input.raw.get("timeout")
        polls = any(call.name == "sleep" or WAIT_ARGS.intersection(call.args) for call in evt.command.calls())
        return polls and (timeout is None or int(timeout) > POLL_UNIT_MS)


POLL_IN_UNITS = (
    f"Wait in the FOREGROUND in units of at most {POLL_UNIT_MS // 1000} seconds: run the wait as foreground Bash with "
    f"timeout: {POLL_UNIT_MS} or less (or bound it with the command's own timeout flag) and run it again until it "
    "reports completion. Your parent's messages reach you only between tool calls, so one longer wait holds them "
    f"undelivered. Work that outlasts a {POLL_UNIT_MS // 1000}-second unit goes through a detach and await pair: "
    f"`{common.LAUNCHER} --dispatch --owner <agent-id>` then the codex-ask-channel await tool, or "
    "`retro.py prose <dir> --detach` then `retro.py prose <dir> --await`, rerun in units until it reports the exit."
)

hook(
    Event.PreToolUse,
    only_if=[
        Or(Tool("Monitor"), And(Tool("Bash"), ToolInput(run_in_background="true"))),
        FromSubagent(),
        InProcessTeammate(),
    ],
    message=(
        "You are an in-process teammate, and Claude Code never wakes an idle teammate for a Monitor event or a "
        "background Bash completion (anthropics/claude-code#77300): the notification is dropped and you idle until "
        f"someone messages you, while the finished work sits on disk. {POLL_IN_UNITS}"
    ),
    block=True,
    tests={
        Input(command="sleep 600", tool_input={"command": "sleep 600", "run_in_background": True}): Allow(),
        Input(
            command="make build",
            agent_id="a266d86820f8d3efd",
            tool_input={"command": "make build", "run_in_background": True},
        ): Allow(),
        Input(tool="Monitor", agent_id="ae4bfcfed12c8f26b", tool_input={"command": "tail -f log"}): Allow(),
        Input(command="make build", agent_id="aworker-1a2b3c4d5e6f7a8b"): Allow(),
        Input(
            tool="Monitor",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "cc-slack watch --session s1"},
        ): Block(pattern=r"units of at most 60 seconds"),
    },
)

hook(
    Event.PreToolUse,
    only_if=[Tool("Bash"), FromSubagent(), InProcessTeammate(), LongPoll()],
    message=(
        "This foreground wait can run past 60 seconds, and an in-process teammate receives its parent's messages "
        f"only between tool calls. {POLL_IN_UNITS}"
    ),
    block=True,
    tests={
        Input(
            command="cc-slack watch --session s1",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "cc-slack watch --session s1", "timeout": 590000},
        ): Block(pattern=r"timeout: 60000 or less"),
        Input(
            command="until gh pr checks 12; do sleep 30; done",
            agent_id=MATE,
            transcript=TEAMMATE,
            tool_input={"command": "until gh pr checks 12; do sleep 30; done"},
        ): Block(pattern="between tool calls"),
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
            command="cc-slack watch --session s1",
            agent_id="a266d86820f8d3efd",
            tool_input={"command": "cc-slack watch --session s1", "timeout": 590000},
        ): Allow(),
        Input(command="sleep 300", tool_input={"command": "sleep 300", "timeout": 600000}): Allow(),
    },
)
