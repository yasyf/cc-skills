from __future__ import annotations

import json
import re
import subprocess
import sys
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

from .compaction_handoff import CompactionState, bus_name, drive_entry, script
from .nudges import NagClock, once_per_hour
from .owner_facing import CCI_POST, incident_sources, last_reply, open_incidents, record_text
from .tests.incident_fixtures import CLOSED_0820, SAND_NOT_MIGRATED
from .tests.register_fixtures import DRIVE_CONFIG

ACTIVE = [CompactionState(active=True)]
DRIVE_ACTIVE = [CompactionState(active=True, slug="brook")]
REPLY_WINDOW = 256
PACIFIC = ZoneInfo("America/Los_Angeles")
MINUTES_A_DAY = 24 * 60
LABEL_SLACK_MINUTES = 1
NEAR_ANY_TURN_MINUTES = 15
SHARED_RUN_WORDS = 6
NEAREST_SHOWN = 2
NUMBERS_SHOWN = 8
NUMBER_TEXT_SHOWN = 160
OWNER_LABEL = re.compile(r"\bowner(?:'s)?,?\s+(?:at\s+)?(\d{1,2}):(\d{2})(?::\d{2})?\s*([ap])\.?m\b\.?", re.IGNORECASE)
QUOTED = re.compile(r"[\"“]([^\"”\n]+)[\"”]")
WORD = re.compile(r"[\w']+")
SYSTEM_PREFIXES = ("<", "[Request interrupted", "This session is being continued")
MISLABELED = (
    "Warning, nothing was blocked: an `OWNER h:mm` label below is off from the owner message it quotes, or, quoting none, more than 15 minutes"
    " from every owner message. The nearest owner message times are beside each; use the right one in your next message or record."
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
LAUNCHERS = frozenset({"orca-launch.sh", "desk-launch.py"})
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
    " Run `cci show <seq>` or `Read` the file, or ask the worker and tell the owner you are reading it and will answer in a few minutes."
)
DEFECT_ADMITTED = re.compile(
    r"\bmy bad\b|\bI (?:missed|forgot|should have|didn'?t)\b|\b(?:tooling|tool|hook|runner|desk)\s+(?:defect|bug|gap|is broken|was broken|misfired)\b"
    r"|\bdead lane\b|\blane (?:is|was) dead\b|\bmissed (?:a|the) step\b|\bstep (?:was )?skipped\b",
    re.IGNORECASE,
)
NARRATED_DEFECT = (
    "Warning, nothing was blocked: A reply that admits a tooling defect, a missed step, or a dead lane ships the fix in the same turn."
    " Spawn the fix lane or post its `cci post --kind go`, then report it."
)
MEASURED = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:%|percent\b|(?:users?|sessions?|runs?|errors?|crashes|failures?|requests?|events?|objects?|rows?|retires?|deletes?|refusals?|chats?|reports?)\b|(?:/|per\s+)(?:min(?:ute)?|hour|sec(?:ond)?|s)\b)",
    re.IGNORECASE,
)
QUERY_CITED = re.compile(r"#\d{3,}\b|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b|https?://\S+")
UNSOURCED_NUMBER = (
    "Warning, nothing was blocked: A count, rate, or percentage told to the owner names the query record it came from."
    " Put that record's `#<seq>` or link beside each number below, or drop the number."
)


def owner_text(event: object) -> str:
    match event:
        case UserEvent() if not event.meta.is_sidechain and not event.is_agent_injected:
            return event.text
        case AttachmentEvent(attachment_type="queued_command") if not event.meta.is_sidechain:
            return event.detail.prompt or ""
        case _:
            return ""


