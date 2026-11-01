from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cached_property
from pathlib import Path

from cc_transcript.models import AssistantEvent, UserEvent
from captain_hook import (
    Allow,
    Annotated,
    BaseHookEvent,
    CommandSchema,
    Event,
    FromSubagent,
    HookResult,
    Input,
    Operand,
    Option,
    T,
    TaskCall,
    Tool,
    Warn,
    ast_grep,
    on,
)
from captain_hook.annotations import dispatch_pairs
from captain_hook.cmd import Call
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState, ccn, drive_entry, post_blocker, rulings, script, script_module
from .judge_metrics import judge_call
from .nudges import once_per_hour, queue_nudge
from .pr_ledger import lane_name
from .register_injection import drive_args, incident_spawn, prose
from .root_context import operand_targets, reads_stdin
from .tests.board_fixtures import ANSWERS, GLOSSARY, JUDGE, REPO, ROWS, board, judged
from .tests.brief_fixtures import INCIDENT_ADJACENT, INCIDENT_TURN, NOT_AN_INCIDENT
from .tests.handoff_fixtures import reply_transcript
from .tests.register_fixtures import DRIVE_CONFIG
from .tests.incident_fixtures import (
    BARE_INCIDENT,
    CLOSED_0820,
    OUTAGE_0820,
    OUTAGE_TWO,
    RECOVERED_0820,
    SAND_NOT_MIGRATED,
    STALE_0820,
)
from .tests.rulings_consumers import separate

