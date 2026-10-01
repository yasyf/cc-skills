from __future__ import annotations

import re
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, TaskCall, Tool, on

from .compaction_handoff import CompactionState
from .nudges import queue_nudge
from .turns import reversed_entries

ACTIVE = [CompactionState(active=True)]
INCIDENT_NAME = re.compile(r"^(?:incident|outage)-")
INCIDENT_BRIEF = re.compile(r"\b(?:incident|outage)\b", re.IGNORECASE)
SUPPORT_ROLES = ("evidence", "export", "ship", "comms", "intake", "retro", "watch", "handoff", "backup")
INCIDENT_ROUTE = (
    "route the incident fix lane through the orca-desk (R16): append `orca-desk: launch <name> NOW` to its inbox for "
    "a codex `gpt-6.1-sol` worker, fast tier, `xhigh`, whatever the surface (CI, Slack report, Sentry, Datadog). "
    "An Agent spawn on an incident is an -evidence, -export, -ship, -comms, -intake, -retro, -watch, or -handoff "
    "lane, or the Opus 5.5 `-backup` lane after sol misses."
)
UTC_CLOCK = re.compile(r"\b\d{1,2}:\d[\dx](?::\d\d)?\s?(?:Z|UTC)\b")
UTC_IN_REPLY = "owner-facing times are Pacific with no zone label (R21): restate {times} from your last reply in Pacific"


def incident_spawn(call: TaskCall) -> bool:
    name, brief = call.agent_name or "", call.raw.get("prompt") or ""
    if any(role in name for role in SUPPORT_ROLES):
        return False
    return bool(INCIDENT_NAME.match(name) or INCIDENT_BRIEF.search(brief.split("\n", 1)[0]))


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent")],
    skip_if=[FromSubagent()],
    tests={
        Input(
            tool="Agent", tool_input={"name": "pr-review-pipeline-fix", "prompt": "CI incident, effort high."}, state=ACTIVE
        ): Block(pattern=r"orca-desk: launch <name> NOW"),
        Input(tool="Agent", tool_input={"name": "incident-api-1n80-fix", "prompt": "Fix it."}, state=ACTIVE): Block(),
        Input(tool="Agent", tool_input={"name": "incident-api-1n80-evidence", "prompt": "Evidence lane."}, state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input={"name": "incident-api-1n80-backup", "prompt": "Opus 5.5 lane."}, state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input={"name": "slack-inline-why", "prompt": "Owner correction.\nThe incident lane..."}, state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input={"name": "incident-api-1n80-fix", "prompt": "Fix it."}): Allow(),
        Input(
            tool="Agent", tool_input={"name": "incident-api-1n80-fix", "prompt": "Fix it."}, agent_id="a1b2c3", state=ACTIVE
        ): Allow(),
    },
)
def route_incident_fix_lanes_to_sol(evt: BaseHookEvent) -> HookResult | None:
    call = evt.as_input(TaskCall)
    if call is None or not CompactionState.load(evt).active or not incident_spawn(call):
        return None
    return evt.block(INCIDENT_ROUTE)


def last_reply(transcript: Path) -> str:
    for entry in reversed_entries(transcript):
        if entry.get("isSidechain") or entry.get("type") != "assistant":
            continue
        content = entry["message"].get("content")
        texts = [block["text"] for block in content if block.get("type") == "text"] if isinstance(content, list) else []
        if texts:
            return "\n".join(texts)
    return ""


@on(Event.Stop, skip_if=[FromSubagent()])
def nudge_utc_in_owner_replies(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active or not evt.transcript_path.is_file():
        return None
    if times := sorted(set(UTC_CLOCK.findall(last_reply(evt.transcript_path)))):
        queue_nudge(evt, UTC_IN_REPLY.format(times=", ".join(times)))
    return None
