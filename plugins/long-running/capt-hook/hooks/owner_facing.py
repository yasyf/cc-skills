from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cc_transcript.models import AssistantEvent, UserEvent
from captain_hook import (
    CONFIRMED,
    UNCONFIRMED,
    Allow,
    Annotated,
    BaseHookEvent,
    Block,
    CommandSchema,
    Confirm,
    Event,
    FromSubagent,
    HookResult,
    Input,
    Operand,
    Option,
    T,
    TaskCall,
    Tool,
    on,
)
from captain_hook.cmd import Call
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState, script
from .nudges import queue_nudge
from .root_context import operand_targets, reads_stdin
from .tests.brief_fixtures import INCIDENT_ADJACENT, INCIDENT_TURN, NOT_AN_INCIDENT
from .tests.handoff_fixtures import reply_transcript
from .tests.incident_fixtures import (
    BARE_INCIDENT,
    CLOSED_0820,
    OUTAGE_0820,
    OUTAGE_TWO,
    RECOVERED_0820,
    SAND_NOT_MIGRATED,
    STALE_0820,
)

ACTIVE = [CompactionState(active=True)]
INCIDENT_NAME = re.compile(r"^(?:incident|outage)-")
INCIDENT_BRIEF = re.compile(r"\b(?:incident|outage)\b", re.IGNORECASE)
INCIDENT_AUTHORITY = re.compile(r"\bIncident Turn\b")
FIX_ROLE = "fix"
INCIDENT_ROUTE = (
    "Launch the incident fix lane with `desk-launch.py incident <name> <fix-brief>`, or as an Agent named `<name>-fix` "
    "once `desk-launch.py refused <name>-fix --session <id>` prints a load refusal. "
    "A support lane's brief says `ccx: role=<role>`; a non-incident fix lane says `ccx: incident=none`."
)
REFUSAL = [sys.executable, str(script("desk-launch.py")), "refused"]
REFUSED = " ".join(REFUSAL)
LOAD_REFUSAL = "LAUNCH-FAILED desk-lane-api-1n80-fix/R474 api-1n80-fix: R474 refused on load 42 above 32 cores; nothing was launched"
REFUSAL_TIMEOUT_SECONDS = 20
INCIDENT_FIX = Confirm(rule="an incident FIX lane must run on the Orca incident alias; tooling, support, and repush lanes may spawn here")
REPLY_WINDOW = 256
UTC_CLOCK = re.compile(r"\b\d{1,2}:\d[\dx](?::\d\d)?\s?(?:Z|UTC)\b")
UTC_IN_REPLY = "Owner-facing times are Pacific with no zone label. Restate the UTC times from your last reply in Pacific."
PUBLISH_CLAIM = re.compile(r"(?<!not )(?<!n't )\b(?:is|are|was|were|been|now|went|got)\s+(?:published|released|live|cut)\b", re.IGNORECASE)
PUBLISH_PROOF = re.compile(r"#\d{3,}\b|buildkite\.com/\S+/builds/\d+")
PUBLISH_FROM_MEMORY = "quote the RELEASED record or run `tools/ci release live`; never from memory"
RECORD_IDS = re.compile(r"\b[GRL]\d{3,4}\b|\b(?:answer|grant|ruling|note|doc|log) [0-9a-f]{7}\b|\b(?:ctx|msg)_\w+")
LETTER_NAMES = re.compile(r"\b[B-HJ-Z]\b")
CODENAME_JUDGE = Confirm(
    rule="Block only when a bare capital letter stands in as the name of a stack, gate, lane, or piece of work, such"
    " as 'D gate' or 'B's tip', so the owner could not pick an option without first asking what the letter means. A"
    " letter that labels an option, plan, or list item, such as 'Option B' or 'B: lane', or that belongs to a real"
    " name or unit, never blocks."
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
INCIDENT_WINDOW = timedelta(hours=6)
INCIDENT_STORE = [sys.executable, str(script("actions.py")), "list", "--json"]
INCIDENT_DIGEST = ["cci", "digest", "--json", "--since", "6h", "--budget", "4000000"]
SETTLED_INCIDENT = ("closed", "recovered")
INCIDENT_SOURCE_TIMEOUT_SECONDS = 10
PLAN_MODE_IN_INCIDENT = (
    "plan mode is forbidden while a customer-visible outage is open; the forward fix ships from the lane that holds it;"
    " replan after fix-live"
)
RECORD_SEQ = re.compile(r"#\d")
OFF_INCIDENT_ASK = "the alert is active: no owner question on other work; the incident's own ask names {names}"
ROOT_PROBE = "during an incident the root only spawns, routes, and acks: send this to a lane (desk-launch.py lane ...)"
PROBES = ("kubectl", "pup", "curl")
PLAN_TALK = re.compile(r"\bplan[ -]?mode\b|\breplan\b", re.IGNORECASE)
PLAN_CONDITIONAL = re.compile(
    r"\bplan[ -]?mode\b[^.\n]{0,40}?\b(?:without|until|before|after|only (?:when|once|after))\b[^.\n]{0,60}?\b(?:incident|outage|fix\w*|done|resolved|closed|green|live)\b"
    r"|\b(?:until|after|once)\b[^.\n]{0,60}?\b(?:incident|outage|fix\w*|done|resolved|closed|green|live)\b[^.\n]{0,40}?\bplan[ -]?mode\b",
    re.IGNORECASE,
)
PLAN_FORBIDDEN = re.compile(
    r"\b(?:dont|don'?t|do not|never|no|not)\s+(?:\w+\s+){0,3}?(?:(?:enter|endter|go (?:in)?to|start|use|re-?enter)\s+)?(?:plan[ -]?mode|replan)\b",
    re.IGNORECASE,
)
PLAN_ORDERED = re.compile(r"\b(?:enter|endter|go (?:in)?to|start|use|re-?enter|get into)\b[^.\n]{0,20}?\bplan[ -]?mode\b|\breplan\b", re.IGNORECASE)
OUTAGE_STOP_WORDS = (
    "a second outage means we stop patching, re-enter plan mode, and find the fundamental fix"
)
CAUSE_RECORD = Path(__file__).parent / "tests" / "fixtures" / "root-cause" / "autoscaler.txt"
NO_PLAN_BEFORE_FIX = "dont enter plan mode without fixing the incident first"
PLAN_WHEN_DONE = "check every incident item is done, then enter plan mode only when everything is done"
PLAN_MODE_FORBIDDEN = (
    "The owner's latest word on plan mode forbids it now. Ship the fix from the lane that holds it, and enter plan mode only when the owner orders it."
)
CAUSE = re.compile(r"\b(?:because|caus(?:e|ed|es|ing)|due to|mechanism|is why|was why|led to|triggered by|results? from)\b", re.IGNORECASE)
CAUSE_KINDS = {"go", "decision", "decide", "note"}
RECORD_REF = re.compile(r"#(\d{3,})\b")
UNQUOTED_CAUSE = (
    "A root record that names a cause cites the lane `MECHANISM` record it rests on."
    " Quote that record's `#<seq>` in the text, or send the question to a lane with `desk-launch.py lane`."
)
CAUSE_JUDGE = Confirm(
    rule="Block only when the root record asserts why a failure happened, its cause or mechanism, with no lane MECHANISM record cited;"
    " a record that explains a plan, routing, or ownership choice never blocks."
)
RELEASE_PROBES = (("release", "start"), ("release", "status"), ("release", "watch"))
INBOX_FILE = re.compile(r"/inbox/[\w.-]+\.md\b")
AUTHORIZING_LINE = re.compile(r"\b(?:GO|LIFT|UNBLOCK) root \(.*")
AUTHORIZING_KINDS = {"go", "lift", "unblock"}
PR_REF = re.compile(r"#\d{3,}")
PR_TAIL = re.compile(r"\d{3,}$")
ROUTING = re.compile(r"(?i)\brout(?:e|es|ed|ing)\b|\bcluster (?:assignment|move)\b|\bcutover\b")
BOARD_VERBS = ("push", "start", "update-block")
AUTHORIZED_BESIDE_A_QUESTION = (
    "A GO, LIFT, or UNBLOCK line or `cci post` record is never written while an owner question on its subject is unanswered."
    " Wait for the answer, then write it in a later turn."
)
SAME_SUBJECT = Confirm(
    rule="Block only when the GO, LIFT, or UNBLOCK acts on the same pull request or routing change that an unanswered"
    " owner question or a board posted this turn asks about."
)
CCI_POST = CommandSchema(
    "cci",
    operands=(Operand("words", count="*"),),
    options=(
        *(
            Option(name, (f"--{name}",))
            for name in (
                "attr", "board", "body-file", "build", "ccn", "census", "commit", "count", "drive", "env", "id", "kind", "lane",
                "lane-ref", "mode", "outcome", "path", "pr", "re", "resolves", "stack", "target", "text", "to", "topic", "ttl", "url",
            )
        ),
        Option("json", ("--json",), bool),
    ),
)


def prose(call: TaskCall) -> list[str]:
    lines = (line.strip() for line in (call.raw.get("prompt") or "").splitlines())
    return [line for line in lines if line and not line.startswith("ccx:")]


def incident_spawn(call: TaskCall, lines: list[str]) -> bool:
    return bool(INCIDENT_NAME.match(call.agent_name or "") or any(INCIDENT_BRIEF.search(line) for line in lines))


def refused_on_load(evt: BaseHookEvent, lane: str) -> bool:
    argv = [*REFUSAL, lane, "--session", evt.session_id]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=REFUSAL_TIMEOUT_SECONDS, env=dict(reqenv.env_map()))
    except subprocess.TimeoutExpired:
        return False
    return done.returncode == 0 and bool(done.stdout.strip())


