from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Call, Event, Input, Tool, Warn, hook

from .compaction_handoff import Bound, CompactionState
from .lane_landing import lane_name

ACTIVE = [CompactionState(active=True, coordinator=True)]
INSTALLER = re.compile(r"install-desk(?:-\d+)?")
PLUGIN_WRITES = (("plugin", "update"), ("plugin", "install"))
LOCKERS = frozenset({"lockf", "flock"})


def claude_args(call: Call) -> tuple[str, ...]:
    if call.name == "claude":
        return tuple(call.args)
    if call.name in LOCKERS and "claude" in call.args:
        return tuple(call.args[call.args.index("claude") + 1 :])
    return ()


class LaneInstallsAPack:
    def check(self, evt: BaseHookEvent) -> bool:
        lane = lane_name(evt)
        if not lane or INSTALLER.fullmatch(lane):
            return False
        plugin_write = any(claude_args(call)[:2] in PLUGIN_WRITES for call in evt.command.calls())
        lr_install = any(call.args[:1] == ("install",) and call.args[1:2] != ("check",) for call in evt.command.calls("lr"))
        return plugin_write or lr_install


hook(
    Event.PreToolUse,
    only_if=[Tool("Bash"), Bound(), LaneInstallsAPack()],
    message="Only install-desk installs a pack on the Mac, behind its green-CI gate. Post landed to install-desk instead: `cci post --kind landed --to install-desk --pr <repo>#<n>`.",
    tests={
        Input(command="claude plugin update long-running@skills", agent_id="aworking-line-all-0123456789abcdef", state=ACTIVE): Warn(pattern=r"Post landed to install-desk instead"),
        Input(command="claude plugin update cc-slack@cc-slack 2>&1 | tail -5", env={"CLAUDE_LONG_RUNNING_LANE": "meta-desk-3"}): Warn(),
        Input(command="claude plugin install codex@skills", env={"CLAUDE_LONG_RUNNING_LANE": "lane-a"}): Warn(),
        Input(command='/usr/bin/lockf -k "$HOME/.claude/plugins/.update.lock" claude plugin update long-running@skills', env={"CLAUDE_LONG_RUNNING_LANE": "lane-a"}): Warn(),
        Input(command="/plugin/bin/lr install 0.7.1782", env={"CLAUDE_LONG_RUNNING_LANE": "lane-a"}): Warn(),
        Input(command="claude plugin update long-running@skills", env={"CLAUDE_LONG_RUNNING_LANE": "install-desk-2"}): Allow(),
        Input(command="lr install 0.7.1782", env={"CLAUDE_LONG_RUNNING_LANE": "install-desk"}): Allow(),
        Input(command="lr install check --config lr.json", env={"CLAUDE_LONG_RUNNING_LANE": "lane-a"}): Allow(),
        Input(command="claude plugin list", env={"CLAUDE_LONG_RUNNING_LANE": "lane-a"}): Allow(),
        Input(command="echo claude plugin update long-running@skills", env={"CLAUDE_LONG_RUNNING_LANE": "lane-a"}): Allow(),
        Input(command="claude plugin update long-running@skills"): Allow(),
    },
)