ACTIVE = [CompactionState(active=True)]
INCIDENT_AUTHORITY = re.compile(r"\bIncident Turn\b")
FIX_ROLE = "fix"
INCIDENT_ROUTE = (
    "Warning, nothing was blocked: Launch an incident fix lane with `desk-launch.py incident <name> <fix-brief>`, or an Agent named `<name>-fix` "
    "once `desk-launch.py refused <name>-fix --session <id>` refuses for load. "
    "Support briefs say `ccx: role=<role>`; a non-incident fix lane, `ccx: incident=none`."
)
READER_ROLE = "reader"
READER_NAME = re.compile(r"^(?:slack|alert)-[\w-]*\b(?:read|reader|look|thread)\b")
ALERT_TALK = re.compile(r"\b(?:incident|outage|alert)s?\b", re.IGNORECASE)
FIX_BESIDE_READER = (
    "Warning, nothing was blocked: a Slack or alert reader lane launches beside the incident fix lane, in the same turn. "
    "Launch the fix now with `desk-launch.py incident <name> <fix-brief>`."
)
REFUSAL = [sys.executable, str(script("desk-launch.py")), "refused"]
REFUSED = " ".join(REFUSAL)
LOAD_REFUSAL = "LAUNCH-FAILED desk-lane-api-1n80-fix/R474 api-1n80-fix: R474 refused on load 42 above 32 cores; nothing was launched"
REFUSAL_TIMEOUT_SECONDS = 20
REPLY_WINDOW = 256
UTC_CLOCK = re.compile(r"\b\d{1,2}:\d[\dx](?::\d\d)?\s?(?:Z|UTC)\b")
UTC_IN_REPLY = "Next reply to the owner: give times in Pacific with no zone label, never UTC."
PUBLISH_CLAIM = re.compile(r"(?<!not )(?<!n't )\b(?:is|are|was|were|been|now|went|got)\s+(?:published|released|live|cut)\b", re.IGNORECASE)
RELEASE_NUMBER = re.compile(r"\b\d{3,}\b|\bv?\d+\.\d+(?:\.\d+)?\b")
PUBLISH_PROOF = re.compile(r"#\d{3,}\b|buildkite\.com/\S+/builds/\d+")
PUBLISH_FROM_MEMORY = "Warning, nothing was blocked: quote the RELEASED record or run `tools/ci release live`; never from memory"
RECORD_IDS = re.compile(r"\b[GRL]\d{3,4}\b|\b(?:answer|grant|ruling|note|doc|log) [0-9a-f]{7}\b|\b(?:ctx|msg)_\w+")
PLAIN_WORDS = (
    "Warning, nothing was blocked: Owner-facing text must read in plain words."
    " Replace each term below, or gloss it in the owner's own words, then post again."
)
REPLY_IN_PLAIN_WORDS = "Next reply to the owner: say each term below in plain words."
QUOTED_TERM = re.compile(r'"([^"\n]+)"')
SLACK_CARD = "mcp__plugin_cc-slack_cc-slack__slack_dm_status"
READ_FAILURES = (OSError, subprocess.SubprocessError, ValueError, LookupError)
CCI_TEXT = 400
SHOWN = frozenset({"title", "summary", "intro", "md", "prompt", "label", "hint", "value", "pros", "cons", "placeholder", "note", "body", "options"})
URL = re.compile(r"https?://\S+")
LINK = re.compile(r"<https?://[^|>\s]+(?:\|[^>\n]*)?>|\[[^\]\n]*\]\(https?://[^)\s]+\)")
PATH_CITE = re.compile(r"`?[\w./-]+:\d+(?:[-,]\d+)*`?\s*@\s*`?[0-9a-f]{7,40}\b`?")
QUOTE_CITE = re.compile(r"[\"”]\s*\([^)\n]*\)")
OWNER_QUOTE = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”")
IN_YOUR_WORDS = re.compile(r"^### In your words\b.*?(?=^#{1,3} |\Z)", re.MULTILINE | re.DOTALL)
IDS = (
    ("an internal id", RECORD_IDS),
    ("a plan row number", re.compile(r"\b(?:C|SI|E|X)-?\d+(?:\.\d+)*\b")),
    ("an answer id", re.compile(r"\b(?=[a-f]*\d)[0-9a-f]{7,40}\b")),
)
CODE = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)
PARENTHESIZED = re.compile(r"\([^()]*\)")
CAPTURES = "owner-prompts.jsonl"
OWNER_ANSWERS = ("answer", "list", "--label", "from:owner", "--limit", "0", "--json")
TOKEN = re.compile(r"[a-z][a-z'-]+")
NAMED = re.compile(r"[A-Z]|\w[- ]\w")
PLAIN_FOR = script_module("long_running.overview").PLAIN_FOR
CC_PRESENT = CommandSchema(
    "cc-present",
    operands=(Operand("verb"), Operand("words", count="*")),
    options=(
        *(Option(name, (f"--{name}",)) for name in ("after", "block", "blocks", "body", "cwd", "doc", "note", "root", "round", "round-title", "session", "slack", "title")),
        *(Option(name, (f"--{name}",), bool) for name in ("clear", "dry-run", "new")),
    ),
)
CC_SLACK = CommandSchema(
    "cc-slack",
    operands=(Operand("words", count="*"),),
    options=(
        *(Option(name, (f"--{name}",)) for name in ("agent", "blocks", "card", "cwd", "file", "session", "thread")),
        *(Option(name, (f"--{name}",), bool) for name in ("rebuild", "root-only")),
    ),
)
RULINGS = f"{sys.executable} {script('rulings.py')}"
PLAIN_READS = {f"{RULINGS} rows": ROWS, f"{RULINGS} glossary": GLOSSARY, "cci post": "#1\n", f"ccn -R {REPO} answer list": ANSWERS, **judged()}
PROGRAM_ACTIVE = [CompactionState(active=True, slug="brook")]
INCIDENT_WINDOW = timedelta(hours=6)
INCIDENT_STORE = [sys.executable, str(script("actions.py")), "list", "--json"]
INCIDENT_DIGEST = ["cci", "digest", "--json", "--since", "6h", "--budget", "4000000"]
INCIDENT_CLOSEOUT = ["cci", "grep", "--ignore-case=false", "--lane", "root", "--json", "-n", "1", "--since", "6h", "--", r"->\s\S+\s(re\s#[0-9]+\s)?(FIX-LIVE|Incident\sclosed)"]
SETTLED_INCIDENT = ("closed", "recovered")
INCIDENT_SOURCE_TIMEOUT_SECONDS = 10
PLAN_MODE_IN_INCIDENT = (
    "Warning, nothing was blocked: plan mode is forbidden while a customer-visible outage is open; the forward fix ships from the lane that holds it;"
    " replan after fix-live"
)
RECORD_SEQ = re.compile(r"#\d")
OFF_INCIDENT_ASK = "Warning, nothing was blocked: the alert is active: no owner question on other work; the incident's own ask names {names}"
ROOT_PROBE = "Warning, nothing was blocked: during an incident the root only spawns, routes, and acks: send this to a lane (desk-launch.py lane ...)"
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
OUTAGE_STOP_WORDS = "on a second outage, stop patching, re-enter plan mode and find the root fix"
CAUSE_RECORD = Path(__file__).parent / "tests" / "fixtures" / "root-cause" / "autoscaler.txt"
NO_PLAN_BEFORE_FIX = "no plan mode until the incident is fixed"
PLAN_WHEN_DONE = "confirm each incident item is finished, then go into plan mode only when all of it is done"
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n")
PLAN_MODE_FORBIDDEN = (
    "Warning, nothing was blocked: The owner's latest word on plan mode forbids it now. Ship the fix from the lane that holds it, and enter plan mode only when the owner orders it."
)
CAUSE = re.compile(r"\b(?:because|caus(?:e|ed|es|ing)|due to|mechanism|is why|was why|led to|triggered by|results? from)\b", re.IGNORECASE)
CAUSE_KINDS = {"go", "decision", "decide", "note", "report", "state", "incident"}
RECORD_REF = re.compile(r"#(\d{3,})\b")
UNQUOTED_CAUSE = (
    "Warning, nothing was blocked: this root record names a cause and quotes no lane record."
    " Quote the lane `MECHANISM` record's `#<seq>`, or send the question to a lane with `desk-launch.py lane`."
)
RELEASE_PROBES = (("release", "start"), ("release", "status"), ("release", "watch"))
INBOX_FILE = re.compile(r"/inbox/[\w.-]+\.md\b")
AUTHORIZING_LINE = re.compile(r"\b(?:GO|LIFT|UNBLOCK) root \(.*")
AUTHORIZING_KINDS = {"go", "lift", "unblock"}
PR_REF = re.compile(r"#\d{3,}")
PR_TAIL = re.compile(r"\d{3,}$")
ROUTING = re.compile(r"(?i)\brout(?:e|es|ed|ing)\b|\bcluster (?:assignment|move)\b|\bcutover\b")
BOARD_VERBS = ("push", "start", "update-block", "reply")
BOARD_DOCS = ("push", "start", "update-block")
STDIN = "-"
AUTHORIZED_BESIDE_A_QUESTION = (
    "Warning, nothing was blocked: A GO, LIFT, or UNBLOCK line or `cci post` record is never written while an owner question on its subject is unanswered."
    " Wait for the answer, then write it in a later turn."
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
    tests=separate("owner_facing.route_incident_fix_lanes_to_sol", {
        spawn("pr-review-pipeline-fix", "CI incident, effort high.", commands={REFUSED: ""}): Warn(
            pattern=r"desk-launch\.py incident <name> <fix-brief>[\s\S]*desk-launch\.py refused <name>-fix"
        ),
        spawn("incident-api-1n80-fix", "Fix it.", commands={REFUSED: ""}): Warn(),
        spawn("lane-failing-fix", INCIDENT_TURN, commands={REFUSED: ""}): Warn(),
        spawn("api-1n80-fix", "ccx: role=fix\nFix the outage on api.", commands={REFUSED: ""}): Warn(),
        spawn("api-1n80-fix", "ccx: role=fix\nFix the outage on api.", commands={REFUSED: LOAD_REFUSAL}): Allow(),
        spawn("lane-failing-fix", INCIDENT_TURN, commands={REFUSED: LOAD_REFUSAL}): Allow(),
        spawn("lane-failing-fix", f"ccx: incident=none\n{INCIDENT_TURN}"): Allow(),
        spawn("flappy-monitors-2", NOT_AN_INCIDENT): Allow(),
        spawn("ignore-protect-preview", INCIDENT_ADJACENT): Allow(),
        spawn("incident-runner", "Lane incident-runner: a durable incident executor.", commands={REFUSED: ""}): Warn(),
        spawn("incident-runner", "ccx: role=tooling tooling-lane=incident-runner\nLane incident-runner."): Allow(),
        spawn("incident-api-1n80-evidence", "ccx: role=evidence\nEvidence lane for the incident."): Allow(),
        spawn("ledger-fix", "ccx: role=fix\nFix the ledger."): Allow(),
        spawn("slack-inline-why", "Owner correction.\nThe incident lane..."): Allow(),
        Input(tool="Agent", tool_input={"name": "incident-api-1n80-fix", "prompt": "Fix it."}): Allow(),
        spawn("incident-api-1n80-fix", "Fix it.", agent_id="a1b2c3"): Allow(),
    }),
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
    return evt.context(INCIDENT_ROUTE)


OUTAGE_READ = "ccx: role=reader\nRead the outage thread the owner linked into a file."
FIX_LAUNCH = "python3 /drive/scripts/desk-launch.py incident api-outage-fix /drive/briefs/api-outage-fix.md"
ORCA_GO = "cci post --drive d1 --lane root --kind go --to orca-desk --text 'R1 (10:31 AM) orca-desk: launch api-outage-fix NOW incident xhigh role=fix brief=/b.md'"


def read_beside(name: str, prompt: str, *siblings: object, earlier: tuple[object, ...] = (), **extra: object) -> Input:
    turn = [T.user("fix the outage the bot posted"), T.assistant(T.tool("Agent", name=name, prompt=prompt), *siblings)]
    history = [T.user("start the relay"), T.assistant(*earlier)] if earlier else []
    return Input(tool="Agent", tool_input={"name": name, "prompt": prompt}, **({"state": ACTIVE, "transcript": [*history, *turn]} | extra))


@on(
    Event.PostToolUse,
    only_if=[Tool("Agent")],
    skip_if=[FromSubagent()],
    tests=separate("owner_facing.launch_the_fix_beside_the_reader", {
        read_beside("outage-reader", OUTAGE_READ): Warn(pattern=r"same turn\. Launch the fix now with `desk-launch\.py incident <name> <fix-brief>`"),
        read_beside("slack-read-outage-1", "Read the outage thread."): Warn(),
        read_beside("outage-reader", OUTAGE_READ, earlier=(T.tool("Bash", command=FIX_LAUNCH),)): Warn(),
        read_beside("outage-reader", OUTAGE_READ, T.tool("Bash", command=FIX_LAUNCH)): Allow(),
        read_beside("outage-reader", OUTAGE_READ, T.tool("Bash", command=ORCA_GO)): Allow(),
        read_beside("outage-reader", OUTAGE_READ, T.tool("Bash", command="orca-launch.sh api-outage-fix incident xhigh /b.md")): Allow(),
        read_beside("outage-reader", OUTAGE_READ, T.tool("Agent", name="api-outage-fix", prompt="Fix it.")): Allow(),
        read_beside("outage-reader", OUTAGE_READ, T.tool("Agent", name="api-patch", prompt="ccx: role=fix\nFix it.")): Allow(),
        read_beside("dm-reader", "ccx: role=reader\nRead the owner's DM about lane names."): Allow(),
        read_beside("slack-read-1793163121", "Read the thread the owner linked."): Allow(),
        read_beside("outage-reader", OUTAGE_READ, state=[]): Allow(),
        read_beside("outage-reader", OUTAGE_READ, agent_id="a1b2c3"): Allow(),
    }),
)
def launch_the_fix_beside_the_reader(evt: BaseHookEvent) -> HookResult | None:
    call = evt.as_input(TaskCall)
    if call is None or not CompactionState.load(evt).active or not reads_an_alert(evt, call) or launched_a_fix(evt):
        return None
    return evt.context(FIX_BESIDE_READER)


def reads_an_alert(evt: BaseHookEvent, call: TaskCall) -> bool:
    role = evt.annotations.get("role")
    reader = role == READER_ROLE or (role is None and bool(READER_NAME.match(call.agent_name or "")))
    return reader and any(ALERT_TALK.search(text) for text in (call.agent_name or "", *prose(call)))


def launched_a_fix(evt: BaseHookEvent) -> bool:
    turn = evt.ctx.turn.tool_calls
    agents = (use.call for use in turn.named("Agent").items)
    commands = (cmd.argv for use in turn.named("Bash").items for cmd in use.call.command_line if cmd.argv)
    return any(fix_agent(call) for call in agents) or any(fix_command(argv) for argv in commands)


def fix_agent(call: TaskCall) -> bool:
    return (call.agent_name or "").endswith("-fix") or dict(dispatch_pairs(call.raw.get("prompt") or "")).get("role") == FIX_ROLE


def fix_command(argv: list[str]) -> bool:
    names = [Path(word).name for word in argv]
    after = dict(zip(names, argv[1:]))
    if after.get("desk-launch.py") == "incident" or (names[0] == "codex-ask" and "--incident" in argv):
        return True
    if "orca-launch.sh" in names and len(argv) > (at := names.index("orca-launch.sh")) + 2 and argv[at + 2] == "incident":
        return True
    return names[0] == "cci" and "post" in argv and any("orca-desk: launch" in word and " incident " in word for word in argv)


def read_incident_source(evt: BaseHookEvent, argv: list[str]) -> str:
    env = dict(reqenv.env_map()) | {"CLAUDE_CODE_SESSION_ID": evt.session_id}
    done = subprocess.run(argv, capture_output=True, text=True, timeout=INCIDENT_SOURCE_TIMEOUT_SECONDS, env=env)
    done.check_returncode()
    return done.stdout


def incident_source(evt: BaseHookEvent, argv: list[str]) -> str:
    try:
        return read_incident_source(evt, argv)
    except (OSError, subprocess.SubprocessError):
        return ""


def stored_incidents(evt: BaseHookEvent, source: Callable[[BaseHookEvent, list[str]], str] = incident_source) -> list[dict]:
    since = datetime.now(UTC) - INCIDENT_WINDOW
    return [
        record
        for record in json.loads(source(evt, INCIDENT_STORE) or "[]")
        if record["status"] not in SETTLED_INCIDENT and not record["facts"].get("not_ours") and datetime.fromisoformat(record["opened_at"]) >= since
    ]


def declared_fixes(evt: BaseHookEvent) -> list[str]:
    return [str(record["facts"]["repair_pr"]) for record in stored_incidents(evt) if record["facts"].get("repair_pr")]


def open_incidents(evt: BaseHookEvent, source: Callable[[BaseHookEvent, list[str]], str] = incident_source) -> list[tuple[str, ...]]:
    stored = [(record["incident_id"], record["facts"].get("target"), record["facts"].get("alert")) for record in stored_incidents(evt, source)]
    closed_at = max((json.loads(line)["seq"] for line in (source(evt, INCIDENT_CLOSEOUT) or "").splitlines() if line.strip()), default=0)
    posted = [
        (f"#{record['seq']}", record.get("topic"), record.get("id"), *((record.get("refs") or {}).get("targets") or []))
        for record in json.loads(source(evt, INCIDENT_DIGEST) or "{}").get("open_incidents") or []
        if record["seq"] > closed_at
    ]
    return [tuple(name for name in names if name) for names in stored + posted]


def incident_sources(*stored: dict, posted: tuple[dict, ...] = (), closeout: tuple[dict, ...] = ()) -> dict[str, str]:
    return {
        " ".join(INCIDENT_STORE): json.dumps(list(stored)),
        " ".join(INCIDENT_DIGEST): json.dumps({"open_incidents": list(posted)}),
        " ".join(INCIDENT_CLOSEOUT): "\n".join(json.dumps(record) for record in closeout),
    }


def plan_mode(tool: str, *stored: dict, posted: tuple[dict, ...] = (), **extra: object) -> Input:
    return Input(tool=tool, tool_input={}, state=ACTIVE, commands=incident_sources(*stored, posted=posted), **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("EnterPlanMode", "ExitPlanMode")],
    skip_if=[FromSubagent()],
    tests=separate("owner_facing.no_plan_mode_in_an_open_incident", {
        plan_mode("EnterPlanMode", OUTAGE_0820): Warn(pattern="forbidden while a customer-visible outage is open"),
        plan_mode("ExitPlanMode", OUTAGE_0820): Warn(pattern="forbidden while a customer-visible outage is open"),
        plan_mode("EnterPlanMode", posted=(OUTAGE_TWO,)): Warn(pattern="replan after fix-live"),
        plan_mode("EnterPlanMode", CLOSED_0820): Allow(),
        plan_mode("EnterPlanMode", RECOVERED_0820): Allow(),
        plan_mode("EnterPlanMode", STALE_0820): Allow(),
        Input(tool="EnterPlanMode", tool_input={}, commands=incident_sources(OUTAGE_0820)): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, agent_id="a1b2c3"): Allow(),
        plan_mode("EnterPlanMode", CLOSED_0820, transcript=[T.user("dont enter plan mode")]): Warn(pattern="latest word on plan mode forbids it"),
        plan_mode("EnterPlanMode", CLOSED_0820, transcript=[T.user("no plan mode today")]): Warn(),
        plan_mode("EnterPlanMode", CLOSED_0820, transcript=[T.user("do not enter plan mode until I ask")]): Warn(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user("don't replan")]): Warn(pattern="latest word on plan mode forbids it"),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user("enter plan mode first")]): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user("dont enter plan mode yet"), T.user("ok now re-enter plan mode and replan")]): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user("no, go into plan mode")]): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user("dont fix anything else, enter plan mode now")]): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user(OUTAGE_STOP_WORDS)]): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user(NO_PLAN_BEFORE_FIX)]): Warn(pattern="replan after fix-live"),
        plan_mode("EnterPlanMode", CLOSED_0820, transcript=[T.user(NO_PLAN_BEFORE_FIX)]): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user(PLAN_WHEN_DONE)]): Warn(pattern="replan after fix-live"),
        plan_mode("EnterPlanMode", CLOSED_0820, transcript=[T.user(PLAN_WHEN_DONE)]): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user("ship the fix")]): Warn(pattern="replan after fix-live"),
        plan_mode("EnterPlanMode", CLOSED_0820, transcript=[T.user("ship the fix")]): Allow(),
        plan_mode("ExitPlanMode", CLOSED_0820, transcript=[T.user("dont enter plan mode")]): Allow(),
        plan_mode("ExitPlanMode", OUTAGE_0820, transcript=[T.user("no plan mode today")]): Allow(),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user("why did you enter plan mode?")]): Warn(pattern="replan after fix-live"),
        plan_mode("EnterPlanMode", OUTAGE_0820, transcript=[T.user("enter plan mode first"), T.user("why did you enter plan mode?")]): Allow(),
    }),
)
def no_plan_mode_in_an_open_incident(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    word = latest_plan_word(evt) or ""
    if not PLAN_CONDITIONAL.search(word) and PLAN_FORBIDDEN.search(word):
        return None if evt.tool_name == "ExitPlanMode" else evt.context(PLAN_MODE_FORBIDDEN)
    if not PLAN_CONDITIONAL.search(word) and PLAN_ORDERED.search(word):
        return None
    return evt.context(PLAN_MODE_IN_INCIDENT) if open_incidents(evt) else None


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


def statements(text: str) -> list[str]:
    return [part for part in SENTENCE_BREAK.split(text) if part.strip() and not part.rstrip().endswith("?")]


def latest_plan_word(evt: BaseHookEvent) -> str | None:
    return next((" ".join(said) for text in reversed(owner_words(evt)) if (said := [part for part in statements(text) if PLAN_TALK.search(part)])), None)


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
    tests=separate("owner_facing.nudge_utc_in_owner_replies", {
        Input(transcript=reply_transcript("Fix lane spawned at 17:35Z."), state=ACTIVE): Allow(),
        Input(transcript=reply_transcript("Fix lane spawned at 10:35am."), state=ACTIVE): Allow(),
        Input(transcript=reply_transcript("Lane at 17:35Z.", sidechain=True), state=ACTIVE): Allow(),
        Input(transcript=reply_transcript("Fix lane spawned at 17:35Z.")): Allow(),
        Input(transcript=reply_transcript("Fix lane spawned at 17:35Z."), agent_id="a1b2c3", state=ACTIVE): Allow(),
    }),
)
def nudge_utc_in_owner_replies(evt: BaseHookEvent) -> HookResult | None:
    if CompactionState.load(evt).active and UTC_CLOCK.search(last_reply(evt)) and once_per_hour(evt, [UTC_IN_REPLY]):
        queue_nudge(evt, UTC_IN_REPLY)
    return None


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests=separate("owner_facing.publish_lines_quote_the_released_record", {
        Input(transcript=reply_transcript("sand 1437 is published."), state=ACTIVE, commands=incident_sources(OUTAGE_0820)): Warn(
            pattern=r"quote the RELEASED record"
        ),
        Input(transcript=reply_transcript("sand 1437 is published (#103935)."), state=ACTIVE, commands=incident_sources(OUTAGE_0820)): Allow(),
        Input(
            transcript=reply_transcript("sand 1437 is live: https://buildkite.com/forge/release/builds/8748"),
            state=ACTIVE,
            commands=incident_sources(OUTAGE_0820),
        ): Allow(),
        Input(transcript=reply_transcript("sand 1437 is published."), state=ACTIVE, commands=incident_sources()): Allow(),
        Input(
            transcript=[T.user("is it out?"), T.assistant(T.tool("Bash", command="tools/ci release live sand")), T.assistant("sand 1437 is live.")],
            state=ACTIVE,
            commands=incident_sources(OUTAGE_0820),
        ): Allow(),
        Input(
            transcript=[T.user("is it out?"), T.assistant(T.tool("Bash", command="echo tools/ci release live sand")), T.assistant("sand 1437 is live.")],
            state=ACTIVE,
            commands=incident_sources(OUTAGE_0820),
        ): Warn(),
        Input(transcript=reply_transcript("The fix lane is building the PR."), state=ACTIVE, commands=incident_sources(OUTAGE_0820)): Allow(),
        Input(transcript=reply_transcript("The release plan is rewritten and now live on the board."), state=ACTIVE, commands=incident_sources(OUTAGE_0820)): Allow(),
        Input(transcript=reply_transcript("sand has not been cut yet; I will cut it next."), state=ACTIVE, commands=incident_sources(OUTAGE_0820)): Allow(),
        Input(transcript=reply_transcript("sand 1437 is published."), commands=incident_sources(OUTAGE_0820)): Allow(),
    }),
)
def publish_lines_quote_the_released_record(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    reply = last_reply(evt)
    claims = any(PUBLISH_CLAIM.search(sentence) and RELEASE_NUMBER.search(sentence) for sentence in SENTENCE_BREAK.split(reply))
    if claims and not PUBLISH_PROOF.search(reply) and not read_release_live(evt) and open_incidents(evt):
        return evt.context(PUBLISH_FROM_MEMORY)
    return None


def read_release_live(evt: BaseHookEvent) -> bool:
    return any(
        Path(cmd.argv[0]).name == "ci" and tuple(cmd.argv[1:3]) == ("release", "live")
        for use in evt.ctx.turn.tool_calls.named("Bash").items
        for cmd in use.call.command_line
        if cmd.argv
    )


def question_text(evt: BaseHookEvent) -> str:
    questions = evt._tool_input.get("questions") or []
    options = [option for question in questions for option in question.get("options") or []]
    return "\n".join(
        [question.get("question", "") for question in questions]
        + [f"{option.get('label', '')} {option.get('description', '')} {option.get('preview', '')}" for option in options]
    )


def ask(question: str, *, incidents: tuple[dict, ...] = (), posted: tuple[dict, ...] = (), **option: str) -> Input:
    questions = [{"question": question, "header": "Ask", "options": [{"label": "Go", **option}, {"label": "Hold"}], "multiSelect": False}]
    return Input(
        tool="AskUserQuestion",
        tool_input={"questions": questions},
        state=ACTIVE,
        commands=incident_sources(*incidents, posted=posted),
    )


def incident_label(incidents: list[tuple[str, ...]]) -> str:
    readable = [next((name for name in names if not RECORD_SEQ.search(name)), "") for names in incidents]
    return ", ".join(name for name in readable if name) or "its target, topic, or record number"


def names_an_incident(text: str, names: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) for name in names)


