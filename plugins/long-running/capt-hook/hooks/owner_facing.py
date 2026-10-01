from __future__ import annotations

import re

from cc_transcript.models import AssistantEvent
from captain_hook import Allow, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, TaskCall, Tool, on

from .compaction_handoff import CompactionState
from .nudges import queue_nudge
from .tests.handoff_fixtures import reply_transcript

ACTIVE = [CompactionState(active=True)]
INCIDENT_NAME = re.compile(r"^(?:incident|outage)-")
INCIDENT_BRIEF = re.compile(r"\b(?:incident|outage)\b", re.IGNORECASE)
SUPPORT_ROLES = ("evidence", "export", "ship", "comms", "intake", "retro", "watch", "handoff", "backup")
INCIDENT_ROUTE = (
    "Route the incident fix lane through the orca-desk. Append `orca-desk: launch <name> NOW` to its inbox; Agent "
    "spawns on an incident are only -evidence, -export, -ship, -comms, -intake, -retro, -watch, -handoff, or -backup lanes."
)
REPLY_WINDOW = 256
UTC_CLOCK = re.compile(r"\b\d{1,2}:\d[\dx](?::\d\d)?\s?(?:Z|UTC)\b")
UTC_IN_REPLY = "Owner-facing times are Pacific with no zone label. Restate the UTC times from your last reply in Pacific."


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


def last_reply(evt: BaseHookEvent) -> str:
    replies = (
        event.text
        for event in reversed(evt.ctx.t.events)
        if isinstance(event, AssistantEvent) and not event.meta.is_sidechain and event.text
    )
    return next(replies, "")


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests={
        Input(transcript=reply_transcript("Fix lane spawned at 17:35Z."), state=ACTIVE): Allow(),
        Input(transcript=reply_transcript("Fix lane spawned at 10:35am."), state=ACTIVE): Allow(),
        Input(transcript=reply_transcript("Lane at 17:35Z.", sidechain=True), state=ACTIVE): Allow(),
        Input(transcript=reply_transcript("Fix lane spawned at 17:35Z.")): Allow(),
        Input(transcript=reply_transcript("Fix lane spawned at 17:35Z."), agent_id="a1b2c3", state=ACTIVE): Allow(),
    },
)
def nudge_utc_in_owner_replies(evt: BaseHookEvent) -> HookResult | None:
    if CompactionState.load(evt).active and UTC_CLOCK.search(last_reply(evt)):
        queue_nudge(evt, UTC_IN_REPLY)
    return None
