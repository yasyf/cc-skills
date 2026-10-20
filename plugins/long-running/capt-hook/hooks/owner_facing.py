from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cc_transcript.models import AssistantEvent
from captain_hook import (
    CONFIRMED,
    UNCONFIRMED,
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
    on,
)
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState, script
from .nudges import queue_nudge
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
SETTLED_INCIDENTS = frozenset({"closed", "recovered"})
INCIDENT_ENV = {"CLAUDE_CONFIG_DIR": str(Path(__file__).parent / "tests" / "fixtures" / "claude-config-incident")}
QUIET_ENV = {"CLAUDE_CONFIG_DIR": str(Path(__file__).parent / "tests" / "fixtures" / "claude-config")}
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
RELEASE_PROBES = (("release", "start"), ("release", "status"), ("release", "watch"))


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
        if record["status"] not in SETTLED_INCIDENT and datetime.fromisoformat(record["opened_at"]) >= since
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
    },
)
def no_plan_mode_in_an_open_incident(evt: BaseHookEvent) -> HookResult | None:
    if CompactionState.load(evt).active and open_incidents(evt):
        return evt.block(PLAN_MODE_IN_INCIDENT)
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


def open_incidents() -> list[dict]:
    """The incident executor's records that are still open: not closed or recovered, and judged ours."""
    root = Path(reqenv.getenv("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "long-running" / "incidents"
    records = (json.loads(path.read_text()) for path in sorted(root.glob("*.json")))
    return [record for record in records if record["status"] not in SETTLED_INCIDENTS and not record["facts"].get("not_ours")]


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests={
        Input(transcript=reply_transcript("sand 1437 is published."), state=ACTIVE, env=INCIDENT_ENV): Block(pattern=r"quote the RELEASED record"),
        Input(transcript=reply_transcript("sand 1437 is published (#103935)."), state=ACTIVE, env=INCIDENT_ENV): Allow(),
        Input(transcript=reply_transcript("sand 1437 is live: https://buildkite.com/forge/release/builds/8748"), state=ACTIVE, env=INCIDENT_ENV): Allow(),
        Input(transcript=reply_transcript("sand 1437 is published."), state=ACTIVE, env=QUIET_ENV): Allow(),
        Input(transcript=reply_transcript("The fix lane is building the PR."), state=ACTIVE, env=INCIDENT_ENV): Allow(),
        Input(transcript=reply_transcript("sand has not been cut yet; I will cut it next."), state=ACTIVE, env=INCIDENT_ENV): Allow(),
        Input(transcript=reply_transcript("sand 1437 is published."), env=INCIDENT_ENV): Allow(),
    },
)
def publish_lines_quote_the_released_record(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    reply = last_reply(evt)
    if PUBLISH_CLAIM.search(reply) and not PUBLISH_PROOF.search(reply) and open_incidents():
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


def root_probe(evt: BaseHookEvent) -> bool:
    return any(
        call.name in PROBES or (call.name == "ci" and call.args[:2] in RELEASE_PROBES)
        for call in evt.command.calls()
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
        probe("kubectl -n plat get deploy api", CLOSED_0820): Allow(),
        Input(command="kubectl -n plat get deploy api", commands=incident_sources(SAND_NOT_MIGRATED)): Allow(),
    },
)
def root_only_routes_during_an_incident(evt: BaseHookEvent) -> HookResult | None:
    if root_probe(evt) and CompactionState.load(evt).active and open_incidents(evt):
        return evt.block(ROOT_PROBE)
    return None