def shown(value: object, where: str = "document", key: str = "") -> Iterator[tuple[str, str]]:
    match value:
        case str() if key in SHOWN:
            yield where, value
        case dict():
            here = value["id"] if isinstance(value.get("type"), str) and isinstance(value.get("id"), str) else where
            for name, item in value.items():
                yield from shown(item, here, name)
        case list():
            for item in value:
                yield from shown(item, where, key)


def readable(text: str) -> str:
    text = IN_YOUR_WORDS.sub(lambda block: OWNER_QUOTE.sub(" ", block[0]), text)
    for exempt in (LINK, URL, PATH_CITE, QUOTE_CITE):
        text = exempt.sub(" ", text)
    return text


def squeeze(text: str) -> str:
    return " ".join(text.split()).strip("\"“”' ").rstrip(".")


def unquoted(text: str, said: list[str]) -> str:
    return OWNER_QUOTE.sub(lambda quote: " " if (words := squeeze(quote[0])) and any(words in source for source in said) else quote[0], text)


def outside_cites(text: str) -> str:
    seen = CODE.sub(" ", OWNER_QUOTE.sub(" ", readable(text)))
    while (stripped := PARENTHESIZED.sub(" ", seen)) != seen:
        seen = stripped
    return seen


def plan_words(text: str, reads: Reads) -> list[str]:
    return judged_plain(reads, readable(text), outside_cites(text))


