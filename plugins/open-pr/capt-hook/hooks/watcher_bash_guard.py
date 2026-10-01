from __future__ import annotations

from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, CustomCondition, Event, Input, Tool, hook
from captain_hook.cmd import Call
from captain_hook.types import TOOL_EVENTS

POLL_SCRIPT = "pr-poll.sh"
SHELLS = frozenset({"sh", "bash", "zsh"})
WATCHER = "open-pr:pr-watcher"
POLL = "bash /p/open-pr/scripts/pr-poll.sh acme/widgets 7 /tmp/cache/pr/7.json"


def runs_poll_script(call: Call) -> bool:
    if "command" in call.wrappers:
        return False
    if call.name == POLL_SCRIPT:
        return True
    return call.name in SHELLS and bool(call.args) and Path(call.args[0]).name == POLL_SCRIPT


class InWatcher(CustomCondition):
    valid_events = TOOL_EVENTS

    def check(self, evt: BaseHookEvent) -> bool:
        return evt.parent_agent_type == WATCHER


class RunsPollScript(CustomCondition):
    valid_events = TOOL_EVENTS

    def check(self, evt: BaseHookEvent) -> bool:
        calls = tuple(evt.command.calls())
        syntax_check = any(call.name in SHELLS and "-n" in call.args for call in calls)
        return not syntax_check and any(runs_poll_script(call) for call in calls)


hook(
    Event.PreToolUse,
    only_if=[Tool("Bash"), InWatcher(), RunsPollScript()],
    message="`pr-poll.sh` runs only under Monitor on the `poll:` command. Handle the armed Monitor's next line instead of polling from Bash.",
    block=True,
    tests={
        Input(command=POLL, agent_type=WATCHER): Block(pattern="only under Monitor"),
        Input(command=f"while true; do {POLL}; sleep 30; done", agent_type=WATCHER): Block(),
        Input(command=f"PR_POLL_STACK=2 {POLL}", agent_type=WATCHER): Block(),
        Input(command=f"timeout 600 {POLL}", agent_type=WATCHER): Block(),
        Input(command=f"cd /tmp && {POLL}", agent_type=WATCHER): Block(),
        Input(
            command="'/p/open-pr/scripts/pr-poll.sh' acme/widgets 7 /tmp/cache/pr/7.json", agent_type=WATCHER
        ): Block(),
        Input(command="gh pr view 7 --json headRefOid", agent_type=WATCHER): Allow(),
        Input(command="sed -n 1,40p /p/open-pr/scripts/pr-poll.sh", agent_type=WATCHER): Allow(),
        Input(command="bash -n /p/open-pr/scripts/pr-poll.sh", agent_type=WATCHER): Allow(),
        Input(command="command -v /p/open-pr/scripts/pr-poll.sh", agent_type=WATCHER): Allow(),
        Input(command="bash -n -c 'bash /p/open-pr/scripts/pr-poll.sh x'", agent_type=WATCHER): Allow(),
        Input(command="grep -n DONE /p/open-pr/scripts/pr-poll.sh", agent_type=WATCHER): Allow(),
        Input(command=POLL): Allow(),
        Input(command=POLL, agent_type="general-purpose"): Allow(),
    },
)
