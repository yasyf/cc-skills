from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

from cc_transcript.models import AttachmentEvent, UserEvent
from captain_hook import (
    Allow,
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

from .compaction_handoff import CompactionState
from .owner_facing import CCI_POST, incident_sources, last_reply, open_incidents, record_text
from .tests.incident_fixtures import CLOSED_0820, SAND_NOT_MIGRATED

ACTIVE = [CompactionState(active=True)]
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
    r"\b(?:is|was|looks|sat|sits|went)\s+(?:idle|silent|dead|stuck|empty)\b|[\w.-]:\s*(?:idle|silent|dead|stuck|empty)\b"
    r"|\bno (?:code|commits|work|progress)\b|\b(?:did|has done|done) nothing\b"
    r"|\bst(?:and(?:s|ing)?|ood)\s+(?:\S+\s+)?down\b|\brelaunch(?:ed|ing)?\b",
    re.IGNORECASE,
)
UNREAD_LANE = (
    "Warning, nothing was blocked: A claim that a lane is idle, dead, or done nothing, or a stand-down or relaunch, follows `drive.py lane-status <lane>` in the same turn."
    " Run it, then report what it shows."
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


def lane_status_read(evt: BaseHookEvent, lanes: set[str]) -> bool:
    reads = [set(args[1:]) for argv in turn_commands(evt) if (args := script_args(argv, frozenset({"drive.py"}))) and args[0] == "lane-status"]
    return bool(reads) and all(any(lane in read for read in reads) for lane in lanes)


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
        ): Warn(pattern=r"^Warning, nothing was blocked[\s\S]*OWNER 4:11 PM"),
        Input(
            transcript=replied(owner_said("stop all cuts now", "2026-01-15T00:11:00Z"), reply='Logged OWNER 4:11 PM "stop all cuts now" as a hold.'),
            state=ACTIVE,
        ): Allow(),
        Input(
            transcript=replied(owner_said("stop all cuts now", "2026-01-15T00:11:30Z"), reply="Logged OWNER 4:12 PM as a hold."),
            state=ACTIVE,
        ): Allow(),
        Input(
            transcript=replied(
                owner_said("stop all cuts now", "2026-01-15T00:11:00Z"),
                owner_said("ship it", "2026-01-15T00:30:00Z"),
                reply='Logged OWNER 4:30 PM "stop all cuts now" as a hold.',
            ),
            state=ACTIVE,
        ): Warn(pattern=r"OWNER 4:11 PM"),
        Input(transcript=replied(*MID_TURN, reply='Recorded OWNER 12:21 PM "post the board once the fix lands".'), state=ACTIVE): Allow(),
        Input(transcript=replied(owner_said("ship it", "2026-01-15T00:11:00Z"), reply="The fix lane is building."), state=ACTIVE): Allow(),
        Input(transcript=replied(owner_said("ship it", "2026-01-15T00:11:00Z"), reply="Logged OWNER 7:58 PM as a hold.")): Allow(),
    },
)
def owner_labels_carry_the_turn_stamp(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active or not (warning := relabeled(evt, lambda: [last_reply(evt)])):
        return None
    return evt.warn(warning)


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
        Input(transcript=[T.user("status?"), T.assistant("worker-fix-2 is idle with no code; relaunching it.")], state=ACTIVE): Warn(
            pattern=r"drive\.py lane-status <lane>"
        ),
        Input(transcript=[T.user("status?"), T.assistant("worker-fix-2 is empty.")], state=ACTIVE): Warn(),
        Input(transcript=[T.user("status?"), T.assistant("worker-fix-2: idle")], state=ACTIVE): Warn(),
        Input(
            transcript=[
                T.user("status?"),
                T.assistant(T.tool("Bash", command="drive.py lane-status worker-fix-2 --drive release-v3")),
                T.assistant("checked."),
                T.user("and now?"),
                T.assistant("worker-fix-2 is idle; relaunching it."),
            ],
            state=ACTIVE,
        ): Warn(),
        Input(transcript=[T.user("status?"), T.assistant("worker-fix-2 is idle; relaunching it.")], state=ACTIVE): Warn(),
        Input(
            transcript=[
                T.user("status?"),
                T.assistant(T.tool("Bash", command="drive.py lane-status worker-fix-2 --drive release-v3")),
                T.assistant("worker-fix-2 is idle; relaunching it."),
            ],
            state=ACTIVE,
        ): Allow(),
        Input(transcript=[T.user("status?"), T.assistant("The fix lane opened its PR.")], state=ACTIVE): Allow(),
        Input(transcript=[T.user("status?"), T.assistant("worker-fix-2 is idle.")]): Allow(),
    },
)
def idle_claims_read_lane_status(evt: BaseHookEvent) -> HookResult | None:
    if CompactionState.load(evt).active and IDLE_CLAIM.search(last_reply(evt)) and not lane_status_read(evt, set()):
        return evt.context(UNREAD_LANE)
    return None


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash", "SendMessage")],
    skip_if=[FromSubagent()],
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
    tests={
        Input(
            transcript=[T.user("why is it slow"), T.assistant("My bad: the runner was broken and I missed the stalled launch.")],
            state=ACTIVE,
            commands=incident_sources(SAND_NOT_MIGRATED),
        ): Warn(pattern=r"ships the fix in the same turn"),
        Input(
            transcript=[
                T.user("why is it slow"),
                T.assistant(T.tool("Agent", name="runner-stall-fix", prompt="Fix the stalled launch.")),
                T.assistant("My bad: the runner was broken; runner-stall-fix is on it."),
            ],
            state=ACTIVE,
            commands=incident_sources(SAND_NOT_MIGRATED),
        ): Allow(),
        Input(
            transcript=[
                T.user("why is it slow"),
                T.assistant(T.tool("Agent", name="pr-review-4", prompt="Review the open PR.")),
                T.assistant("My bad: the runner was broken and I missed the stalled launch."),
            ],
            state=ACTIVE,
            commands=incident_sources(SAND_NOT_MIGRATED),
        ): Warn(),
        Input(
            transcript=[T.user("why is it slow"), T.assistant("My bad: the runner was broken and I missed the stalled launch.")],
            state=ACTIVE,
            commands=incident_sources(CLOSED_0820),
        ): Allow(),
        Input(
            transcript=[T.user("why is it slow"), T.assistant("The fix lane opened its PR.")],
            state=ACTIVE,
            commands=incident_sources(SAND_NOT_MIGRATED),
        ): Allow(),
    },
)
def narrated_defects_ship_their_fix(evt: BaseHookEvent) -> HookResult | None:
    reply = last_reply(evt)
    if CompactionState.load(evt).active and DEFECT_ADMITTED.search(reply) and not fix_named(evt, reply) and open_incidents(evt):
        return evt.context(NARRATED_DEFECT)
    return None


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests={
        Input(transcript=[T.user("how bad?"), T.assistant("About 300 requests per minute fail, and 12 runs crashed.")], state=ACTIVE): Warn(
            pattern=r"300 requests per minute"
        ),
        Input(transcript=[T.user("how bad?"), T.assistant("15 runs crashed in the last hour (#104701).")], state=ACTIVE): Allow(),
        Input(transcript=[T.user("how bad?"), T.assistant("15 runs crashed in the last hour.")], state=ACTIVE): Warn(),
        Input(transcript=[T.user("status?"), T.assistant("Three PRs are open and two lanes are working.")], state=ACTIVE): Allow(),
        Input(transcript=[T.user("how bad?"), T.assistant("15 runs crashed.")]): Allow(),
    },
)
def owner_numbers_cite_their_query(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    lines = [line.strip()[:NUMBER_TEXT_SHOWN] for line in last_reply(evt).splitlines() if MEASURED.search(line) and not QUERY_CITED.search(line)][:NUMBERS_SHOWN]
    return evt.context("\n".join([UNSOURCED_NUMBER, "", "```", *lines, "```"])) if lines else None
