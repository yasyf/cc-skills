from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

from cc_transcript.models import AttachmentEvent, UserEvent
from captain_hook import (
    Allow,
    Ask,
    Block,
    BaseHookEvent,
    Event,
    FromSubagent,
    HookResult,
    Input,
    T,
    Tool,
    Warn,
    on,
)
from captain_hook.transcripts import TranscriptLoadError
from captain_hook.util import reqenv

from .compaction_handoff import LR, bus_name, Communicator, communicator_tests, CompactionState, Coordinator, coordinator_tests, drive_entry, judged, script
from .nudges import NagClock, once_per_hour
from .owner_facing import post_values, incident_sources, last_reply, open_incidents
from .root_context import HARNESS_PREFIXES
from .tests.incident_fixtures import CLOSED_0820, SAND_NOT_MIGRATED
from .tests.register_fixtures import DRIVE_CONFIG

ACTIVE = [CompactionState(active=True, coordinator=True)]
DRIVE_ACTIVE = [CompactionState(active=True, coordinator=True, slug="brook")]
REPLY_WINDOW = 256
CLAUDE = "claude -p"
JEV = "uv run --no-project --quiet"
PACIFIC = ZoneInfo("America/Los_Angeles")
MINUTES_A_DAY = 24 * 60
LABEL_SLACK_MINUTES = 1
NEAR_ANY_TURN_MINUTES = 15
SHARED_RUN_WORDS = 6
NEAREST_SHOWN = 2
OWNER_LABEL = re.compile(r"\bowner(?:'s)?,?\s+(?:at\s+)?(\d{1,2}):(\d{2})(?::\d{2})?\s*([ap])\.?m\b\.?", re.IGNORECASE)
QUOTED = re.compile(r"[\"“]([^\"”\n]+)[\"”]")
WORD = re.compile(r"[\w']+")
MISLABELED = (
    "Warning, nothing was blocked: an `OWNER h:mm` label below is off from the owner message it quotes, or, quoting none, more than 15 minutes"
    " from every owner message. The nearest owner message times are beside each; name his ledger row instead of typing a time."
)
MID_TURN = [
    T.user("pause the batch until the scan lands", timestamp="2026-01-15T20:09:00Z"),
    {
        "type": "attachment",
        "timestamp": "2026-01-15T20:19:00Z",
        "attachment": {"type": "queued_command", "prompt": "also fix that hook timing mismatch and stop waiting for me to ask", "commandMode": "prompt"},
    },
    {
        "type": "attachment",
        "timestamp": "2026-01-15T20:21:00Z",
        "attachment": {"type": "queued_command", "prompt": "then post the board once the fix lands", "commandMode": "prompt"},
    },
]
IDLE_CLAIM = re.compile(
    r"\b(?:is|was|looks|sat|sits|went)\s+(?:idle|silent|dead|stuck|empty)\b|(?<=[\w.-]):\s*(?:idle|silent|dead|stuck|empty)\b"
    r"|\bno (?:code|commits|work|progress)\b|\b(?:did|has done|done) nothing\b"
    r"|\bst(?:and(?:s|ing)?|ood)(?=\s+(?:\S+\s+)?down\b)|\brelaunch(?:ed|ing)?\b",
    re.IGNORECASE,
)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
LANE_NAME = re.compile(r"(?<![\w./-])[a-z][a-z0-9]*(?:-[a-z0-9]+)+(?![\w/-])")
NOT_LANES = frozenset({"lane-status", "stand-down"})
LANE_STATUS = f"{Path(__file__).parents[2] / 'bin' / 'drive.py'} lane-status"
UNREAD_LANE = (
    f"Warning, nothing was blocked: A claim that a lane is idle, dead, or done nothing, or a stand-down or relaunch, follows `{LANE_STATUS} <lane>` in the same turn."
    " Run it, then report what it shows."
)
CODE_CLAIM = re.compile(
    r"\bthe (?:review |codex |real )?findings (?:were|are)\b|\b(?:codex|sol|the review(?:er)?|the finder) found\b"
    r"|\bthe (?:mechanism|reason) (?:is|was)\b|\bit works by\b|\bwhat happens is\b|(?<!by )\bmeans\b(?! of\b)",
    re.IGNORECASE,
)
READ_TOOLS = frozenset({"Read", "Grep", "Glob"})
READ_MCP = ("mcp__plugin_cc-context_", "mcp__plugin_cc-notes_")
READ_COMMANDS = (
    ("cat",),
    ("sed",),
    ("head",),
    ("tail",),
    ("rg",),
    ("grep",),
    ("jq",),
    ("git", "show"),
    ("git", "diff"),
    ("git", "log"),
    ("git", "blame"),
    ("ccx", "code"),
    ("ccx", "vcs"),
    ("ccx", "repo"),
    ("cci", "show"),
    ("cci", "tail"),
    ("cci", "digest"),
    ("ccn",),
    ("gh", "pr", "view"),
    ("gh", "pr", "diff"),
)
WORKER_ANSWER = ("<teammate-message", '<channel source="plugin:cc-inbox:cci"')
UNREAD_CLAIM = (
    "Warning, nothing was blocked: A claim about what code does or what a review found follows a read of the file or record, or the owning worker's answer, in the same turn."
    " Run `cci show <seq>` or `Read` the file, or ask the worker."
)
DEFECT_ADMITTED = re.compile(
    r"\bmy (?:bad|doing|fault|mistake|miss|error|call was wrong)\b|\bI (?:missed|forgot|should have|didn'?t|was wrong|got \w+(?: \w+)? wrong)\b"
    r"|\b(?:tooling|tool|hook|runner|desk|worker|script|check)\s+(?:defect|bug|gap|is broken|was broken|broke|misfired|failed)\b"
    r"|\bdead lane\b|\blane (?:is|was) dead\b|\bmissed (?:a|the) step\b|\bstep (?:was )?skipped\b|\b(?:was|were) wrong\b",
    re.IGNORECASE,
)
NARRATED_DEFECT = (
    "Warning, nothing was blocked: a judge reads this reply as admitting a defect whose fix nothing in this turn started."
    " Spawn the fix lane or post its `cci post --kind go`, then report it."
)
OVERCLAIMED = (
    "Warning, nothing was blocked: a judge reads this reply as claiming more about the code or a review than what this turn read shows."
    " Read the part it rests on, or say what is still unread."
)
ACTION_SHOWN = 300
READ_SHOWN = 1500
FLAGGED = {"verdict": "yes", "artifact_phrase": "the runner was broken", "evidence_phrase": "", "sentence": "It admits the runner broke, and nothing this turn starts the fix."}
CLEARED = {"verdict": "no", "artifact_phrase": "", "evidence_phrase": "", "sentence": "Nothing to add."}