def reason(failure: BaseException) -> str:
    if isinstance(failure, subprocess.CalledProcessError):
        last = str(failure.stderr or "").strip().splitlines()[-1:]
        return f"exit {failure.returncode}" + "".join(f": {line}" for line in last)
    return str(failure) or type(failure).__name__


def fenced(rule: str, problems: list[str]) -> str:
    return f"{rule}\n\n```\n" + "\n".join(problems) + "\n```"


@dataclass
class Reads:
    evt: BaseHookEvent
    which: list[str]
    failures: list[str] = field(default_factory=list)
    loaded: dict[str, object] = field(default_factory=dict)

    @cached_property
    def entry(self) -> dict:
        return drive_entry(self.evt) or {}

    @property
    def repo(self) -> str:
        return self.entry.get("checkout") or str(self.evt.cwd)

    def read[V](self, what: str, load: Callable[[], V]) -> V | None:
        if what not in self.loaded:
            try:
                self.loaded[what] = load()
            except READ_FAILURES as failure:
                self.failures.append(f"{what}: {reason(failure)}")
                self.loaded[what] = None
        return self.loaded[what]

    def register(self) -> dict | None:
        return self.read("rulings.py rows", lambda: json.loads(rulings(self.repo, "rows", *self.which, "--json", evt=self.evt)))

    def glossary(self) -> str | None:
        return self.read("rulings.py glossary", lambda: rulings(self.repo, "glossary", *self.which, evt=self.evt))

    def scopes(self) -> list[tuple[list[str], str]] | None:
        if (register := self.register()) is None:
            return None
        rows = [row for row in register["rows"].values() if row.get("kind") == "scope"]
        return self.read("the scope rows", lambda: [(json.loads(row.get("names") or "[]"), row.get("words", "")) for row in rows])

    def said(self) -> list[str] | None:
        if (register := self.register()) is None:
            return None
        path = Path(register["state_dir"]) / CAPTURES

        def load() -> list[str]:
            captured = [json.loads(line)["text"] for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
            repeats = [repeat.get("words") or "" for row in register["rows"].values() for repeat in json.loads(row.get("repeats") or "[]")]
            listed = ccn(self.repo, *OWNER_ANSWERS)
            listed.check_returncode()
            answered = [quote for answer in json.loads(listed.stdout) for quote in OWNER_QUOTE.findall(answer["body"])]
            return [squeeze(text) for text in [*captured, *(row.get("words", "") for row in register["rows"].values()), *repeats, *answered]]

        return self.read("the owner's captured words", load)

    def report(self, hook: str) -> None:
        if self.failures:
            text = f"{hook}: {'; '.join(self.failures)}"[:CCI_TEXT]
            post_blocker(self.evt, text, lane=lane_name(self.evt), key=f"{hook}:{hashlib.sha256(text.encode()).hexdigest()[:12]}")


def judged_plain(reads: Reads, text: str, picked: str) -> list[str]:
    if not text.strip() or (glossary := reads.glossary()) is None or (scopes := reads.scopes()) is None:
        return []
    judge, env = script_module("long_running/judge"), reqenv.env_map()
    lowered = text.lower()
    tokens = set(TOKEN.findall(lowered))
    names = dict.fromkeys(line.strip() for line in glossary.splitlines() if NAMED.search(line.strip()))
    known = [name for name in names if name.lower() not in PLAIN_FOR and (name.lower() in tokens or " " in name and name.lower() in lowered)]
    ruled = [(names, words) for names, words in scopes if any(name.lower() in lowered for name in names)]
    ids = list(dict.fromkeys(hit[0] for _, pattern in IDS for hit in pattern.finditer(picked)))
    verdict = judge_call(
        judge,
        "plain",
        judge.reader(known, ruled, PLAIN_FOR, ids),
        text,
        backend=judge.backend_from(env),
        drive=reads.which[1],
        timeout=reqenv.clamp_timeout(judge.JUDGE_SECONDS),
        env=env,
    )
    return [f'plain words, "{verdict.artifact_phrase}": {verdict.sentence}'] if verdict is not None and verdict.flagged else []


def term_of(problem: str) -> str:
    return (found[1] if (found := QUOTED_TERM.search(problem)) else problem).casefold()


def unsaid(evt: BaseHookEvent, problems: list[str]) -> list[str]:
    due = set(once_per_hour(evt, map(term_of, problems)))
    return [problem for problem in problems if term_of(problem) in due]


def plain_words(texts: list[str], reads: Reads) -> list[str]:
    text = "\n\n".join(readable(text) for text in texts if text.strip())
    return judged_plain(reads, text, text)


def piped_boards(evt: BaseHookEvent) -> list[str]:
    statements = [statement.text for statement in ast_grep.find_kinds(evt.command.raw, "bash", frozenset({"redirected_statement"}))]
    named = [text for text in statements if [name.text for name in ast_grep.find_kinds(text, "bash", frozenset({"command_name"}))][:1] == ["cc-present"]]
    bodies = [body for text in named if (body := next(ast_grep.find_kinds(text, "bash", frozenset({"heredoc_body"})), None))]
    return [body.text if body.text.endswith("\n") else f"{body.text}\n" for body in bodies]


def board_bytes(evt: BaseHookEvent, call: Call, source: str, piped: int) -> bytes:
    if source != STDIN:
        return Path(str(call.cwd or evt.cwd), Path(source).expanduser()).read_bytes()
    if feeder := pipe_feeder(evt, call):
        return fed_board(evt, feeder)
    return piped_boards(evt)[piped].encode()


def pipe_feeder(evt: BaseHookEvent, call: Call) -> Call | None:
    return next((each for each in evt.command.calls() if each.occurrence.index == call.occurrence.index - 1 and each.occurrence.next_op == "|"), None)


def fed_board(evt: BaseHookEvent, feeder: Call) -> bytes:
    """The board an `echo '<json>' |` or `cat <file> |` feeds to cc-present, as the shell would; any other producer runs only after the hook, so its board cannot be read."""
    match [word.value for word in feeder.source.words]:
        case ["echo", str(text)]:
            return f"{text}\n".encode()
        case ["cat", str(path)]:
            return Path(str(feeder.cwd or evt.cwd), Path(path).expanduser()).read_bytes()
        case [name, *_]:
            raise ValueError(f"a board piped from {name} exists only once the command runs")
    raise ValueError("a board piped from an empty command")


def board_sources(evt: BaseHookEvent) -> Iterator[tuple[Call, dict[str, tuple], str | None, int]]:
    piped = 0
    for call in evt.command.calls("cc-present"):
        values = CC_PRESENT.bind(call).values
        verb = next(iter(values.get("verb", ())), None)
        source = next(iter(values.get("doc" if verb == "start" else "words", ())), None) if verb in BOARD_DOCS else None
        yield call, values, source, piped
        piped += source == STDIN and pipe_feeder(evt, call) is None


def slack_card(evt: BaseHookEvent) -> dict | None:
    if evt.tool_name == SLACK_CARD:
        return evt._tool_input.get("card")
    for call in evt.command.calls("cc-slack"):
        values = CC_SLACK.bind(call).values
        if values.get("words", ())[:1] == ("dm-status",) and (card := next(iter(values.get("card", ())), None)):
            found = Path(str(call.cwd or evt.cwd), Path(card[1:]).expanduser()).read_text() if card.startswith("@") else card
            return json.loads(found)
    return None


def started_boards(evt: BaseHookEvent, reads: Reads) -> list[tuple[str, str]]:
    docs = [
        reads.read(f"the started board {source} {piped}", lambda call=call, source=source, piped=piped: json.loads(board_bytes(evt, call, source, piped)))
        for call, values, source, piped in board_sources(evt)
        if values.get("verb", ())[:1] == ("start",) and not values.get("dry-run") and source
    ]
    return [pair for doc in docs if doc for pair in shown(doc)]


def owner_strings(evt: BaseHookEvent, reads: Reads) -> list[tuple[str, str]]:
    if not (card := reads.read("the status card", lambda: slack_card(evt))):
        return started_boards(evt, reads)
    said = reads.said() or []
    options = [("card option", option) for option in card.get("options") or []]
    facts = [("card fact", f"{fact.get('label') or ''}: {fact.get('value') or ''}") for fact in card.get("facts") or []]
    fields = [("card title", card.get("title") or ""), ("card body", card.get("body") or ""), *options, *facts, ("card phase", card.get("phase") or "")]
    return [(where, unquoted(text, said)) for where, text in fields if text]


def status_card(phrase: str = "", **card: object) -> Input:
    return Input(tool=SLACK_CARD, tool_input={"card": card}, state=PROGRAM_ACTIVE, env=DRIVE_CONFIG, commands={**PLAIN_READS, **judged(phrase)})


def drive_ask(question: str, phrase: str = "", **option: str) -> Input:
    asked = ask(question, **option)
    return Input(tool="AskUserQuestion", tool_input=asked.tool_input, state=PROGRAM_ACTIVE, env=DRIVE_CONFIG, commands={**asked.commands, **PLAIN_READS, **judged(phrase)})


def off_incident_question(evt: BaseHookEvent) -> HookResult | None:
    if (incidents := open_incidents(evt)) and not any(names_an_incident(question_text(evt), names) for names in incidents):
        return evt.context(OFF_INCIDENT_ASK.format(names=incident_label(incidents)))
    return None


@on(
    Event.PreToolUse,
    only_if=[Tool("AskUserQuestion", "Bash", SLACK_CARD)],
    skip_if=[FromSubagent()],
    tests=separate("owner_facing.plain_words_in_owner_questions", {
        drive_ask("D gate: the plan said dry-run before enqueue. Which gate?", "D gate"): Warn(pattern=r'^Warning, nothing was blocked: Owner-facing text must read in plain words[\s\S]*plain words, "D gate"'),
        drive_ask("Lift the hold?", "G304", description="walker walks the reverted HEAD after G304 lifts"): Warn(pattern=r'plain words, "G304"'),
        drive_ask('Proposed reply to Andrew in #platform-squad: "No design conflict." OK to post?', "Proposed reply to Andrew"): Warn(pattern=r'plain words, "Proposed reply to Andrew"'),
        drive_ask("Post the Slack lane's reply to Andrew?", preview="Thanks, no design conflict with anything in flight."): Allow(),
        Input(
            tool="AskUserQuestion",
            tool_input=ask("Lift the hold?").tool_input,
            state=PROGRAM_ACTIVE,
            env=DRIVE_CONFIG,
            commands={**ask("Lift the hold?").commands, **PLAIN_READS, JUDGE: (1, "claude: overloaded")},
        ): Allow(),
        ask("D gate: the plan said dry-run before enqueue. Which gate?"): Allow(),
        Input(tool="AskUserQuestion", tool_input={"questions": [{"question": "D gate?", "options": []}]}, agent_id="a1b2c3", state=ACTIVE): Allow(),
        ask("Should I extend the backlog deadline to 11:30?", incidents=(SAND_NOT_MIGRATED,)): Warn(pattern=r"the alert is active[\s\S]*sand-not-migrated"),
        ask("sand-not-migrated: approve the 0-delete apply on plat?", incidents=(SAND_NOT_MIGRATED,)): Allow(),
        ask("Approve the 0-delete sand apply on plat?", incidents=(SAND_NOT_MIGRATED,)): Allow(),
        ask("Should I extend the backlog deadline to 11:30?", incidents=(SAND_NOT_MIGRATED | {"status": "closed"},)): Allow(),
        ask("Should I extend the backlog deadline to 11:30?", posted=(OUTAGE_TWO,)): Warn(pattern=r"the alert is active[\s\S]*outage-two"),
        ask("Roll the executor back for outage two (#102819)?", posted=(OUTAGE_TWO,)): Allow(),
        ask("Should I extend the thousand-row backlog?", incidents=(SAND_NOT_MIGRATED,)): Warn(pattern=r"the alert is active"),
        ask("Should I extend the backlog deadline to noon?", posted=(BARE_INCIDENT,)): Warn(pattern=r"names its target, topic, or record number"),
        ask("Hand-publish sand for #103958?", posted=(BARE_INCIDENT,)): Allow(),
        status_card("slice", title="Merge the slice for lane p1a?", body="Its checks passed.", options=["Merge", "Hold"]): Warn(pattern=r'plain words, "slice"'),
        status_card(title="Merge the chat-list fix?", body="Its checks passed.", options=["Merge #42301", "Hold"]): Allow(),
        Input(
            command="""cc-slack dm-status --card '{"title": "Ship the sidecar change?", "body": "Ready."}'""",
            state=PROGRAM_ACTIVE,
            env=DRIVE_CONFIG,
            commands={**PLAIN_READS, **judged("sidecar")},
        ): Warn(pattern=r'plain words, "sidecar"'),
        Input(
            command=f"cc-present start --session s1 --doc {board('r3-spikes-queue-v1.json')}",
            state=PROGRAM_ACTIVE,
            env=DRIVE_CONFIG,
            commands={**PLAIN_READS, **judged("lane")},
        ): Warn(pattern=r'plain words, "lane"'),
        Input(command=f"cc-present start --session s1 --doc {board('clean-card.json')}", state=PROGRAM_ACTIVE, env=DRIVE_CONFIG, commands=PLAIN_READS): Allow(),
        Input(
            command=f"cc-present start --session s1 --doc {board('r3-spikes-queue-v1.json')} --dry-run",
            state=PROGRAM_ACTIVE,
            env=DRIVE_CONFIG,
            commands={**PLAIN_READS, **judged("lane")},
        ): Allow(),
        Input(
            tool=SLACK_CARD,
            tool_input={"card": {"title": "Merge the chat-list fix?", "body": "Its checks passed.", "options": ["Merge"]}},
            state=PROGRAM_ACTIVE,
            env=DRIVE_CONFIG,
            commands={f"{RULINGS} rows": "no register for brook", f"{RULINGS} glossary": GLOSSARY, "cci post": "#1\n", **judged("chat-list")},
        ): Allow(),
        Input(command="ls", state=PROGRAM_ACTIVE, env=DRIVE_CONFIG, commands=PLAIN_READS): Allow(),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Merge the slice?"}}, commands={**PLAIN_READS, **judged("slice")}): Allow(),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Merge the slice?"}}, agent_id="a1b2c3", state=PROGRAM_ACTIVE, commands={**PLAIN_READS, **judged("slice")}): Allow(),
    }),
)
def plain_words_in_owner_questions(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    if evt.tool_name == "AskUserQuestion" and (warned := off_incident_question(evt)):
        return warned
    if not (which := drive_args(evt)):
        return None
    reads = Reads(evt, which)
    texts = [question_text(evt)] if evt.tool_name == "AskUserQuestion" else [text for _, text in owner_strings(evt, reads)]
    problems = plain_words(texts, reads)
    reads.report("plain_words_in_owner_questions")
    if reads.failures or not (problems := unsaid(evt, problems)):
        return None
    return evt.context(fenced(PLAIN_WORDS, problems))


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests=separate("owner_facing.plain_words_on_reply", {
        Input(transcript=reply_transcript("The slice for lane p1a is done."), state=PROGRAM_ACTIVE, env=DRIVE_CONFIG, commands={**PLAIN_READS, **judged("slice")}): Allow(),
        Input(transcript=reply_transcript("The chat-list fix is live for every team."), state=PROGRAM_ACTIVE, env=DRIVE_CONFIG, commands=PLAIN_READS): Allow(),
        Input(transcript=reply_transcript("The slice for lane p1a is done."), commands=PLAIN_READS): Allow(),
        Input(transcript=reply_transcript("The slice for lane p1a is done."), agent_id="a1b2c3", state=PROGRAM_ACTIVE, commands=PLAIN_READS): Allow(),
    }),
)
def plain_words_on_reply(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active or not (which := drive_args(evt)) or not (reply := last_reply(evt)):
        return None
    reads = Reads(evt, which)
    if problems := unsaid(evt, plain_words([reply], reads)):
        queue_nudge(evt, fenced(REPLY_IN_PLAIN_WORDS, problems))
    reads.report("plain_words_on_reply")
    return None


def scratch(value: str) -> bool:
    path = Path.home() / value[2:] if value.startswith("~/") else Path(value)
    roots = (Path.home() / ".claude", Path("/tmp"), Path("/private/tmp"))
    return path.is_absolute() and not path.is_relative_to(Path.home() / ".claude" / "worktrees") and any(path.is_relative_to(root) for root in roots)


def product_read(call: Call, calls: list[Call]) -> bool:
    searches = call.name == "rg" or call.verb_argv[:2] == ("git", "show") or (call.name == "ccx" and call.args[:1] == ("code",))
    if not searches:
        return False
    places = [target.value for target in operand_targets(call) if target.value and "/" in target.value]
    if call.name == "rg" and reads_stdin(call, calls) and not places:
        return False
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
    tests=separate("owner_facing.root_only_routes_during_an_incident", {
        probe("kubectl -n plat get deploy api", SAND_NOT_MIGRATED): Warn(pattern="only spawns, routes, and acks"),
        probe("curl -s https://api.poetic.com/health", SAND_NOT_MIGRATED): Warn(pattern="only spawns, routes, and acks"),
        probe("pup monitors list", SAND_NOT_MIGRATED): Warn(),
        probe("tools/ci release start --emergency sand", SAND_NOT_MIGRATED): Warn(),
        probe("./tools/ci release watch 2026-10-20/31", SAND_NOT_MIGRATED): Warn(),
        probe("/opt/homebrew/bin/kubectl get pods", SAND_NOT_MIGRATED): Warn(),
        probe("tools/ci release plan --from 94d61b9", SAND_NOT_MIGRATED): Allow(),
        probe("kubectl -n plat get deploy api", SAND_NOT_MIGRATED, agent_id="aevidence-0123456789abcdef"): Allow(),
        probe("cci post --kind decision --lane root --to inc-sand-fix 'ship it'", SAND_NOT_MIGRATED): Allow(),
        probe("echo 'kubectl get pods'", SAND_NOT_MIGRATED): Allow(),
        probe("git show HEAD:service/store/open.go", SAND_NOT_MIGRATED): Warn(pattern="only spawns, routes, and acks"),
        probe("rg -n Retire service/store", SAND_NOT_MIGRATED): Warn(),
        probe("rg -n Retire", SAND_NOT_MIGRATED): Warn(),
        probe("ccx code read service/store/open.go --section 170-200", SAND_NOT_MIGRATED): Warn(),
        probe("ccx code search 'who retires a store'", SAND_NOT_MIGRATED): Warn(),
        probe("rg -n MECHANISM ~/.claude/scratch/drive/inbox", SAND_NOT_MIGRATED): Allow(),
        probe("rg -n 'api/health' /tmp/records", SAND_NOT_MIGRATED): Allow(),
        probe("rg -n '~nobody/x' /tmp/records", SAND_NOT_MIGRATED): Allow(),
        probe("cci tail --drive release-v3 --cursor root | rg -n MECHANISM", SAND_NOT_MIGRATED): Allow(),
        probe("cci tail --drive release-v3 --cursor root | grep -n MECHANISM", SAND_NOT_MIGRATED): Allow(),
        probe("cci tail --drive release-v3 --cursor root | rg Retire service/store", SAND_NOT_MIGRATED): Warn(),
        probe("rg -e 'api/health' /tmp/records", SAND_NOT_MIGRATED): Allow(),
        probe("rg -n Retire ~/.claude/worktrees/service/store", SAND_NOT_MIGRATED): Warn(),
        probe("git status", SAND_NOT_MIGRATED): Allow(),
        probe("git show HEAD:service/store/open.go", CLOSED_0820): Allow(),
        probe("kubectl -n plat get deploy api", CLOSED_0820): Allow(),
        Input(command="kubectl -n plat get deploy api", commands=incident_sources(SAND_NOT_MIGRATED)): Allow(),
    }),
)
def root_only_routes_during_an_incident(evt: BaseHookEvent) -> HookResult | None:
    if root_probe(evt) and CompactionState.load(evt).active and open_incidents(evt):
        return evt.context(ROOT_PROBE)
    return None


