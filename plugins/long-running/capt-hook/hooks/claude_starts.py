from __future__ import annotations

from captain_hook import Allow, Annotated, Input, Occurrence, PreToolUseEvent, Rewrite, rewrite_command_occurrences

from .compaction_handoff import Bound, CompactionState

BOUND = [CompactionState(active=True, coordinator=True)]
NOT_SESSIONS = frozenset({"plugin", "mcp", "config", "update", "doctor", "install", "setup-token", "migrate-installer", "agents", "auth", "--version", "-v", "--help", "-h"})


def through_lr(evt: PreToolUseEvent, occ: Occurrence) -> str | None:
    command = occ.command
    if command.executable != "claude" or command.args and command.args[0] in NOT_SESSIONS:
        return None
    return "lr launch " + command.raw


rewrite_command_occurrences(
    to=through_lr,
    only_if=[Bound()],
    skip_if=[Annotated("raw")],
    note="A Claude started from a drive runs through `lr launch claude`, which sets the 300k compaction limit, opus unless --model names another, and effort high; a Fable or astra model or an effort above high takes `--owner-pick owner:m<row>` naming his words.",
    tests={
        Input(command="claude -p 'summarize the log'", state=BOUND): Rewrite(pattern=r"lr launch claude -p 'summarize the log'"),
        Input(command="cd /w && claude --model sonnet --effort low -p hi | tail -1", state=BOUND): Rewrite(pattern="cd /w && lr launch claude --model sonnet --effort low -p hi"),
        Input(command="claude --resume abc", state=BOUND): Rewrite(pattern=r"lr launch claude --resume abc"),
        Input(command="claude plugin update long-running@skills", state=BOUND): Allow(),
        Input(command="claude mcp list", state=BOUND): Allow(),
        Input(command="claude --version", state=BOUND): Allow(),
        Input(command="lr launch claude -p hi", state=BOUND): Allow(),
        Input(command="claude -p hi"): Allow(),
        Input(command="claude -p hi  # ccx:raw", state=BOUND): Allow(),
    },
)