def judge_answer(verdict: dict) -> dict[str, str | tuple[int, str]]:
    return {JEV: json.dumps({"p_yes": 0.9 if verdict["verdict"] == "yes" else 0.1, "tokens": 2}), CLAUDE: json.dumps({"structured_output": verdict, "total_cost_usd": 0.01, "usage": {"input_tokens": 1000, "output_tokens": 50}})}


def owner_text(event: object) -> str:
    match event:
        case UserEvent() if not event.meta.is_sidechain and not event.is_agent_injected:
            return event.text
        case AttachmentEvent(attachment_type="queued_command") if not event.meta.is_sidechain:
            return event.detail.prompt or ""
        case _:
            return ""


def owner_message(event: object) -> str:
    text = owner_text(event)
    return text if text and not event.meta.is_meta and not text.lstrip().startswith(HARNESS_PREFIXES) else ""


def owner_turns(evt: BaseHookEvent) -> list[tuple[datetime, list[str]]]:
    return [
        (event.meta.timestamp.astimezone(PACIFIC), spoken(text))
        for event in evt.ctx.t.events
        if (text := owner_message(event))
    ]


def spoken(text: str) -> list[str]:
    return WORD.findall(text.casefold())


def runs(words: list[str]) -> set[tuple[str, ...]]:
    return {tuple(words[at : at + SHARED_RUN_WORDS]) for at in range(len(words) - SHARED_RUN_WORDS + 1)}


def says(words: list[str], quote: list[str]) -> bool:
    return bool(quote) and f" {' '.join(quote)} " in f" {' '.join(words)} "


def quoted_turn(segment: str, turns: list[tuple[datetime, list[str]]]) -> datetime | None:
    quotes = [spoken(quote) for quote in QUOTED.findall(segment)]
    latest_first = turns[::-1]
    if source := next((at for at, words in latest_first if any(says(words, quote) for quote in quotes)), None):
        return source
    shared = runs(spoken(segment))
    overlap, source = max(((len(shared & runs(words)), at) for at, words in latest_first), key=lambda pair: pair[0])
    return source if overlap else None


