from __future__ import annotations

import re

from cc_transcript.models import AssistantEvent
from captain_hook import (
    Allow,
    BaseHookEvent,
    Block,
    Confirm,
    Event,
    FromSubagent,
    HookResult,
    Input,
    TaskCall,
    Tool,
    Warn,
    on,
)

from .compaction_handoff import CompactionState
from .nudges import queue_nudge
from .tests.handoff_fixtures import reply_transcript

ACTIVE = [CompactionState(active=True)]
INCIDENT_NAME = re.compile(r"^(?:incident|outage)-")
INCIDENT_BRIEF = re.compile(r"\b(?:incident|outage)\b", re.IGNORECASE)
FIX_ROLE = "fix"
INCIDENT_ROUTE = (
    "Route the incident fix lane through the orca-desk. Append `orca-desk: launch <name> NOW` to its inbox; an Agent "
    "spawn on an incident is a support lane and says so in a `ccx: role=<role>` line of its brief."
)
INCIDENT_FIX = Confirm(rule="an incident FIX lane must run on Orca sol; tooling, support, and repush lanes may spawn here")
REPLY_WINDOW = 256
UTC_CLOCK = re.compile(r"\b\d{1,2}:\d[\dx](?::\d\d)?\s?(?:Z|UTC)\b")
UTC_IN_REPLY = "Owner-facing times are Pacific with no zone label. Restate the UTC times from your last reply in Pacific."


def prose(call: TaskCall) -> list[str]:
    return [line for line in (call.raw.get("prompt") or "").splitlines() if not line.strip().startswith("ccx:")]


def incident_spawn(call: TaskCall, lines: list[str]) -> bool:
    return bool(INCIDENT_NAME.match(call.agent_name or "") or any(INCIDENT_BRIEF.search(line) for line in lines))


def spawn(name: str, prompt: str, **extra: object) -> Input:
    return Input(tool="Agent", tool_input={"name": name, "prompt": prompt}, state=ACTIVE, **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent")],
    skip_if=[FromSubagent()],
    tests={
        spawn("pr-review-pipeline-fix", "CI incident, effort high."): Block(pattern=r"orca-desk: launch <name> NOW"),
        spawn("incident-api-1n80-fix", "Fix it."): Block(),
        spawn("api-1n80-fix", "ccx: role=fix\nFix the outage on api.", llm={"block": False}): Block(),
        spawn("incident-runner", "Lane incident-runner: a durable incident executor.", llm={"block": False}): Warn(
            pattern=r"allowed, the model found the call outside the rule"
        ),
        spawn("incident-runner", "ccx: role=tooling tooling-lane=incident-runner\nLane incident-runner."): Allow(),
        spawn("incident-api-1n80-evidence", "ccx: role=evidence\nEvidence lane for the incident."): Allow(),
        spawn("ledger-fix", "ccx: role=fix\nFix the ledger."): Allow(),
        spawn("slack-inline-why", "Owner correction.\nThe incident lane..."): Allow(),
        Input(tool="Agent", tool_input={"name": "incident-api-1n80-fix", "prompt": "Fix it."}): Allow(),
        spawn("incident-api-1n80-fix", "Fix it.", agent_id="a1b2c3"): Allow(),
    },
)
def route_incident_fix_lanes_to_sol(evt: BaseHookEvent) -> HookResult | None:
    call = evt.as_input(TaskCall)
    if call is None or not CompactionState.load(evt).active:
        return None
    lines = prose(call)
    if "role" not in evt.annotations:
        return evt.block(INCIDENT_ROUTE, confirm=INCIDENT_FIX) if incident_spawn(call, lines[:1]) else None
    if evt.annotations["role"] == FIX_ROLE and incident_spawn(call, lines):
        return evt.block(INCIDENT_ROUTE)
    return None


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