def owner_turns(evt: BaseHookEvent) -> list[tuple[datetime, list[str]]]:
    return [
        (event.meta.timestamp.astimezone(PACIFIC), spoken(text))
        for event in evt.ctx.t.events
        if (text := owner_text(event)) and not text.lstrip().startswith(SYSTEM_PREFIXES)
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


def launched_lanes(argv: tuple[str, ...]) -> list[str]:
    if (args := script_args(argv, LAUNCHERS)) is not None:
        return [next((arg for arg in args if not arg.startswith("-") and arg != "launch"), "")]
    if argv[:2] == ("cci", "post") and ("--kind", "go") in zip(argv, argv[1:]):
        return [value for flag, value in zip(argv, argv[1:]) if flag == "--to"] + [arg.removeprefix("--to=") for arg in argv if arg.startswith("--to=")]
    return []


def fix_named(evt: BaseHookEvent, reply: str) -> bool:
    spawned = [use.call.raw.get("name") for use in evt.ctx.turn.tool_calls.named("Agent").items]
    launched = [name for argv in turn_commands(evt) for name in launched_lanes(argv)]
    names = {part for name in [*spawned, *launched] if name for part in name.split(",") if part}
    return any(re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w.-])", reply) for name in names)


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
    tests={
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
    },
)
def owner_labels_carry_the_turn_stamp(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active or not (warning := relabeled(evt, lambda: [last_reply(evt)])):
        return None
    return evt.allow(system_message=warning)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    skip_if=[FromSubagent()],
    tests={
        Input(
            command="cci post --drive d --lane root --kind hold --text 'OWNER 7:58 PM: stop all cuts now'",
            transcript=[owner_said("stop all cuts now", "2026-01-15T00:11:00Z")],
            state=ACTIVE,
        ): Warn(pattern=r"^Warning, nothing was blocked[\s\S]*OWNER 4:11 PM"),
        Input(
            command="cci post --drive d --lane root --kind hold --text 'OWNER 4:11 PM: stop all cuts now'",
            transcript=[owner_said("stop all cuts now", "2026-01-15T00:11:00Z")],
            state=ACTIVE,
        ): Allow(),
        Input(command="cci post --drive d --lane root --kind hold --text 'cuts held'", transcript=[owner_said("stop", "2026-01-15T00:11:00Z")], state=ACTIVE): Allow(),
        Input(
            command="cci post --drive d --lane root --kind decide --text 'OWNER 12:19 PM: \"also fix that hook timing mismatch\"'",
            transcript=MID_TURN,
            state=ACTIVE,
        ): Allow(),
        Input(
            command="cci post --drive d --lane root --kind decide --text 'OWNER 12:09 PM: also fix that hook timing mismatch and stop waiting'",
            transcript=MID_TURN,
            state=ACTIVE,
        ): Warn(pattern=r"OWNER 12:09 PM -> OWNER 12:19 PM\n"),
        Input(command="cci post --drive d --lane root --kind decide --text 'OWNER 12:30 PM: board is posted'", transcript=MID_TURN, state=ACTIVE): Allow(),
        Input(
            command="cci post --drive d --lane root --kind decide --text 'OWNER 12:21 PM \"post the board once the fix lands\"; OWNER 12:09 PM \"pause the batch until the scan lands\"'",
            transcript=MID_TURN,
            state=ACTIVE,
        ): Allow(),
        Input(command="cci post --drive d --lane root --kind decide --text 'OWNER 12:51 PM: board is posted'", transcript=MID_TURN, state=ACTIVE): Warn(
            pattern=r"OWNER 12:51 PM -> OWNER 12:21 PM or 12:19 PM"
        ),
    },
)
def owner_records_carry_the_turn_stamp(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    posts = [record_text(values) for call in evt.command.calls("cci") if (values := CCI_POST.bind(call).values).get("words", ())[:1] == ("post",)]
    return evt.context(warning) if (warning := relabeled(evt, lambda: posts)) else None


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests={
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
    },
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
    tests={
        Input(transcript=[T.user("what did review find?"), T.assistant("Codex found two races in the retire path.")], state=ACTIVE): Allow(system_message=r"same turn"),
        Input(transcript=[T.user("how does landing work?"), T.assistant("Landing means the journal is copied to a shared server.")], state=ACTIVE): Allow(system_message=r"^Warning, nothing was blocked"),
        Input(
            transcript=[
                T.user("what did review find?"),
                T.assistant(T.tool("Bash", command="cci show 149433")),
                T.assistant("Per #149433, codex found two races in the retire path."),
            ],
            state=ACTIVE,
        ): Ask(),
        Input(
            transcript=[
                T.user("how does landing work?"),
                T.assistant(T.tool("Read", file_path="/repo/service/landing.go")),
                T.assistant("The mechanism is a journal upload after each merge."),
            ],
            state=ACTIVE,
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
        ): Ask(),
        Input(transcript=[T.user("status?"), T.assistant("Three PRs are open and two lanes are working.")], state=ACTIVE): Ask(),
        Input(transcript=[T.user("status?"), T.assistant("The PR lands by means of the queue.")], state=ACTIVE): Ask(),
        Input(transcript=[T.user("what did review find?"), T.assistant("Codex found two races in the retire path.")]): Ask(),
    },
)
def code_claims_follow_a_read(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active or not (claims := CODE_CLAIM.findall(last_reply(evt))) or read_this_turn(evt):
        return None
    return evt.allow(system_message=UNREAD_CLAIM) if once_per_hour(evt, [f"code-claim:{claim.casefold()}" for claim in claims]) else None


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash", "SendMessage")],
    skip_if=[FromSubagent()],
    max_fires=1,
    tests={
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
    },
)
def stand_downs_read_lane_status(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    if evt.tool_name == "SendMessage":
        raw = evt.input.raw
        message, to = raw.get("message"), raw.get("to")
        standing_down = isinstance(message, str) and re.match(r"\s*stand[ -]down\b", message, re.IGNORECASE)
        return evt.context(UNREAD_LANE) if standing_down and not lane_status_read(evt, {str(to)}) else None
    posts = [CCI_POST.bind(call).values for call in evt.command.calls("cci")]
    lanes = {lane for values in posts if "stand-down" in values.get("kind", ()) for to in values.get("to", ()) for lane in to.split(",") if lane}
    standing_down = any("stand-down" in values.get("kind", ()) for values in posts)
    return evt.context(UNREAD_LANE) if standing_down and not lane_status_read(evt, lanes) else None


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    max_fires=1,
    tests={
        Input(
            transcript=[T.user("why is it slow"), T.assistant("My bad: the runner was broken and I missed the stalled launch.")],
            state=ACTIVE,
            commands=incident_sources(SAND_NOT_MIGRATED),
        ): Allow(system_message=r"ships the fix in the same turn"),
        Input(
            transcript=[
                T.user("why is it slow"),
                T.assistant(T.tool("Agent", name="runner-stall-fix", prompt="Fix the stalled launch.")),
                T.assistant("My bad: the runner was broken; runner-stall-fix is on it."),
            ],
            state=ACTIVE,
            commands=incident_sources(SAND_NOT_MIGRATED),
        ): Ask(),
        Input(
            transcript=[
                T.user("why is it slow"),
                T.assistant(T.tool("Agent", name="pr-review-4", prompt="Review the open PR.")),
                T.assistant("My bad: the runner was broken and I missed the stalled launch."),
            ],
            state=ACTIVE,
            commands=incident_sources(SAND_NOT_MIGRATED),
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
    },
)
def narrated_defects_ship_their_fix(evt: BaseHookEvent) -> HookResult | None:
    reply = last_reply(evt)
    if CompactionState.load(evt).active and DEFECT_ADMITTED.search(reply) and not fix_named(evt, reply) and open_incidents(evt):
        return evt.allow(system_message=NARRATED_DEFECT)
    return None


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    max_fires=1,
    tests={
        Input(transcript=[T.user("how bad?"), T.assistant("About 300 requests per minute fail, and 12 runs crashed.")], state=ACTIVE): Allow(system_message=r"300 requests per minute"),
        Input(transcript=[T.user("how bad?"), T.assistant("15 runs crashed in the last hour (#104701).")], state=ACTIVE): Ask(),
        Input(transcript=[T.user("how bad?"), T.assistant("15 runs crashed in the last hour.")], state=ACTIVE): Allow(system_message=r"^Warning, nothing was blocked"),
        Input(transcript=[T.user("status?"), T.assistant("Three PRs are open and two lanes are working.")], state=ACTIVE): Ask(),
        Input(transcript=[T.user("how bad?"), T.assistant("15 runs crashed.")]): Ask(),
    },
)
def owner_numbers_cite_their_query(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    lines = [line.strip()[:NUMBER_TEXT_SHOWN] for line in last_reply(evt).splitlines() if MEASURED.search(line) and not QUERY_CITED.search(line)][:NUMBERS_SHOWN]
    return evt.allow(system_message="\n".join([UNSOURCED_NUMBER, "", "```", *lines, "```"])) if lines else None


TAP_CLAIM = re.compile(
    r"\b(?:wait(?:s|ing)?\s+(?:only\s+)?(?:on|for)|await(?:s|ing)?|needs?|needing|blocked\s+on|held\s+for)\s+(?:your|his|the\s+owner'?s?)\s+(?:board\s+)?(?:taps?|approvals?|picks?|go-ahead)\b"
    r"|\bon\s+your\s+board\b|\bfor\s+you\s+to\s+(?:tap|approve|pick)\b",
    re.IGNORECASE,
)
CLAIM_NEGATED = re.compile(r"\b(?:no|nothing|none|not|never|without)\b|n't\b", re.IGNORECASE)
CLAIMED_PR = re.compile(r"(?<![\w&])#(\d{4,})\b")
OWNER_WAITS = f"{sys.executable} {script('owner_waits.py')}"
OWNER_WAITS_SECONDS = 30
FALSE_TAP = (
    "Your reply says something waits on the owner's tap, approval or pick, and his board does not show it. "
    "Restate it in plain words from these lines, and quote `cci owner-waits --drive {drive}` for what waits on him:\n```\n{lines}\n```"
)
NOTHING_WAITS = "nothing on his board awaits his tap or pick"
FALSE_PR = json.dumps([{"pr": "45507", "head": "632c4a5", "waiting": False, "line": "#45507 at 632c4a5: not waiting on you: the board holds no row for it; its fidelity grade by fidelity-desk-3 reads missing since 3:02 AM (cci #203600); a PR comment by yasyf answered it at 3:19 AM and no grade has followed"}])
TRUE_PR = json.dumps([{"pr": "45541", "head": "3502b11", "waiting": True, "line": "#45541 at 3502b11: waits on your tap on merge-prs, up since 4:50 AM"}])
CHECK_PR = f"{OWNER_WAITS} --drive brook-drive --json --pr"
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


def owner_waits(drive: str, *args: str) -> list[dict]:
    argv = [sys.executable, str(script("owner_waits.py")), "--drive", drive, "--json", *args]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=OWNER_WAITS_SECONDS, env=dict(reqenv.env_map()), check=True)
    return json.loads(done.stdout)


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests={
        Input(transcript=[T.user("anything for me?"), T.assistant("#45507 waits on your tap, and #45524 waits on it below.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={f"{CHECK_PR} 45507": FALSE_PR}): Block(
            pattern=r"#45507 at 632c4a5: not waiting on you: the board holds no row for it; its fidelity grade .* reads missing since 3:02 AM"
        ),
        Input(transcript=[T.user("anything for me?"), T.assistant("#45541 waits on your tap.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={f"{CHECK_PR} 45541": TRUE_PR}): Allow(),
        Input(transcript=[T.user("anything for me?"), T.assistant("Two fixes are waiting on your approval.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={CHECK_BOARD: "[]"}): Block(pattern=NOTHING_WAITS),
        Input(transcript=[T.user("anything for me?"), T.assistant("The routing card is on your board.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={CHECK_BOARD: TRUE_PR}): Allow(),
        Input(transcript=[T.user("anything for me?"), T.assistant("No PR waits on your tap; #45507 is not waiting on your approval either.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG): Allow(),
        Input(transcript=[T.user("anything for me?"), T.assistant("#45507 waits on your tap.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG, commands={f"{CHECK_PR} 45507": (1, "cc-present down")}): Allow(),
        Input(transcript=[T.user("anything for me?"), T.assistant("#45507 waits on your tap.")], state=DRIVE_ACTIVE, env=DRIVE_CONFIG, agent_id="a1b2c3"): Allow(),
        Input(transcript=[T.user("anything for me?"), T.assistant("#45507 waits on your tap.")], env=DRIVE_CONFIG): Allow(),
    },
)
def tap_claims_match_the_board(evt: BaseHookEvent) -> HookResult | None:
    """A root reply that says a PR or anything else waits on the owner's tap, approval or pick stands only when `cci owner-waits` finds a board row awaiting him at that PR's head, or anything awaiting him for a claim naming no PR; otherwise the reply goes back with the real state to say instead (owner, 4:40 AM Oct 30, #207532). A board that does not read blocks nothing."""
    if not CompactionState.load(evt).active or not (entry := drive_entry(evt)):
        return None
    prs, bare = tap_claims(last_reply(evt))
    if not prs and not bare:
        return None
    drive = bus_name(entry)
    try:
        lines = [found["line"] for found in owner_waits(drive, *(arg for pr in prs for arg in ("--pr", pr))) if not found["waiting"]] if prs else []
        if bare and not owner_waits(drive):
            lines.append(NOTHING_WAITS)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return evt.block(FALSE_TAP.format(lines="\n".join(lines), drive=drive)) if lines else None