def labeled_segments(text: str) -> list[tuple[re.Match[str], str]]:
    labels = list(OWNER_LABEL.finditer(text))
    return [(label, text[label.start() : following.start() if following else None]) for label, following in pairwise([*labels, None])]


def minute_of_day(hour: int, minute: int, half: str) -> int:
    return (hour % 12 + (12 if half.casefold() == "p" else 0)) * 60 + minute


def apart(first: int, second: int) -> int:
    gap = abs(first - second) % MINUTES_A_DAY
    return min(gap, MINUTES_A_DAY - gap)


def stamp(at: datetime) -> str:
    return at.strftime("%-I:%M %p")


def mislabeled(evt: BaseHookEvent, text: str) -> list[str]:
    labeled = labeled_segments(text)
    turns = owner_turns(evt) if labeled else []
    wrong = []
    for label, segment in labeled if turns else ():
        minute = minute_of_day(int(label[1]), int(label[2]), label[3])
        source = quoted_turn(segment, turns)
        ranked = [source] if source else sorted((at for at, _ in turns), key=lambda at: apart(minute, at.hour * 60 + at.minute))
        if apart(minute, ranked[0].hour * 60 + ranked[0].minute) <= (LABEL_SLACK_MINUTES if source else NEAR_ANY_TURN_MINUTES):
            continue
        nearest = " or ".join(dict.fromkeys(stamp(at) for at in ranked[:NEAREST_SHOWN]))
        wrong.append(f"OWNER {label[1]}:{label[2]} {label[3].upper()}M -> OWNER {nearest}")
    return wrong


def turn_commands(evt: BaseHookEvent) -> list[tuple[str, ...]]:
    return [tuple(cmd.argv) for use in evt.ctx.turn.tool_calls.named("Bash").items for cmd in use.call.command_line]


def script_args(argv: tuple[str, ...], names: frozenset[str]) -> tuple[str, ...] | None:
    at = next((index for index, word in enumerate(argv[:3]) if Path(word).name in names), None)
    return None if at is None else argv[at + 1 :]


def idle_claimed_lanes(reply: str) -> list[str]:
    return [lane for sentence in SENTENCE_END.split(reply) if IDLE_CLAIM.search(sentence) for lane in LANE_NAME.findall(IDLE_CLAIM.sub(" ", sentence)) if lane not in NOT_LANES]


def lane_status_read(evt: BaseHookEvent, lanes: set[str]) -> bool:
    reads = [set(args[1:]) for argv in turn_commands(evt) if (args := script_args(argv, frozenset({"drive.py"}))) and args[0] == "lane-status"]
    return bool(reads) and all(any(lane in read for read in reads) for lane in lanes)


def lane_status_ran(evt: BaseHookEvent) -> bool:
    return any((args := script_args(argv, frozenset({"drive.py"}))) and args[0] == "lane-status" for argv in turn_commands(evt))


def first_line(text: object) -> str:
    return next(iter(str(text or "").strip().splitlines()), "")[:ACTION_SHOWN]


def turn_actions(evt: BaseHookEvent) -> str:
    lines = []
    for use in evt.ctx.turn.tool_calls.items:
        name, raw = use.call.name, use.call.raw
        match name:
            case "Agent":
                lines.append(f"- Agent {raw.get('name') or raw.get('subagent_type') or ''}: {first_line(raw.get('prompt'))}")
            case "Bash":
                lines.append(f"- Bash: {str(raw.get('command') or '')[:ACTION_SHOWN]}")
            case "SendMessage":
                lines.append(f"- SendMessage to {raw.get('to')}: {first_line(raw.get('message'))}")
            case "Edit" | "Write":
                lines.append(f"- {name} {raw.get('file_path')}")
            case _:
                lines.append(f"- {name}")
    return "What the writer did this turn, in order:\n" + ("\n".join(lines) or "- nothing")


def read_call(name: str, argvs: list[tuple[str, ...]]) -> bool:
    return name in READ_TOOLS or name.startswith(READ_MCP) or any(argv[: len(read)] == read for argv in argvs for read in READ_COMMANDS)