def spawn(name: str, prompt: str, **extra: object) -> Input:
    return Input(tool="Agent", tool_input={"name": name, "prompt": prompt}, state=ACTIVE, **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent")],
    skip_if=[FromSubagent(), Annotated("incident", "none")],
    tests={
        spawn("pr-review-pipeline-fix", "CI incident, effort high.", decide=CONFIRMED, commands={REFUSED: ""}): Block(
            pattern=r"desk-launch\.py incident <name> <fix-brief>[\s\S]*desk-launch\.py refused <name>-fix"
        ),
        spawn("incident-api-1n80-fix", "Fix it.", decide=CONFIRMED, commands={REFUSED: ""}): Block(),
        spawn("lane-failing-fix", INCIDENT_TURN, decide=UNCONFIRMED, commands={REFUSED: ""}): Block(),
        spawn("api-1n80-fix", "ccx: role=fix\nFix the outage on api.", decide=CONFIRMED, commands={REFUSED: ""}): Block(),
        spawn("api-1n80-fix", "ccx: role=fix\nFix the outage on api.", decide=CONFIRMED, commands={REFUSED: LOAD_REFUSAL}): Allow(),
        spawn("lane-failing-fix", INCIDENT_TURN, decide=UNCONFIRMED, commands={REFUSED: LOAD_REFUSAL}): Allow(),
        spawn("api-1n80-fix", "ccx: role=fix\nFix the outage on api.", decide=UNCONFIRMED, commands={REFUSED: ""}): Allow(),
        spawn("lane-failing-fix", f"ccx: incident=none\n{INCIDENT_TURN}"): Allow(),
        spawn("flappy-monitors-2", NOT_AN_INCIDENT): Allow(),
        spawn("ignore-protect-preview", INCIDENT_ADJACENT): Allow(),
        spawn("incident-runner", "Lane incident-runner: a durable incident executor.", decide=UNCONFIRMED, commands={REFUSED: ""}): Allow(),
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
    authorized = role == FIX_ROLE and any(INCIDENT_AUTHORITY.search(line) for line in lines)
    if not authorized and not (role in (None, FIX_ROLE) and incident_spawn(call, lines[:1])):
        return None
    if call.agent_name and refused_on_load(evt, call.agent_name):
        return None
    return evt.block(INCIDENT_ROUTE) if authorized else evt.block(INCIDENT_ROUTE, confirm=INCIDENT_FIX)


def incident_source(evt: BaseHookEvent, argv: list[str]) -> str:
    env = dict(reqenv.env_map()) | {"CLAUDE_CODE_SESSION_ID": evt.session_id}
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=INCIDENT_SOURCE_TIMEOUT_SECONDS, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout if done.returncode == 0 else ""


def open_incidents(evt: BaseHookEvent) -> list[tuple[str, ...]]:
    since = datetime.now(UTC) - INCIDENT_WINDOW
    stored = [
        (record["incident_id"], record["facts"].get("target"), record["facts"].get("alert"))
        for record in json.loads(incident_source(evt, INCIDENT_STORE) or "[]")
        if record["status"] not in SETTLED_INCIDENT
        and not record["facts"].get("not_ours")
        and datetime.fromisoformat(record["opened_at"]) >= since
    ]
    posted = [
        (f"#{record['seq']}", record.get("topic"), record.get("id"), *((record.get("refs") or {}).get("targets") or []))
        for record in json.loads(incident_source(evt, INCIDENT_DIGEST) or "{}").get("open_incidents") or []
    ]
    return [tuple(name for name in names if name) for names in stored + posted]


def incident_sources(*stored: dict, posted: tuple[dict, ...] = ()) -> dict[str, str]:
    return {
        " ".join(INCIDENT_STORE): json.dumps(list(stored)),
        " ".join(INCIDENT_DIGEST): json.dumps({"open_incidents": list(posted)}),
    }


def plan_mode(tool: str, *stored: dict, posted: tuple[dict, ...] = (), **extra: object) -> Input:
    return Input(tool=tool, tool_input={}, state=ACTIVE, commands=incident_sources(*stored, posted=posted), **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("EnterPlanMode", "ExitPlanMode")],
    skip_if=[FromSubagent()],
    tests={
        plan_mode("EnterPlanMode", OUTAGE_0820): Block(pattern="forbidden while a customer-visible outage is open"),
        plan_mode("ExitPlanMode", OUTAGE_0820): Block(pattern="forbidden while a customer-visible outage is open"),
        plan_mode("EnterPlanMode", posted=(OUTAGE_TWO,)): Block(pattern="replan after fix-live"),
        plan_mode("EnterPlanMode", CLOSED_0820): Allow(),
        plan_mode("EnterPlanMode", RECOVERED_0820): Allow(),
        plan_mode("EnterPlanMode", STALE_0820): Allow(),
        Input(tool="EnterPlanMode", tool_input={}, commands=incident_sources(OUTAGE_0820)): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, agent_id="a1b2c3"): Allow(),
        plan_mode("EnterPlanMode", CLOSED_0820, transcript=[T.user("dont enter plan mode")]): Block(pattern="latest word on plan mode forbids it"),
        plan_mode("EnterPlanMode", CLOSED_0820, transcript=[T.user("no plan mode today")]): Block(),
        plan_mode("EnterPlanMode", CLOSED_0820, transcript=[T.user("do not enter plan mode until I ask")]): Block(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user("don't replan")]): Block(pattern="latest word on plan mode forbids it"),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user("enter plan mode first")]): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user("dont enter plan mode yet"), T.user("ok now re-enter plan mode and replan")]): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user("no, go into plan mode")]): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user("dont fix anything else, enter plan mode now")]): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user(OUTAGE_STOP_WORDS)]): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user(NO_PLAN_BEFORE_FIX)]): Block(pattern="replan after fix-live"),
        plan_mode("EnterPlanMode", CLOSED_0820, transcript=[T.user(NO_PLAN_BEFORE_FIX)]): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user(PLAN_WHEN_DONE)]): Block(pattern="replan after fix-live"),
        plan_mode("EnterPlanMode", CLOSED_0820, transcript=[T.user(PLAN_WHEN_DONE)]): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user("ship the fix")]): Block(pattern="replan after fix-live"),
        plan_mode("EnterPlanMode", CLOSED_0820, transcript=[T.user("ship the fix")]): Allow(),
    },
)
def no_plan_mode_in_an_open_incident(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    word = latest_plan_word(evt) or ""
    if not PLAN_CONDITIONAL.search(word) and PLAN_FORBIDDEN.search(word):
        return evt.block(PLAN_MODE_FORBIDDEN)
    if not PLAN_CONDITIONAL.search(word) and PLAN_ORDERED.search(word):
        return None
    return evt.block(PLAN_MODE_IN_INCIDENT) if open_incidents(evt) else None


def owner_words(evt: BaseHookEvent) -> list[str]:
    return [
        event.text
        for event in evt.ctx.t.events
        if isinstance(event, UserEvent)
        and not event.meta.is_sidechain
        and not event.is_agent_injected
        and event.text
        and not event.text.lstrip().startswith(("<", "[Request interrupted", "This session is being continued"))
    ]


def latest_plan_word(evt: BaseHookEvent) -> str | None:
    return next((text for text in reversed(owner_words(evt)) if PLAN_TALK.search(text)), None)


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


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests={
        Input(transcript=reply_transcript("sand 1437 is published."), state=ACTIVE, commands=incident_sources(OUTAGE_0820)): Block(
            pattern=r"quote the RELEASED record"
        ),
        Input(transcript=reply_transcript("sand 1437 is published (#103935)."), state=ACTIVE, commands=incident_sources(OUTAGE_0820)): Allow(),
        Input(
            transcript=reply_transcript("sand 1437 is live: https://buildkite.com/forge/release/builds/8748"),
            state=ACTIVE,
            commands=incident_sources(OUTAGE_0820),
        ): Allow(),
        Input(transcript=reply_transcript("sand 1437 is published."), state=ACTIVE, commands=incident_sources()): Allow(),
        Input(transcript=reply_transcript("The fix lane is building the PR."), state=ACTIVE, commands=incident_sources(OUTAGE_0820)): Allow(),
        Input(transcript=reply_transcript("sand has not been cut yet; I will cut it next."), state=ACTIVE, commands=incident_sources(OUTAGE_0820)): Allow(),
        Input(transcript=reply_transcript("sand 1437 is published."), commands=incident_sources(OUTAGE_0820)): Allow(),
    },
)
def publish_lines_quote_the_released_record(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    reply = last_reply(evt)
    if PUBLISH_CLAIM.search(reply) and not PUBLISH_PROOF.search(reply) and open_incidents(evt):
        return evt.block(PUBLISH_FROM_MEMORY)
    return None


def question_text(evt: BaseHookEvent) -> str:
    questions = evt._tool_input.get("questions") or []
    options = [option for question in questions for option in question.get("options") or []]
    return "\n".join(
        [question.get("question", "") for question in questions]
        + [f"{option.get('label', '')} {option.get('description', '')} {option.get('preview', '')}" for option in options]
    )


def ask(
    question: str, decide: dict[str, object] | None = None, *, incidents: tuple[dict, ...] = (), posted: tuple[dict, ...] = (), **option: str
) -> Input:
    questions = [{"question": question, "header": "Ask", "options": [{"label": "Go", **option}, {"label": "Hold"}], "multiSelect": False}]
    return Input(
        tool="AskUserQuestion",
        tool_input={"questions": questions},
        state=ACTIVE,
        decide=decide or CONFIRMED,
        commands=incident_sources(*incidents, posted=posted),
    )


def incident_label(incidents: list[tuple[str, ...]]) -> str:
    readable = [next((name for name in names if not RECORD_SEQ.search(name)), "") for names in incidents]
    return ", ".join(name for name in readable if name) or "its target, topic, or record number"


def names_an_incident(text: str, names: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) for name in names)


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
        ask("Land the release-pipeline cutover stack now, or after the 5pm freeze?", UNCONFIRMED, description="Options A and B"): Allow(),
        ask("Land the release-pipeline cutover stack now?", UNCONFIRMED, description="B lands after D"): Allow(),
        ask("Apply G327 per R1053?"): Block(pattern=r"never a codename"),
        ask("Retry the dispatch?", description="ctx_8f2a1 stalled; log c093750 has the trace"): Block(pattern=r"never a codename"),
        ask("Resend the relay?", description="msg_01HXQ never arrived"): Block(pattern=r"never a codename"),
        ask(
            "For Slack releases whose plan shows a delete or replace, should the drive also approve on your behalf without asking, or keep bringing you the delete list first?",
            description="Safe plans are approved unasked; any delete or replace pauses at Platy's hold until you read the list, as today.",
        ): Allow(),
        ask(
            "For a Slack release whose plan shows a delete or a replace, should the drive also approve it for you without asking, or keep bringing you the list of resources first?",
            description="The lane approves every Slack release, deletes and replaces included, and reports the counts afterward.",
        ): Allow(),
        ask(
            "Eleven of your older pull requests that predate the release pipeline work are still open and untouched. What should happen to them?",
            description="They are not part of the release pipeline program; the drive keeps landing only its own pull requests.",
        ): Allow(),
        ask(
            "The 11 older PRs of yours outside the drive (#29147, #28357, #22400–#22402, #17808) are untouched. What should happen to them?",
            description="Leave them: the drive keeps landing only its own PRs, such as #29976.",
        ): Allow(),
        ask(
            "The AWS single sign-on session expired at about 11:12 PM. The command is `aws sso login --sso-session forge`. Have you signed in?",
            description="I tell the walker and the release lane to retry their sign-ins now.",
        ): Allow(),
        ask("Six Orca lanes (d-land, merge-walker, deploy-scope-fix) show Login expired. How do you want them back?"): Allow(),
        Input(tool="AskUserQuestion", tool_input={"questions": [{"question": "D gate?", "options": []}]}): Allow(),
        Input(tool="AskUserQuestion", tool_input={"questions": [{"question": "D gate?", "options": []}]}, agent_id="a1b2c3", state=ACTIVE): Allow(),
        ask("Should I extend the backlog deadline to 11:30?", incidents=(SAND_NOT_MIGRATED,)): Block(pattern=r"the alert is active[\s\S]*sand-not-migrated"),
        ask("sand-not-migrated: approve the 0-delete apply on plat?", incidents=(SAND_NOT_MIGRATED,)): Allow(),
        ask("Approve the 0-delete sand apply on plat?", incidents=(SAND_NOT_MIGRATED,)): Allow(),
        ask("Should I extend the backlog deadline to 11:30?", incidents=(SAND_NOT_MIGRATED | {"status": "closed"},)): Allow(),
        ask("Should I extend the backlog deadline to 11:30?", posted=(OUTAGE_TWO,)): Block(pattern=r"the alert is active[\s\S]*outage-two"),
        ask("Roll the executor back for outage two (#102819)?", posted=(OUTAGE_TWO,)): Allow(),
        ask("Should I extend the thousand-row backlog?", incidents=(SAND_NOT_MIGRATED,)): Block(pattern=r"the alert is active"),
        ask("Should I extend the backlog deadline to noon?", posted=(BARE_INCIDENT,)): Block(pattern=r"names its target, topic, or record number"),
        ask("Hand-publish sand for #103958?", posted=(BARE_INCIDENT,)): Allow(),
    },
)
def plain_words_in_owner_questions(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    text = question_text(evt)
    if (incidents := open_incidents(evt)) and not any(names_an_incident(text, names) for names in incidents):
        return evt.block(OFF_INCIDENT_ASK.format(names=incident_label(incidents)))
    if DRAFTED_COPY.search(text):
        return evt.block(DRAFT_IN_QUESTION)
    if RECORD_IDS.search(text):
        return evt.block(CODENAME_IN_QUESTION)
    if LETTER_NAMES.search(text):
        return evt.block(CODENAME_IN_QUESTION, confirm=CODENAME_JUDGE)
    return None


def scratch(value: str) -> bool:
    path = Path.home() / value[2:] if value.startswith("~/") else Path(value)
    return path.is_absolute() and any(path.is_relative_to(root) for root in (Path.home() / ".claude", Path("/tmp"), Path("/private/tmp")))


def product_read(call: Call, calls: list[Call]) -> bool:
    searches = call.name == "rg" or call.verb_argv[:2] == ("git", "show") or (call.name == "ccx" and call.args[:1] == ("code",))
    if not searches or (call.name == "rg" and reads_stdin(call, calls)):
        return False
    places = [target.value for target in operand_targets(call) if target.value and "/" in target.value]
    return not places or not all(scratch(place) for place in places)


def root_probe(evt: BaseHookEvent) -> bool:
    calls = list(evt.command.calls())
    return any(
        call.name in PROBES or (call.name == "ci" and call.args[:2] in RELEASE_PROBES) or product_read(call, calls)
        for call in calls
    )


def probe(command: str, *stored: dict, **extra: object) -> Input:
    return Input(command=command, state=ACTIVE, commands=incident_sources(*stored), **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    skip_if=[FromSubagent()],
    tests={
        probe("kubectl -n plat get deploy api", SAND_NOT_MIGRATED): Block(pattern="only spawns, routes, and acks"),
        probe("curl -s https://api.poetic.com/health", SAND_NOT_MIGRATED): Block(pattern="only spawns, routes, and acks"),
        probe("pup monitors list", SAND_NOT_MIGRATED): Block(),
        probe("tools/ci release start --emergency sand", SAND_NOT_MIGRATED): Block(),
        probe("./tools/ci release watch 2026-10-20/31", SAND_NOT_MIGRATED): Block(),
        probe("/opt/homebrew/bin/kubectl get pods", SAND_NOT_MIGRATED): Block(),
        probe("tools/ci release plan --from 94d61b9", SAND_NOT_MIGRATED): Allow(),
        probe("kubectl -n plat get deploy api", SAND_NOT_MIGRATED, agent_id="aevidence-0123456789abcdef"): Allow(),
        probe("cci post --kind decision --lane root --to inc-sand-fix 'ship it'", SAND_NOT_MIGRATED): Allow(),
        probe("echo 'kubectl get pods'", SAND_NOT_MIGRATED): Allow(),
        probe("git show HEAD:service/store/open.go", SAND_NOT_MIGRATED): Block(pattern="only spawns, routes, and acks"),
        probe("rg -n Retire service/store", SAND_NOT_MIGRATED): Block(),
        probe("rg -n Retire", SAND_NOT_MIGRATED): Block(),
        probe("ccx code read service/store/open.go --section 170-200", SAND_NOT_MIGRATED): Block(),
        probe("ccx code search 'who retires a store'", SAND_NOT_MIGRATED): Block(),
        probe("rg -n MECHANISM ~/.claude/scratch/drive/inbox", SAND_NOT_MIGRATED): Allow(),
        probe("rg -n 'api/health' /tmp/records", SAND_NOT_MIGRATED): Allow(),
        probe("rg -n '~nobody/x' /tmp/records", SAND_NOT_MIGRATED): Allow(),
        probe("cci tail --drive release-v3 --cursor root | rg -n MECHANISM", SAND_NOT_MIGRATED): Allow(),
        probe("cci tail --drive release-v3 --cursor root | grep -n MECHANISM", SAND_NOT_MIGRATED): Allow(),
        probe("git status", SAND_NOT_MIGRATED): Allow(),
        probe("git show HEAD:service/store/open.go", CLOSED_0820): Allow(),
        probe("kubectl -n plat get deploy api", CLOSED_0820): Allow(),
        Input(command="kubectl -n plat get deploy api", commands=incident_sources(SAND_NOT_MIGRATED)): Allow(),
    },
)
def root_only_routes_during_an_incident(evt: BaseHookEvent) -> HookResult | None:
    if root_probe(evt) and CompactionState.load(evt).active and open_incidents(evt):
        return evt.block(ROOT_PROBE)
    return None


def mechanism_cited(evt: BaseHookEvent, drive: tuple, refs: list[str]) -> bool | None:
    for ref in refs:
        argv = ["cci", "grep", f"^#{ref} ", "--kind", "mechanism", "--json", "-n", "1", *(["--drive", str(drive[0])] if drive else [])]
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=INCIDENT_SOURCE_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if done.returncode != 0:
            return None
        if done.stdout.strip():
            return True
    return False


def attached(values: dict[str, tuple]) -> str:
    paths = [Path(str(value)).expanduser() for key in ("path", "body-file") for value in values.get(key, ()) if value]
    return "\n".join(path.read_text(errors="replace") for path in paths if path.is_file())


def causal_records(evt: BaseHookEvent) -> list[tuple[dict[str, tuple], str]]:
    posts = [values for call in evt.command.calls("cci") if (values := CCI_POST.bind(call).values).get("words", ())[:1] == ("post",)]
    return [
        (values, text)
        for values in posts
        if CAUSE_KINDS & set(values.get("kind", ())) and CAUSE.search(text := f"{record_text(values)}\n{attached(values)}")
    ]


def cause(command: str, **extra: object) -> Input:
    return Input(command=command, state=ACTIVE, decide=extra.pop("decide", CONFIRMED), **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    skip_if=[FromSubagent()],
    tests={
        cause("cci post --drive release-v3 --lane root --kind go --to worker-fix --text 'the autoscaler caused the worker crashes; scale the pool'"): Block(
            pattern=r"cites the lane `MECHANISM` record"
        ),
        cause(
            "cci post --drive release-v3 --lane root --kind decision --to worker-fix --text 'a missing object is why uploads fail (#104717)'",
            commands={"cci grep": ""},
        ): Block(),
        cause(
            "cci post --drive release-v3 --lane root --kind go --to worker-fix --text 'MECHANISM #104942: a missing object caused it; ship the fix'",
            commands={"cci grep": '{"seq": 104942, "kind": "mechanism"}'},
        ): Allow(),
        cause("cci post --drive release-v3 --lane root --kind go --to worker-fix --text 'the autoscaler caused the crashes'", decide=UNCONFIRMED): Allow(),
        cause("cci post --drive release-v3 --lane root --kind go --to landing-desk --pr 41464 --text 'land it'"): Allow(),
        cause("cci post --drive release-v3 --lane root --kind head --text 'p1 abc because rebased'"): Allow(),
        cause("cci tail --drive release-v3 --cursor root"): Allow(),
        cause(f"cci post --drive release-v3 --lane root --kind go --to worker-fix --path {CAUSE_RECORD}"): Block(),
        Input(command="cci post --drive release-v3 --lane root --kind go --text 'the autoscaler caused it'", decide=CONFIRMED): Allow(),
        cause("cci post --drive release-v3 --lane x --kind go --text 'the autoscaler caused it'", agent_id="a1b2c3"): Allow(),
    },
)
def root_causes_quote_a_lane_mechanism(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    for values, text in causal_records(evt):
        refs = RECORD_REF.findall(" ".join([text, *(str(value) for key in ("re", "resolves") for value in values.get(key, ()))]))
        if mechanism_cited(evt, values.get("drive", ()), refs) is False:
            return evt.block(UNQUOTED_CAUSE, confirm=CAUSE_JUDGE)
    return None


def subjects(text: str) -> set[str]:
    return set(PR_REF.findall(text)) | ({"routing"} if ROUTING.search(text) else set())


def appends_to_inbox(evt: BaseHookEvent) -> bool:
    return any(
        INBOX_FILE.search(target)
        for call in evt.command.calls()
        for target in [*(redirect.target for redirect in call.redirects), *(call.args if call.name == "tee" else ())]
    )


def inbox_text(evt: BaseHookEvent) -> str:
    if evt.tool_name == "Bash":
        return str(evt.command) if appends_to_inbox(evt) else ""
    return (evt.content or "") if evt.file is not None and INBOX_FILE.search(str(evt.file.path)) else ""


def record_text(values: dict[str, tuple]) -> str:
    prs = [f"#{tail[0]}" for value in values.get("pr", ()) if value for part in value.split(",") if (tail := PR_TAIL.search(part))]
    words = [*values.get("text", ()), *values.get("words", ())[1:], *values.get("topic", ())]
    return " ".join([*(str(word) for word in words if word), *prs])


def authorizing_lines(evt: BaseHookEvent) -> list[str]:
    records = [
        record_text(values)
        for call in evt.command.calls("cci")
        if (values := CCI_POST.bind(call).values).get("words", ())[:1] == ("post",)
        and AUTHORIZING_KINDS & set(values.get("kind", ()))
    ]
    return AUTHORIZING_LINE.findall(inbox_text(evt)) + records


def posts_board(use) -> bool:
    return any(cmd.runs("cc-present", verb) for cmd in use.call.command_line for verb in BOARD_VERBS)


def board_text(evt: BaseHookEvent, use) -> str:
    command = use.call.command
    written = evt.ctx.turn.tool_calls.named("Write").where(lambda write: write.call.file_path in command)
    return "\n".join([command, *(write.call.content for write in written.items)])


def asked_subjects(evt: BaseHookEvent) -> set[str]:
    open_questions = evt.ctx.t.tool_calls.named("AskUserQuestion").where(lambda use: use.result is None)
    boards = evt.ctx.turn.tool_calls.named("Bash").where(posts_board)
    texts = [json.dumps(use.call.raw, ensure_ascii=False) for use in open_questions.items]
    texts += [board_text(evt, use) for use in boards.items]
    return set().union(*map(subjects, texts))


DRIVE_INBOX = "/home/u/.claude/scratch/drive/inbox/deploy-go.md"
ROUTING_QUESTION = T.assistant(
    T.tool("AskUserQuestion", questions=[{"question": "Switch acme routing to cluster-b now?", "options": [{"label": "Switch"}, {"label": "Hold"}]}])
)
MERGE_QUESTION = T.assistant(
    T.tool("AskUserQuestion", questions=[{"question": "Merge #30502 now?", "options": [{"label": "Merge"}, {"label": "Hold"}]}])
)
ROUTING_GO = f'echo "GO root (11:02 PM) acme-routing: switch acme routing to cluster-b now" >> {DRIVE_INBOX}'


def authorize(*transcript: object, decide: dict[str, object] | None = None, **call: object) -> Input:
    return Input(transcript=[T.user("keep going"), *transcript], state=ACTIVE, decide=decide or CONFIRMED, **call)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash", "Edit", "Write")],
    skip_if=[FromSubagent()],
    tests={
        authorize(ROUTING_QUESTION, command=ROUTING_GO): Block(pattern="later turn"),
        authorize(
            T.assistant(T.tool("Bash", command="cc-present push - <<'EOF'\n{\"blocks\": [{\"title\": \"Should acme routing switch?\"}]}\nEOF")),
            command=f"cat >> {DRIVE_INBOX} <<'EOF'\nLIFT root (11:02 PM) acme routing fence lifted.\nEOF",
        ): Block(),
        authorize(MERGE_QUESTION, tool="Write", file=DRIVE_INBOX, content="UNBLOCK root (11:02 PM) #30502 may merge now.\n"): Block(),
        authorize(
            T.assistant(T.tool("Write", file_path="/tmp/board.json", content='{"title": "Switch acme routing?"}')),
            T.assistant(T.tool("Bash", command="cc-present push /tmp/board.json")),
            tool="Write",
            file=DRIVE_INBOX,
            content="GO root (11:02 PM) acme routing switch applied.\n",
        ): Block(),
        authorize(MERGE_QUESTION, command="cci post --lane root --kind go --to landing-desk --pr 30502 --text 'land it'"): Block(
            pattern="cci post"
        ),
        authorize(ROUTING_QUESTION, command="cci post --lane root --kind lift --to deploy-desk 'acme routing fence lifted'"): Block(),
        authorize(MERGE_QUESTION, command="cci post --lane root --kind note --to landing-desk --pr 30502 --text 'question is open'"): Allow(),
        authorize(MERGE_QUESTION, command="cci post --lane root --kind go --to landing-desk --pr 30409 --text 'land it'"): Allow(),
        authorize(MERGE_QUESTION, command="cci post --lane root --kind go --to landing-desk --pr 30502 --text 'land it'", decide=UNCONFIRMED): Allow(),
        authorize(command="cc-present start --session s1 --doc /tmp/board.json"): Allow(),
        authorize(command=ROUTING_GO): Allow(),
        authorize(ROUTING_QUESTION, command=f'echo "STATE census (11:02 PM) acme routing question is open" >> {DRIVE_INBOX}'): Allow(),
        authorize(ROUTING_QUESTION, command=f'echo "GO root (11:02 PM) release-simplify-2: take over #30409" >> {DRIVE_INBOX}'): Allow(),
        authorize(
            T.tool_turn(
                "AskUserQuestion",
                result="Switch",
                questions=[{"question": "Switch acme routing to cluster-b now?", "options": [{"label": "Switch"}, {"label": "Hold"}]}],
            ),
            command=ROUTING_GO,
        ): Allow(),
        Input(
            transcript=[
                T.user("ask the owner"),
                T.assistant(T.tool("Bash", command="cc-present push /tmp/board.json")),
                T.user("the owner answered: switch"),
            ],
            command=ROUTING_GO,
            state=ACTIVE,
        ): Allow(),
        Input(transcript=[T.user("keep going"), ROUTING_QUESTION], command=ROUTING_GO): Allow(),
    },
)
def authorizing_lines_wait_for_the_owner_question(evt: BaseHookEvent) -> HookResult | None:
    lines = authorizing_lines(evt)
    if not lines or not CompactionState.load(evt).active:
        return None
    asked = asked_subjects(evt)
    return evt.block(AUTHORIZED_BESIDE_A_QUESTION, confirm=SAME_SUBJECT) if any(subjects(line) & asked for line in lines) else None
