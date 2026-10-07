from __future__ import annotations

from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, CustomCondition, Event, Input, Tool, hook
from captain_hook.bindings import Known
from captain_hook.cmd import Call, env_bindings
from captain_hook.types import TOOL_EVENTS

POLL_SCRIPT = "pr-poll.sh"
SHELLS = frozenset({"sh", "bash", "zsh"})
WATCHER = "open-pr:pr-watcher"
POLL = "bash /p/open-pr/scripts/pr-poll.sh acme/widgets 7 /tmp/cache/pr/7.json"
SHORT_POLL = f"PR_POLL_WINDOW=40 {POLL}"
SHORT_WINDOW = 40
FOREGROUND_TIMEOUT_MS = 60_000


def runs_poll_script(call: Call) -> bool:
    if "command" in call.wrappers:
        return False
    if call.name == POLL_SCRIPT:
        return True
    return call.name in SHELLS and bool(call.args) and Path(call.args[0]).name == POLL_SCRIPT


def short_window(call: Call) -> bool:
    match env_bindings(call).get("PR_POLL_WINDOW"):
        case Known(candidates=values):
            return all(value.isdigit() and 0 < int(value) <= SHORT_WINDOW for value in values)
        case _:
            return False


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


class ShortForegroundPoll(CustomCondition):
    valid_events = TOOL_EVENTS

    def check(self, evt: BaseHookEvent) -> bool:
        raw = evt.input.raw
        timeout = raw.get("timeout")
        if raw.get("run_in_background") or timeout is None or int(timeout) > FOREGROUND_TIMEOUT_MS:
            return False
        calls = tuple(evt.command.calls())
        return len(calls) == 1 and runs_poll_script(calls[0]) and short_window(calls[0])


hook(
    Event.PreToolUse,
    only_if=[Tool("Bash"), InWatcher(), RunsPollScript()],
    skip_if=[ShortForegroundPoll()],
    message=(
        "`pr-poll.sh` runs under Monitor on the `poll:` command, or alone as foreground Bash when Monitor is refused. "
        "Run `PR_POLL_WINDOW=40 <poll:>` with `timeout: 60000` and rerun it on `DONE window-elapsed`."
    ),
    block=True,
    tests={
        Input(command=POLL, agent_type=WATCHER): Block(pattern="PR_POLL_WINDOW=40"),
        Input(command=f"while true; do {POLL}; sleep 30; done", agent_type=WATCHER): Block(),
        Input(command=f"PR_POLL_STACK=2 {POLL}", agent_type=WATCHER): Block(),
        Input(command=f"timeout 600 {POLL}", agent_type=WATCHER): Block(),
        Input(command=f"cd /tmp && {POLL}", agent_type=WATCHER): Block(),
        Input(
            command="'/p/open-pr/scripts/pr-poll.sh' acme/widgets 7 /tmp/cache/pr/7.json", agent_type=WATCHER
        ): Block(),
        Input(
            command=POLL, agent_type=WATCHER, tool_input={"command": POLL, "timeout": FOREGROUND_TIMEOUT_MS}
        ): Block(),
        Input(command=SHORT_POLL, agent_type=WATCHER): Block(),
        Input(
            command=SHORT_POLL, agent_type=WATCHER, tool_input={"command": SHORT_POLL, "timeout": 600_000}
        ): Block(),
        Input(
            command=SHORT_POLL,
            agent_type=WATCHER,
            tool_input={"command": SHORT_POLL, "timeout": FOREGROUND_TIMEOUT_MS, "run_in_background": True},
        ): Block(),
        Input(
            command=f"PR_POLL_WINDOW=1500 {POLL}",
            agent_type=WATCHER,
            tool_input={"command": f"PR_POLL_WINDOW=1500 {POLL}", "timeout": FOREGROUND_TIMEOUT_MS},
        ): Block(),
        Input(
            command=f"while true; do {SHORT_POLL}; done",
            agent_type=WATCHER,
            tool_input={"command": f"while true; do {SHORT_POLL}; done", "timeout": FOREGROUND_TIMEOUT_MS},
        ): Block(),
        Input(
            command=SHORT_POLL, agent_type=WATCHER, tool_input={"command": SHORT_POLL, "timeout": FOREGROUND_TIMEOUT_MS}
        ): Allow(),
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