def turn_reads(evt: BaseHookEvent) -> str:
    shown = []
    for use in evt.ctx.turn.tool_calls.items:
        argvs = [(Path(cmd.argv[0]).name, *cmd.argv[1:]) for cmd in use.call.command_line if cmd.argv] if use.call.name == "Bash" else []
        if read_call(use.call.name, argvs):
            what = use.call.raw.get("command") or use.call.raw.get("file_path") or use.call.raw.get("pattern") or ""
            shown.append(f"$ {use.call.name} {what}\n{(use.result.content if use.result else '')[:READ_SHOWN]}")
    shown += [event.text[:READ_SHOWN] for event in evt.ctx.turn.events if isinstance(event, UserEvent) and event.text and any(marker in event.text for marker in WORKER_ANSWER)]
    return "What the writer read this turn, verbatim:\n" + "\n\n".join(shown)


def relabel(wrong: list[str]) -> str:
    return "\n".join([MISLABELED, "", "```", *wrong, "```"])


def owner_said(text: str, at: str) -> dict:
    return T.user(text, timestamp=at)


def replied(*events: object, reply: str) -> list[object]:
    return [*events, T.assistant(reply)]


def relabeled(evt: BaseHookEvent, texts: Callable[[], list[str]]) -> str | None:
    try:
        wrong = [line for text in texts() for line in mislabeled(evt, text)]
    except TranscriptLoadError:
        return None
    return relabel(wrong) if wrong else None


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    only_if=[Communicator()],
    tests=communicator_tests({
        Input(
            transcript=replied(owner_said("stop all cuts now", "2026-01-15T00:11:00Z"), reply='Logged OWNER 7:58 PM "stop all cuts now" as a hold.'),
            state=ACTIVE,
        ): Allow(system_message=r"^Warning, nothing was blocked[\s\S]*OWNER 4:11 PM"),
        Input(
            transcript=replied(owner_said("stop all cuts now", "2026-01-15T00:11:00Z"), reply='Logged OWNER 4:11 PM "stop all cuts now" as a hold.'),
            state=ACTIVE,
        ): Ask(),
        Input(
            transcript=replied(owner_said("stop all cuts now", "2026-01-15T00:11:30Z"), reply="Logged OWNER 4:12 PM as a hold."),
            state=ACTIVE,
        ): Ask(),
        Input(
            transcript=replied(
                owner_said("stop all cuts now", "2026-01-15T00:11:00Z"),
                owner_said("ship it", "2026-01-15T00:30:00Z"),
                reply='Logged OWNER 4:30 PM "stop all cuts now" as a hold.',
            ),
            state=ACTIVE,
        ): Allow(system_message=r"OWNER 4:11 PM"),
        Input(transcript=replied(*MID_TURN, reply='Recorded OWNER 12:21 PM "post the board once the fix lands".'), state=ACTIVE): Ask(),
        Input(transcript=replied(owner_said("ship it", "2026-01-15T00:11:00Z"), reply="The fix lane is building."), state=ACTIVE): Ask(),
        Input(transcript=replied(owner_said("ship it", "2026-01-15T00:11:00Z"), reply="Logged OWNER 7:58 PM as a hold.")): Ask(),
    }),
)
def owner_labels_carry_the_turn_stamp(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active or not (warning := relabeled(evt, lambda: [last_reply(evt)])):
        return None
    return evt.allow(system_message=warning)


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    only_if=[Communicator()],
    tests=communicator_tests({
        Input(transcript=[T.user("status?"), T.assistant("worker-fix-2 is idle with no code; relaunching it.")], state=ACTIVE): Allow(system_message=r"drive\.py lane-status <lane>"),
        Input(transcript=[T.user("status?"), T.assistant("worker-fix-2 is empty.")], state=ACTIVE): Allow(system_message=r"^Warning, nothing was blocked"),
        Input(transcript=[T.user("status?"), T.assistant("worker-fix-2: idle")], state=ACTIVE): Allow(system_message=r"^Warning, nothing was blocked"),
        Input(
            transcript=[
                T.user("status?"),
                T.assistant(T.tool("Bash", command="drive.py lane-status worker-fix-2 --drive release-v3")),
                T.assistant("checked."),
                T.user("and now?"),
                T.assistant("worker-fix-2 is idle; relaunching it."),
            ],
            state=ACTIVE,
        ): Allow(system_message=r"^Warning, nothing was blocked"),
        Input(transcript=[T.user("status?"), T.assistant("worker-fix-2 is idle; relaunching it.")], state=ACTIVE): Allow(system_message=r"^Warning, nothing was blocked"),
        Input(transcript=[T.user("status?"), T.assistant("I stood worker-fix-2 down.")], state=ACTIVE): Allow(system_message=r"^Warning, nothing was blocked"),
        Input(
            transcript=[
                T.user("status?"),
                T.assistant(T.tool("Bash", command="drive.py lane-status worker-fix-2 --drive release-v3")),
                T.assistant("worker-fix-2 is idle per `drive.py lane-status worker-fix-2`; relaunching it."),
            ],
            state=ACTIVE,
        ): Ask(),
        Input(
            transcript=[
                T.user("status?"),
                T.assistant(T.tool("Bash", command="drive.py lane-status worker-fix-2 port-misc-3 --drive release-v3")),
                T.assistant("worker-fix-2 is idle and port-misc-3 is idle per lane-status; the long-running stop-hook fix keeps going."),
            ],
            state=ACTIVE,
        ): Ask(),
        Input(transcript=[T.user("status?"), T.assistant("The fix lane opened its PR.")], state=ACTIVE): Ask(),
        Input(
            transcript=[T.user("merge?"), T.assistant("Third merge question is up: the judgment rule has no work left and reads clean. Three more to come.")], state=ACTIVE
        ): Ask(),
        Input(
            transcript=[T.user("status?"), T.assistant("worker-fix-2 is idle; relaunching it.")],
            state=[*ACTIVE, NagClock(said={"idle-claim:worker-fix-2": time.time()})],
        ): Ask(),
        Input(transcript=[T.user("status?"), T.assistant("worker-fix-2 is idle.")]): Ask(),
    }),
)
def idle_claims_read_lane_status(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active or lane_status_ran(evt):
        return None
    unread = idle_claimed_lanes(last_reply(evt))
    return evt.allow(system_message=UNREAD_LANE) if once_per_hour(evt, [f"idle-claim:{lane}" for lane in unread]) else None


def read_this_turn(evt: BaseHookEvent) -> bool:
    turn = evt.ctx.turn
    tools = [use.call.name for use in turn.tool_calls.items]
    commands = [(Path(argv[0]).name, *argv[1:]) for argv in turn_commands(evt) if argv]
    return (
        any(name in READ_TOOLS or name.startswith(READ_MCP) for name in tools)
        or any(argv[: len(read)] == read for argv in commands for read in READ_COMMANDS)
        or any(marker in event.text for event in turn.events if isinstance(event, UserEvent) and event.text for marker in WORKER_ANSWER)
    )


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    only_if=[Communicator()],
    tests=communicator_tests({
        Input(transcript=[T.user("what did review find?"), T.assistant("Codex found two races in the retire path.")], state=ACTIVE): Allow(system_message=r"same turn"),
        Input(transcript=[T.user("how does landing work?"), T.assistant("Landing means the journal is copied to a shared server.")], state=ACTIVE): Allow(system_message=r"^Warning, nothing was blocked"),
        Input(
            transcript=[
                T.user("what did review find?"),
                T.assistant(T.tool("Bash", command="cci show 149433")),
                T.assistant("Per #149433, codex found two races in the retire path."),
            ],
            state=ACTIVE,
            commands=judge_answer(CLEARED),
        ): Ask(),
        Input(
            transcript=[
                T.user("what did review find?"),
                T.assistant(T.tool("Bash", command="cci show 149433")),
                T.assistant("Per #149433, codex found two races in the retire path."),
            ],
            state=ACTIVE,
            commands=judge_answer(FLAGGED),
        ): Allow(system_message=r"^Warning, nothing was blocked: a judge reads this reply as claiming more[\s\S]*the runner was broken"),
        Input(
            transcript=[
                T.user("what did review find?"),
                T.assistant(T.tool("Bash", command="cci show 149433")),
                T.assistant("Per #149433, codex found two races in the retire path."),
            ],
            state=ACTIVE,
            commands={CLAUDE: (1, "model unavailable")},
        ): Ask(),
        Input(
            transcript=[
                T.user("how does landing work?"),
                T.assistant(T.tool("Read", file_path="/repo/service/landing.go")),
                T.assistant("The mechanism is a journal upload after each merge."),
            ],
            state=ACTIVE,
            commands=judge_answer(CLEARED),
        ): Ask(),
        Input(
            transcript=[
                T.user("what did review find?"),
                T.assistant(T.tool("Bash", command="cci show 149433")),
                T.assistant("checked."),
                T.user("and the mechanism?"),
                T.assistant("The mechanism is a journal upload after each merge."),
            ],
            state=ACTIVE,
        ): Allow(system_message=r"^Warning, nothing was blocked"),
        Input(
            transcript=[
                T.user('<teammate-message teammate_id="p8a-step5-2">Landing uploads the journal after each merge.</teammate-message>'),
                T.assistant("The mechanism is a journal upload after each merge, per p8a-step5-2."),
            ],
            state=ACTIVE,
            commands=judge_answer(CLEARED),
        ): Ask(),
        Input(transcript=[T.user("status?"), T.assistant("Three PRs are open and two lanes are working.")], state=ACTIVE): Ask(),
        Input(transcript=[T.user("status?"), T.assistant("The PR lands by means of the queue.")], state=ACTIVE): Ask(),
        Input(transcript=[T.user("what did review find?"), T.assistant("Codex found two races in the retire path.")]): Ask(),
    }),
)
def code_claims_follow_a_read(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active or not (claims := CODE_CLAIM.findall(reply := last_reply(evt))):
        return None
    if not read_this_turn(evt):
        return evt.allow(system_message=UNREAD_CLAIM) if once_per_hour(evt, [f"code-claim:{claim.casefold()}" for claim in claims]) else None
    found = judged("claim", turn_reads(evt), reply, (drive_entry(evt) or {}).get("drive"))
    return evt.allow(system_message=f"{OVERCLAIMED}\n{found}") if found else None


@on(
    Event.PreToolUse,
    only_if=[Coordinator(), Tool("Bash", "SendMessage")],
    skip_if=[FromSubagent()],
    max_fires=1,
    tests=coordinator_tests({
        Input(command="cci post --drive d --lane root --kind stand-down --to worker-fix-2 --text 'stand down'", state=ACTIVE): Warn(
            pattern=r"drive\.py lane-status <lane>"
        ),
        Input(
            command="cci post --drive d --lane root --kind stand-down --to worker-fix-2 --text 'stand down'",
            transcript=[T.user("status?"), T.assistant(T.tool("Bash", command="drive.py lane-status worker-fix-2 --drive d"))],
            state=ACTIVE,
        ): Allow(),
        Input(
            command="cci post --drive d --lane root --kind stand-down --to worker-fix-2 --text 'stand down'",
            transcript=[T.user("status?"), T.assistant(T.tool("Bash", command="drive.py lane-status worker-fix-3 --drive d"))],
            state=ACTIVE,
        ): Warn(),
        Input(tool="SendMessage", tool_input={"to": "worker-fix-2", "message": "Stand down: stop working and send nothing further."}, state=ACTIVE): Warn(),
        Input(
            tool="SendMessage",
            tool_input={"to": "worker-fix-2", "message": "Stand down: stop working and send nothing further."},
            transcript=[T.user("status?"), T.assistant(T.tool("Bash", command="drive.py lane-status worker-fix-2 --drive d"))],
            state=ACTIVE,
        ): Allow(),
        Input(command="cci post --drive d --lane root --kind go --to worker-fix-2 --text 'ship it'", state=ACTIVE): Allow(),
        Input(command="cci post --drive d --lane root --kind stand-down --to x --text 'stand down'"): Allow(),
    }),
)
def stand_downs_read_lane_status(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    if evt.tool_name == "SendMessage":
        raw = evt.input.raw
        message, to = raw.get("message"), raw.get("to")
        standing_down = isinstance(message, str) and re.match(r"\s*stand[ -]down\b", message, re.IGNORECASE)
        return evt.context(UNREAD_LANE) if standing_down and not lane_status_read(evt, {str(to)}) else None
    posts = [post_values(evt, call) for call in evt.command.calls("cci")]
    lanes = {lane for values in posts if "stand-down" in values.get("kind", ()) for to in values.get("to", ()) for lane in to.split(",") if lane}
    standing_down = any("stand-down" in values.get("kind", ()) for values in posts)
    return evt.context(UNREAD_LANE) if standing_down and not lane_status_read(evt, lanes) else None


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    max_fires=1,
    only_if=[Communicator()],
    tests=communicator_tests({
        Input(
            transcript=[T.user("why is it slow"), T.assistant("My bad: the runner was broken and I missed the stalled launch.")],
            state=ACTIVE,
            commands=incident_sources(SAND_NOT_MIGRATED) | judge_answer(FLAGGED),
        ): Allow(system_message=r"^Warning, nothing was blocked: a judge reads this reply as admitting a defect[\s\S]*nothing this turn starts the fix"),
        Input(
            transcript=[T.user("why is it slow"), T.assistant("My bad: the runner was broken and I missed the stalled launch.")],
            state=ACTIVE,
            commands=incident_sources(SAND_NOT_MIGRATED) | {CLAUDE: (1, "model unavailable")},
        ): Ask(),
        Input(
            transcript=[
                T.user("why is it slow"),
                T.assistant(T.tool("Agent", name="runner-stall-fix", prompt="Fix the stalled launch.")),
                T.assistant("My bad: the runner was broken; runner-stall-fix is on it."),
            ],
            state=ACTIVE,
            commands=incident_sources(SAND_NOT_MIGRATED) | judge_answer(CLEARED),
        ): Ask(),
        Input(
            transcript=[
                T.user("why is it slow"),
                T.assistant(T.tool("Agent", name="pr-review-4", prompt="Review the open PR.")),
                T.assistant("My bad: the runner was broken and I missed the stalled launch."),
            ],
            state=ACTIVE,
            commands=incident_sources(SAND_NOT_MIGRATED) | judge_answer(FLAGGED),
        ): Allow(system_message=r"^Warning, nothing was blocked"),
        Input(
            transcript=[T.user("why is it slow"), T.assistant("My bad: the runner was broken and I missed the stalled launch.")],
            state=ACTIVE,
            commands=incident_sources(CLOSED_0820),
        ): Ask(),
        Input(
            transcript=[T.user("why is it slow"), T.assistant("The fix lane opened its PR.")],
            state=ACTIVE,
            commands=incident_sources(SAND_NOT_MIGRATED),
        ): Ask(),
    }),
)
def narrated_defects_ship_their_fix(evt: BaseHookEvent) -> HookResult | None:
    reply = last_reply(evt)
    if not CompactionState.load(evt).active or not DEFECT_ADMITTED.search(reply) or not open_incidents(evt):
        return None
    found = judged("defect", turn_actions(evt), reply, (drive_entry(evt) or {}).get("drive"))
    return evt.allow(system_message=f"{NARRATED_DEFECT}\n{found}") if found else None


TAP_CLAIM = re.compile(
    r"\b(?:wait(?:s|ing)?\s+(?:only\s+)?(?:on|for)|await(?:s|ing)?|needs?|needing|blocked\s+on|held\s+for)\s+(?:your|his|the\s+owner'?s?)\s+(?:board\s+)?(?:taps?|approvals?|picks?|go-ahead)\b"
    r"|\bon\s+your\s+board\b|\bfor\s+you\s+to\s+(?:tap|approve|pick)\b",
    re.IGNORECASE,
)
CLAIM_NEGATED = re.compile(r"\b(?:no|nothing|none|not|never|without)\b|n't\b", re.IGNORECASE)
CLAIMED_PR = re.compile(r"(?<![\w&])#(\d{4,5})\b")
OWNER_WAITS = f"{LR} board waits"
OWNER_WAITS_SECONDS = 30
FALSE_TAP = (
    "Your reply says something waits on the owner's tap, approval or pick, and his board does not show it. "
    "Restate it in plain words from these lines, and quote what `{command}` prints:\n```\n{lines}\n```"
)
NOTHING_WAITS = "nothing on his board awaits his tap or pick"
FALSE_PR = json.dumps([{"number": 45507, "ready_for_tap": False, "waits_on": "fidelity grade missing at head"}])
TRUE_PR = json.dumps([{"number": 45541, "ready_for_tap": True, "waits_on": ""}])
ROWS_READ = f"{LR} board rows --drive brook-drive --json"
UNREADY = "#{pr} is not ready for his tap: {waits}"
NO_ROW = "it has no row"
CHECK_BOARD = f"{OWNER_WAITS} --drive brook-drive --json"


def tap_claims(reply: str) -> tuple[list[str], bool]:
    """The PRs a reply says wait on the owner's tap, approval or pick, and whether it says so of something it names by no PR number; a sentence whose claim follows a negation claims nothing."""
    prs: list[str] = []
    bare = False
    for sentence in SENTENCE_END.split(reply):
        claimed = [found for found in TAP_CLAIM.finditer(sentence) if not CLAIM_NEGATED.search(sentence[: found.start()])]
        if not claimed:
            continue
        named = CLAIMED_PR.findall(sentence)
        prs += [pr for pr in named if pr not in prs]
        bare = bare or not named
    return prs, bare


def board_rows(drive: str) -> dict[str, dict]:
    done = subprocess.run([str(LR), "board", "rows", "--drive", drive, "--json"], capture_output=True, text=True, timeout=OWNER_WAITS_SECONDS, env=dict(reqenv.env_map()), check=True)
    return {str(row["number"]): row for row in json.loads(done.stdout)}


def unready(rows: dict[str, dict], prs: list[str]) -> list[str]:
    return [UNREADY.format(pr=pr, waits=rows[pr]["waits_on"] if pr in rows else NO_ROW) for pr in prs if pr not in rows or not rows[pr]["ready_for_tap"]]


def owner_waits(drive: str, *args: str) -> list[dict]:
    argv = [str(LR), "board", "waits", "--drive", drive, "--json", *args]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=OWNER_WAITS_SECONDS, env=dict(reqenv.env_map()), check=True)
    return json.loads(done.stdout)


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    only_if=[Communicator()],
    tests=communicator_tests({
        Input(transcript=[T.user("anything for me?"), T.assistant("#45507 waits on your tap, and #45524 waits on it below.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={ROWS_READ: FALSE_PR}): Block(
            pattern=r"#45507 is not ready for his tap: fidelity grade missing at head"
        ),
        Input(transcript=[T.user("anything for me?"), T.assistant("#45541 waits on your tap.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={ROWS_READ: TRUE_PR}): Allow(),
        Input(transcript=[T.user("anything for me?"), T.assistant("Two fixes are waiting on your approval.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={CHECK_BOARD: "[]"}): Block(pattern=NOTHING_WAITS),
        Input(transcript=[T.user("anything for me?"), T.assistant("The routing card is on your board.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={CHECK_BOARD: TRUE_PR}): Allow(),
        Input(transcript=[T.user("anything for me?"), T.assistant("No PR waits on your tap; #45507 is not waiting on your approval either.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG): Allow(),
        Input(transcript=[T.user("anything for me?"), T.assistant("#45507 waits on your tap.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={ROWS_READ: (1, "store unreadable")}): Allow(),
        Input(transcript=[T.user("anything for me?"), T.assistant("#45507 waits on your tap.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG, agent_id="a1b2c3"): Allow(),
        Input(transcript=[T.user("status?"), T.assistant("The release worker has finished and stopped. Platform cluster: everything shipped, and the health check passed with nothing missing. Customer clusters: every memory fix is live there except the retry-alert counting change, which goes out with a later release. Health: job starts stayed normal and there were no error spikes. One false alarm along the way: the post-deploy check went red, but nothing was rolled back. It's the same pipeline defect the worker noted earlier, where the check counts running jobs over only a few seconds right after a deploy.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={CHECK_BOARD: "[]"}): Allow(),
        Input(transcript=[T.user("status?"), T.assistant("The durable-agents session owns it, and I've asked them to either put it on your board or merge it themselves."), T.user("release worker stood down")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={CHECK_BOARD: "[]"}): Allow(),
        Input(transcript=[T.user("status?"), T.assistant("Nothing is ready."), T.user("release worker stood down"), T.assistant("The merge card waits on your tap.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={CHECK_BOARD: "[]"}): Block(pattern=NOTHING_WAITS),
        Input(transcript=[T.user("anything for me?"), T.assistant("#45507 waits on your tap.")], env=DRIVE_CONFIG): Allow(),
    }),
)
def tap_claims_match_the_board(evt: BaseHookEvent) -> HookResult | None:
    """A communicator reply that says a PR or anything else waits on the owner's tap, approval or pick stands only when that PR's row in `lr board rows` says ready_for_tap, or when `lr board waits` finds anything awaiting him for a claim naming no PR; otherwise the reply goes back with the real state to say instead (owner, 4:40 AM Oct 30, #207532). A board that does not read blocks nothing."""
    if not CompactionState.load(evt).active or not (entry := drive_entry(evt)):
        return None
    prs, bare = tap_claims(last_reply(evt))
    if not prs and not bare:
        return None
    drive = bus_name(entry)
    try:
        lines = unready(board_rows(drive), prs) if prs else []
        if bare and not owner_waits(drive):
            lines.append(NOTHING_WAITS)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return None
    return evt.block(FALSE_TAP.format(lines="\n".join(lines), command=f"lr board rows --json --drive {drive}")) if lines else None
