from __future__ import annotations

from captain_hook import (
    Allow,
    And,
    BaseHookEvent,
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


class InProcessTeammate(CustomCondition):
    def check(self, evt: BaseHookEvent) -> bool:
        return common.in_process_teammate(evt)


hook(
    Event.PreToolUse,
    only_if=[
        Or(Tool("Monitor"), And(Tool("Bash"), ToolInput(run_in_background="true"))),
        FromSubagent(),
        InProcessTeammate(),
    ],
    message=(
        "You are an in-process teammate, and Claude Code never wakes an idle teammate for a Monitor "
        "event or a background Bash completion (anthropics/claude-code#77300): the notification is "
        "dropped and you idle until someone messages you, while the finished work sits on disk. Wait "
        "in the FOREGROUND: run the command as foreground Bash with timeout: 600000. Work that "
        f"outlasts 10 minutes goes through a detach and await pair: `{common.LAUNCHER} --dispatch "
        "--owner <agent-id>` then the codex-ask-channel await tool, or `retro.py prose <dir> "
        "--detach` then `retro.py prose <dir> --await`, rerun in the foreground until it reports "
        "the exit."
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
    },
)
