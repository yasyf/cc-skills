from __future__ import annotations

import json
import subprocess
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Confirm, Event, HookResult, Input, Tool, on

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
SLACK = "mcp__plugin_cc-slack_cc-slack__"
POSTS = (*(SLACK + tool for tool in ("slack_dm_status", "slack_reply", "slack_send", "slack_edit")), "mcp__plugin_long-running_drive__say")
NO_DRIVE = "0a0a0a0a-no-drive-session"


def checked(evt: BaseHookEvent, script: str) -> HookResult | None:
    """Run the drive's own check for this call and let Jev judge each refusal it makes, so a check that matched a call its rule never meant lets the call through; a merge, release or public-body guard the check marks unjudged blocks outright."""
    payload = evt._raw
    said = subprocess.run(
        ["python3", "-I", str(HOOKS / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=payload.get("cwd") or None,
        check=True,
    ).stdout.strip()
    if not said:
        return None
    output = json.loads(said)
    found = output.get("hookSpecificOutput") or {}
    if reason := found.get("permissionDecisionReason") or (output.get("decision") == "block" and output["reason"]):
        return evt.block(reason, confirm=Confirm(rule=reason) if output.get("judged", True) else None)
    if made := found.get("updatedInput"):
        return evt.rewrite(made)
    if context := found.get("additionalContext"):
        return evt.context(context)
    return None


@on(
    Event.PreToolUse,
    only_if=[Tool("Read", "Write", "Edit", "Bash")],
    tests={Input(command="ls", session_id=NO_DRIVE): Allow()},
)
def no_file_tools(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "no_file_tools.py")


@on(
    Event.PreToolUse,
    only_if=[Tool(SLACK + "slack_thread", SLACK + "slack_history")],
    tests={Input(tool=SLACK + "slack_thread", tool_input={"channel": "D0", "ts": "1.0"}, session_id=NO_DRIVE): Allow()},
)
def no_slack_reads(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "no_slack_reads.py")


@on(Event.PreToolUse, only_if=[Tool("Bash")], tests={Input(command="ls", session_id=NO_DRIVE): Allow()})
def landing_guard(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "landing_guard.py")


@on(Event.PreToolUse, only_if=[Tool("Bash", "Read", "Grep")], tests={Input(command="ls", session_id=NO_DRIVE): Allow()})
def bash_guard(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "bash_guard.py")


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent")],
    tests={Input(tool="Agent", tool_input={"subagent_type": "long-running:handler", "prompt": "Record: 1", "model": "sonnet"}, session_id=NO_DRIVE): Allow()},
)
def agent_brief(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "agent_brief.py")


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent")],
    tests={Input(tool="Agent", tool_input={"subagent_type": "long-running:builder", "prompt": "Goal: x"}, session_id=NO_DRIVE): Allow()},
)
def scratch_folder(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "scratch_folder.py")


@on(
    Event.PreToolUse,
    only_if=[Tool(*POSTS)],
    tests={Input(tool=SLACK + "slack_dm_status", tool_input={"text": "On it. Then more."}, session_id=NO_DRIVE): Allow()},
)
def message_limits(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "message_limits.py")


@on(
    Event.PreToolUse,
    only_if=[Tool("mcp__plugin_cc-inbox_cci__post")],
    tests={Input(tool="mcp__plugin_cc-inbox_cci__post", tool_input={"kind": "report", "text": "x"}, session_id=NO_DRIVE): Allow()},
)
def record_lines(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "record_lines.py")


@on(Event.PreToolUse, tests={Input(tool="WebFetch", tool_input={"url": "https://example.com"}, session_id=NO_DRIVE): Allow()})
def communicator_calls(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "communicator_calls.py")


@on(Event.Stop, tests={Input(session_id=NO_DRIVE): Allow()})
def quiet_turn(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "quiet_turn.py")


@on(Event.SubagentStop, tests={Input(session_id=NO_DRIVE): Allow()})
def one_line_result(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "one_line_result.py")