def attached(values: dict[str, tuple]) -> str:
    paths = [Path(str(value)).expanduser() for key in ("path", "body-file") for value in values.get(key, ()) if value]
    return "\n".join(path.read_text(errors="replace") for path in paths if path.is_file())


def causal_records(evt: BaseHookEvent) -> list[tuple[dict[str, tuple], str]]:
    posts = [values for call in evt.command.calls("cci") if (values := CCI_POST.bind(call).values).get("words", ())[:1] == ("post",)]
    return [
        (values, text)
        for values in posts
        if CAUSE_KINDS & set(values.get("kind", ())) and CAUSE.search(text := posted_text(values))
    ]


def cause(command: str, **extra: object) -> Input:
    return Input(command=command, state=ACTIVE, **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    skip_if=[FromSubagent()],
    max_fires=1,
    tests=separate("owner_facing.root_causes_quote_a_lane_mechanism", {
        cause("cci post --drive release-v3 --lane root --kind go --to worker-fix --text 'the autoscaler caused the worker crashes; scale the pool'"): Warn(
            pattern=r"^Warning, nothing was blocked: this root record names a cause and quotes no lane record\."
        ),
        cause("cci post --drive release-v3 --lane root --kind decision --to worker-fix --text 'a missing object is why uploads fail (#104717)'"): Allow(),
        cause("cci post --drive release-v3 --lane root --kind go --to worker-fix --text 'MECHANISM #104942: a missing object caused it; ship the fix'"): Allow(),
        cause("cci post --drive release-v3 --lane root --kind decision --to p5-api --re 112366 --text 'A: write it red asserting cause oom on run and metric'"): Allow(),
        cause("cci post --drive release-v3 --lane root --kind report --text 'the autoscaler caused the worker crashes'"): Warn(),
        cause("cci post --drive release-v3 --lane root --kind go --to landing-desk --pr 41464 --text 'land it'"): Allow(),
        cause("cci post --drive release-v3 --lane root --kind head --text 'p1 abc because rebased'"): Allow(),
        cause("cci tail --drive release-v3 --cursor root"): Allow(),
        cause(f"cci post --drive release-v3 --lane root --kind go --to worker-fix --path {CAUSE_RECORD}"): Warn(),
        Input(command="cci post --drive release-v3 --lane root --kind go --text 'the autoscaler caused it'"): Allow(),
        cause("cci post --drive release-v3 --lane x --kind go --text 'the autoscaler caused it'", agent_id="a1b2c3"): Allow(),
    }),
)
def root_causes_quote_a_lane_mechanism(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    for values, text in causal_records(evt):
        if not (values.get("re") or values.get("resolves") or RECORD_REF.search(text)):
            return evt.context(UNQUOTED_CAUSE)
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


def posted_text(values: dict[str, tuple]) -> str:
    return "\n".join(part for part in (record_text(values), attached(values)) if part)


def authorizing_lines(evt: BaseHookEvent) -> list[str]:
    records = [
        posted_text(values)
        for call in evt.command.calls("cci")
        if (values := CCI_POST.bind(call).values).get("words", ())[:1] == ("post",)
        and AUTHORIZING_KINDS & set(values.get("kind", ()))
    ]
    return AUTHORIZING_LINE.findall(inbox_text(evt)) + records


def posts_board(use) -> bool:
    return any(cmd.runs("cc-present", verb) for cmd in use.call.command_line for verb in BOARD_VERBS)


def board_text(evt: BaseHookEvent, use) -> str:
    command = use.call.command
    written = evt.ctx.turn.tool_calls.named("Write").items
    return "\n".join([command, *(write.call.content for write in written if write.call.file_path in command)])


def asked_subjects(evt: BaseHookEvent) -> set[str]:
    questions = evt.ctx.t.tool_calls.named("AskUserQuestion").items
    boards = evt.ctx.turn.tool_calls.named("Bash").items
    texts = [json.dumps(use.call.raw, ensure_ascii=False) for use in questions if use.result is None]
    texts += [board_text(evt, use) for use in boards if posts_board(use)]
    return set().union(*map(subjects, texts))


DRIVE_INBOX = "/home/u/.claude/scratch/drive/inbox/deploy-go.md"
ROUTING_QUESTION = T.assistant(
    T.tool("AskUserQuestion", questions=[{"question": "Switch acme routing to cluster-b now?", "options": [{"label": "Switch"}, {"label": "Hold"}]}])
)
MERGE_QUESTION = T.assistant(
    T.tool("AskUserQuestion", questions=[{"question": "Merge #30502 now?", "options": [{"label": "Merge"}, {"label": "Hold"}]}])
)
ROUTING_GO = f'echo "GO root (11:02 PM) acme-routing: switch acme routing to cluster-b now" >> {DRIVE_INBOX}'


def authorize(*transcript: object, **call: object) -> Input:
    return Input(transcript=[T.user("keep going"), *transcript], state=ACTIVE, **call)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash", "Edit", "Write")],
    skip_if=[FromSubagent()],
    tests=separate("owner_facing.authorizing_lines_wait_for_the_owner_question", {
        authorize(ROUTING_QUESTION, command=ROUTING_GO): Warn(pattern="later turn"),
        authorize(
            T.assistant(T.tool("Bash", command="cc-present push - <<'EOF'\n{\"blocks\": [{\"title\": \"Should acme routing switch?\"}]}\nEOF")),
            command=f"cat >> {DRIVE_INBOX} <<'EOF'\nLIFT root (11:02 PM) acme routing fence lifted.\nEOF",
        ): Warn(),
        authorize(MERGE_QUESTION, tool="Write", file=DRIVE_INBOX, content="UNBLOCK root (11:02 PM) #30502 may merge now.\n"): Warn(),
        authorize(
            T.assistant(T.tool("Write", file_path="/tmp/board.json", content='{"title": "Switch acme routing?"}')),
            T.assistant(T.tool("Bash", command="cc-present push /tmp/board.json")),
            tool="Write",
            file=DRIVE_INBOX,
            content="GO root (11:02 PM) acme routing switch applied.\n",
        ): Warn(),
        authorize(MERGE_QUESTION, command="cci post --lane root --kind go --to landing-desk --pr 30502 --text 'land it'"): Warn(
            pattern="cci post"
        ),
        authorize(ROUTING_QUESTION, command="cci post --lane root --kind lift --to deploy-desk 'acme routing fence lifted'"): Warn(),
        authorize(MERGE_QUESTION, command="cci post --lane root --kind note --to landing-desk --pr 30502 --text 'question is open'"): Allow(),
        authorize(MERGE_QUESTION, command="cci post --lane root --kind go --to landing-desk --pr 30409 --text 'land it'"): Allow(),
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
    }),
)
def authorizing_lines_wait_for_the_owner_question(evt: BaseHookEvent) -> HookResult | None:
    lines = authorizing_lines(evt)
    if not lines or not CompactionState.load(evt).active:
        return None
    asked = asked_subjects(evt)
    return evt.context(AUTHORIZED_BESIDE_A_QUESTION) if any(subjects(line) & asked for line in lines) else None
