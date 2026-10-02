from __future__ import annotations

import re

from cc_transcript.models import AssistantEvent
from captain_hook import (
    Allow,
    Annotated,
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
from .tests.brief_fixtures import INCIDENT_ADJACENT, INCIDENT_TURN, NOT_AN_INCIDENT
from .tests.handoff_fixtures import reply_transcript

ACTIVE = [CompactionState(active=True)]
INCIDENT_NAME = re.compile(r"^(?:incident|outage)-")
INCIDENT_BRIEF = re.compile(r"\b(?:incident|outage)\b", re.IGNORECASE)
INCIDENT_AUTHORITY = re.compile(r"\bIncident Turn\b")
FIX_ROLE = "fix"
INCIDENT_ROUTE = (
    "Route the incident fix lane through the orca-desk. Append `orca-desk: launch <name> NOW` to its inbox; an Agent "
    "spawn on an incident is a support lane and says so in a `ccx: role=<role>` line of its brief, and a fix lane "
    "that is not an incident says `ccx: incident=none`."
)
INCIDENT_FIX = Confirm(rule="an incident FIX lane must run on Orca sol; tooling, support, and repush lanes may spawn here")
REPLY_WINDOW = 256
UTC_CLOCK = re.compile(r"\b\d{1,2}:\d[\dx](?::\d\d)?\s?(?:Z|UTC)\b")
UTC_IN_REPLY = "Owner-facing times are Pacific with no zone label. Restate the UTC times from your last reply in Pacific."
CODENAMES = re.compile(
    r"\b[GRL]\d{3,4}\b|\b(?:answer|grant|ruling|note) [0-9a-f]{7}\b|\bctx_\w+|\b(?:merge-)?walker\b|\bthe drive\b"
    r"|\b[B-HJ-Z](?:'s\b| (?=(?:landed|lands|landing|gate|sat|has|is|was|tip|branch|chain|stack|lane|continues|keeps|waits)\b))"
)
DRAFTED_COPY = re.compile(r"\bProposed (?:reply|post|text|message)\b", re.IGNORECASE)
CODENAME_IN_QUESTION = (
    "An owner question names the work in plain words, never a codename, inbox id, or answer id."
    " Rename each one for what it is, such as the release-pipeline cutover stack or the deploy that runs after each merge, and ask again."
)
DRAFT_IN_QUESTION = (
    "The root never composes Slack copy, so a question never carries a proposed reply."
    " Hand the Slack lane the facts and links, then show its draft verbatim in a Send option's preview."
)


def prose(call: TaskCall) -> list[str]:
    lines = (line.strip() for line in (call.raw.get("prompt") or "").splitlines())
    return [line for line in lines if line and not line.startswith("ccx:")]


def incident_spawn(call: TaskCall, lines: list[str]) -> bool:
    return bool(INCIDENT_NAME.match(call.agent_name or "") or any(INCIDENT_BRIEF.search(line) for line in lines))


def spawn(name: str, prompt: str, **extra: object) -> Input:
    return Input(tool="Agent", tool_input={"name": name, "prompt": prompt}, state=ACTIVE, **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent")],
    skip_if=[FromSubagent(), Annotated("incident", "none")],
    tests={
        spawn("pr-review-pipeline-fix", "CI incident, effort high."): Block(pattern=r"orca-desk: launch <name> NOW"),
        spawn("incident-api-1n80-fix", "Fix it."): Block(),
        spawn("lane-failing-fix", INCIDENT_TURN, llm={"block": False}): Block(),
        spawn("api-1n80-fix", "ccx: role=fix\nFix the outage on api."): Block(),
        spawn("api-1n80-fix", "ccx: role=fix\nFix the outage on api.", llm={"block": False}): Warn(
            pattern=r"allowed, the model found the call outside the rule"
        ),
        spawn("lane-failing-fix", f"ccx: incident=none\n{INCIDENT_TURN}"): Allow(),
        spawn("flappy-monitors-2", NOT_AN_INCIDENT): Allow(),
        spawn("ignore-protect-preview", INCIDENT_ADJACENT): Allow(),
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
    role = evt.annotations.get("role")
    if role == FIX_ROLE and any(INCIDENT_AUTHORITY.search(line) for line in lines):
        return evt.block(INCIDENT_ROUTE)
    if role in (None, FIX_ROLE) and incident_spawn(call, lines[:1]):
        return evt.block(INCIDENT_ROUTE, confirm=INCIDENT_FIX)
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


def question_text(evt: BaseHookEvent) -> str:
    questions = evt._tool_input.get("questions") or []
    options = [option for question in questions for option in question.get("options") or []]
    return "\n".join(
        [question.get("question", "") for question in questions]
        + [f"{option.get('label', '')} {option.get('description', '')} {option.get('preview', '')}" for option in options]
    )


def ask(question: str, **option: str) -> Input:
    questions = [{"question": question, "header": "Ask", "options": [{"label": "Go", **option}, {"label": "Hold"}], "multiSelect": False}]
    return Input(tool="AskUserQuestion", tool_input={"questions": questions}, state=ACTIVE)


@on(
    Event.PreToolUse,
    only_if=[Tool("AskUserQuestion")],
    skip_if=[FromSubagent()],
    tests={
        ask("D gate: the plan said dry-run before enqueue. Which gate?"): Block(pattern=r"never a codename"),
        ask("Lift the hold?", description="walker walks the reverted HEAD after G304 lifts"): Block(pattern=r"never a codename"),
        ask("Extend the alert grant?", description="same terms as answer 543e865"): Block(pattern=r"never a codename"),
        ask('Proposed reply to Andrew in #platform-squad: "No design conflict." OK to post?'): Block(pattern=r"never composes Slack copy"),
        ask("Post the Slack lane's reply to Andrew?", preview="Thanks, no design conflict with anything in flight."): Allow(),
        ask("Land the release-pipeline cutover stack now, or after the 5pm freeze?", description="Options A and B"): Allow(),
        Input(tool="AskUserQuestion", tool_input={"questions": [{"question": "D gate?", "options": []}]}): Allow(),
        Input(tool="AskUserQuestion", tool_input={"questions": [{"question": "D gate?", "options": []}]}, agent_id="a1b2c3", state=ACTIVE): Allow(),
    },
)
def plain_words_in_owner_questions(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    text = question_text(evt)
    if DRAFTED_COPY.search(text):
        return evt.block(DRAFT_IN_QUESTION)
    if CODENAMES.search(text):
        return evt.block(CODENAME_IN_QUESTION)
    return None
