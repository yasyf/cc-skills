from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from cc_transcript.models import UserEvent
from captain_hook import (
    CONFIRMED,
    UNCONFIRMED,
    Allow,
    BaseHookEvent,
    Block,
    Confirm,
    Event,
    FromSubagent,
    HookResult,
    Input,
    RanCommand,
    Regex,
    T,
    Tool,
    UsedTool,
    on,
)

from .compaction_handoff import CompactionState
from .owner_facing import CCI_POST, incident_sources, last_reply, open_incidents, record_text
from .tests.incident_fixtures import CLOSED_0820, SAND_NOT_MIGRATED

ACTIVE = [CompactionState(active=True)]
REPLY_WINDOW = 256
PACIFIC = ZoneInfo("America/Los_Angeles")
MINUTES_A_DAY = 24 * 60
LABEL_SLACK_MINUTES = 1
QUOTE_PREFIX = 30
OWNER_LABEL = re.compile(r"\bowner(?:'s)?,?\s+(?:at\s+)?(\d{1,2}):(\d{2})(?::\d{2})?\s*([ap])\.?m\b\.?(?:[^\"“\n]{0,12}[\"“]([^\"”\n]+))?", re.IGNORECASE)
SYSTEM_PREFIXES = ("<", "[Request interrupted", "This session is being continued")
MISLABELED = (
    "An `OWNER h:mm` label carries the stamp of the owner turn it quotes, read from the transcript."
    " Relabel each one below with the time shown, then send again."
)
LANE_STATUS = RanCommand(Regex(r"\bdrive\.py\s+lane-status\b"))
IDLE_CLAIM = re.compile(
    r"\b(?:is|was|looks|sat|sits|went)\s+(?:idle|silent|dead|stuck)\b|\bno (?:code|commits|work|progress)\b|\b(?:did|has done|done) nothing\b"
    r"|\bst(?:and(?:s|ing)?|ood)\s+(?:\S+\s+)?down\b|\brelaunch(?:ed|ing)?\b",
    re.IGNORECASE,
)
UNREAD_LANE = (
    "A claim that a lane is idle, dead, or done nothing, or a stand-down or relaunch, follows `drive.py lane-status <lane>` in the same turn."
    " Run it, then report what it shows."
)
IDLE_JUDGE = Confirm(
    rule="Block only when the reply calls a named lane idle, silent, dead, or empty, or stands one down or relaunches it, with no lane-status read this turn."
)
FIX_SPAWNED = [UsedTool("Agent"), RanCommand(Regex(r"\b(?:orca-launch\.sh|desk-launch\.py)\b|\bcci\s+post\b[^\n]*--kind[ =]go\b"))]
DEFECT_ADMITTED = re.compile(
    r"\bmy bad\b|\bI (?:missed|forgot|should have|didn'?t)\b|\b(?:tooling|tool|hook|runner|desk)\s+(?:defect|bug|gap|is broken|was broken|misfired)\b"
    r"|\bdead lane\b|\blane (?:is|was) dead\b|\bmissed (?:a|the) step\b|\bstep (?:was )?skipped\b",
    re.IGNORECASE,
)
NARRATED_DEFECT = (
    "A reply that admits a tooling defect, a missed step, or a dead lane ships the fix in the same turn."
    " Spawn the fix lane or post its `cci post --kind go`, then report it."
)
DEFECT_JUDGE = Confirm(rule="Block only when the reply admits the drive's own tooling defect, missed step, or dead lane and names no fix lane or GO for it.")
MEASURED = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:%|percent\b|(?:users?|sessions?|runs?|errors?|crashes|failures?|requests?|events?|objects?|rows?|retires?|deletes?|refusals?|chats?|reports?)\b|(?:/|per\s+)(?:min(?:ute)?|hour|sec(?:ond)?|s)\b)",
    re.IGNORECASE,
)
QUERY_CITED = re.compile(r"#\d{3,}\b|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b|https?://\S+")
UNSOURCED_NUMBER = (
    "A count, rate, or percentage told to the owner names the query record it came from."
    " Put that record's `#<seq>` or link beside each number below, or drop the number."
)
NUMBER_JUDGE = Confirm(rule="Block only when the reply states a measured production count, rate, or percentage with no record, query, or link it came from.")


def owner_turns(evt: BaseHookEvent) -> list[tuple[datetime, str]]:
    return [
        (event.meta.timestamp.astimezone(PACIFIC), event.text)
        for event in evt.ctx.t.events
        if isinstance(event, UserEvent)
        and not event.meta.is_sidechain
        and not event.is_agent_injected
        and event.text
        and not event.text.lstrip().startswith(SYSTEM_PREFIXES)
    ]


def minute_of_day(hour: int, minute: int, half: str) -> int:
    return (hour % 12 + (12 if half.casefold() == "p" else 0)) * 60 + minute


def apart(first: int, second: int) -> int:
    gap = abs(first - second) % MINUTES_A_DAY
    return min(gap, MINUTES_A_DAY - gap)


def stamp(at: datetime) -> str:
    return at.strftime("%-I:%M %p")


def mislabeled(evt: BaseHookEvent, text: str) -> list[str]:
    turns = owner_turns(evt) if OWNER_LABEL.search(text) else []
    wrong = []
    for match in OWNER_LABEL.finditer(text) if turns else ():
        labeled = minute_of_day(int(match[1]), int(match[2]), match[3])
        if any(apart(labeled, at.hour * 60 + at.minute) <= LABEL_SLACK_MINUTES for at, _ in turns):
            continue
        quoted = (match[4] or "").strip().casefold()[:QUOTE_PREFIX]
        source = next((at for at, words in reversed(turns) if quoted and quoted in words.casefold()), turns[-1][0])
        wrong.append(f"OWNER {match[1]}:{match[2]} {match[3].upper()}M -> OWNER {stamp(source)}")
    return wrong


def relabel(wrong: list[str]) -> str:
    return "\n".join([MISLABELED, "", "```", *wrong, "```"])


def owner_said(text: str, at: str) -> dict:
    return T.user(text, timestamp=at)


def replied(*events: object, reply: str) -> list[object]:
    return [*events, T.assistant(reply)]


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests={
        Input(
            transcript=replied(owner_said("stop all cuts now", "2026-01-15T00:11:00Z"), reply='Logged OWNER 7:58 PM "stop all cuts now" as a hold.'),
            state=ACTIVE,
        ): Block(pattern=r"OWNER 4:11 PM"),
        Input(
            transcript=replied(owner_said("stop all cuts now", "2026-01-15T00:11:00Z"), reply='Logged OWNER 4:11 PM "stop all cuts now" as a hold.'),
            state=ACTIVE,
        ): Allow(),
        Input(
            transcript=replied(owner_said("stop all cuts now", "2026-01-15T00:11:30Z"), reply="Logged OWNER 4:12 PM as a hold."),
            state=ACTIVE,
        ): Allow(),
        Input(transcript=replied(owner_said("ship it", "2026-01-15T00:11:00Z"), reply="The fix lane is building."), state=ACTIVE): Allow(),
        Input(transcript=replied(owner_said("ship it", "2026-01-15T00:11:00Z"), reply="Logged OWNER 7:58 PM as a hold.")): Allow(),
    },
)
def owner_labels_carry_the_turn_stamp(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active or not (wrong := mislabeled(evt, last_reply(evt))):
        return None
    return evt.block(relabel(wrong))


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    skip_if=[FromSubagent()],
    tests={
        Input(
            command="cci post --drive d --lane root --kind hold --text 'OWNER 7:58 PM: stop all cuts now'",
            transcript=[owner_said("stop all cuts now", "2026-01-15T00:11:00Z")],
            state=ACTIVE,
        ): Block(pattern=r"OWNER 4:11 PM"),
        Input(
            command="cci post --drive d --lane root --kind hold --text 'OWNER 4:11 PM: stop all cuts now'",
            transcript=[owner_said("stop all cuts now", "2026-01-15T00:11:00Z")],
            state=ACTIVE,
        ): Allow(),
        Input(command="cci post --drive d --lane root --kind hold --text 'cuts held'", transcript=[owner_said("stop", "2026-01-15T00:11:00Z")], state=ACTIVE): Allow(),
    },
)
def owner_records_carry_the_turn_stamp(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    posts = [record_text(values) for call in evt.command.calls("cci") if (values := CCI_POST.bind(call).values).get("words", ())[:1] == ("post",)]
    wrong = [line for text in posts for line in mislabeled(evt, text)]
    return evt.block(relabel(wrong)) if wrong else None


@on(
    Event.Stop,
    skip_if=[FromSubagent(), LANE_STATUS],
    transcript_events=REPLY_WINDOW,
    tests={
        Input(transcript=[T.user("status?"), T.assistant("worker-fix-2 is idle with no code; relaunching it.")], state=ACTIVE, decide=CONFIRMED): Block(
            pattern=r"drive\.py lane-status <lane>"
        ),
        Input(transcript=[T.user("status?"), T.assistant("worker-fix-2 is idle; relaunching it.")], state=ACTIVE, decide=UNCONFIRMED): Allow(),
        Input(
            transcript=[
                T.user("status?"),
                T.assistant(T.tool("Bash", command="drive.py lane-status worker-fix-2 --drive release-v3")),
                T.assistant("worker-fix-2 is idle; relaunching it."),
            ],
            state=ACTIVE,
            decide=CONFIRMED,
        ): Allow(),
        Input(transcript=[T.user("status?"), T.assistant("The fix lane opened its PR.")], state=ACTIVE, decide=CONFIRMED): Allow(),
        Input(transcript=[T.user("status?"), T.assistant("worker-fix-2 is idle.")], decide=CONFIRMED): Allow(),
    },
)
def idle_claims_read_lane_status(evt: BaseHookEvent) -> HookResult | None:
    if CompactionState.load(evt).active and IDLE_CLAIM.search(last_reply(evt)):
        return evt.block(UNREAD_LANE, confirm=IDLE_JUDGE)
    return None


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash", "SendMessage")],
    skip_if=[FromSubagent(), LANE_STATUS],
    tests={
        Input(command="cci post --drive d --lane root --kind stand-down --to worker-fix-2 --text 'stand down'", state=ACTIVE): Block(
            pattern=r"drive\.py lane-status <lane>"
        ),
        Input(
            command="cci post --drive d --lane root --kind stand-down --to worker-fix-2 --text 'stand down'",
            transcript=[T.user("status?"), T.assistant(T.tool("Bash", command="drive.py lane-status worker-fix-2 --drive d"))],
            state=ACTIVE,
        ): Allow(),
        Input(tool="SendMessage", tool_input={"to": "worker-fix-2", "message": "Stand down: stop working and send nothing further."}, state=ACTIVE): Block(),
        Input(command="cci post --drive d --lane root --kind go --to worker-fix-2 --text 'ship it'", state=ACTIVE): Allow(),
        Input(command="cci post --drive d --lane root --kind stand-down --to x --text 'stand down'"): Allow(),
    },
)
def stand_downs_read_lane_status(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    if evt.tool_name == "SendMessage":
        message = evt.tool_input.get("message")
        return evt.block(UNREAD_LANE) if isinstance(message, str) and re.match(r"\s*stand[ -]down\b", message, re.IGNORECASE) else None
    kinds = [set(CCI_POST.bind(call).values.get("kind", ())) for call in evt.command.calls("cci")]
    return evt.block(UNREAD_LANE) if any("stand-down" in kind for kind in kinds) else None


@on(
    Event.Stop,
    skip_if=[FromSubagent(), *FIX_SPAWNED],
    transcript_events=REPLY_WINDOW,
    tests={
        Input(
            transcript=[T.user("why is it slow"), T.assistant("My bad: the runner was broken and I missed the stalled launch.")],
            state=ACTIVE,
            decide=CONFIRMED,
            commands=incident_sources(SAND_NOT_MIGRATED),
        ): Block(pattern=r"ships the fix in the same turn"),
        Input(
            transcript=[
                T.user("why is it slow"),
                T.assistant(T.tool("Agent", name="runner-stall-fix", prompt="Fix the stalled launch.")),
                T.assistant("My bad: the runner was broken; runner-stall-fix is on it."),
            ],
            state=ACTIVE,
            decide=CONFIRMED,
            commands=incident_sources(SAND_NOT_MIGRATED),
        ): Allow(),
        Input(
            transcript=[T.user("why is it slow"), T.assistant("My bad: the runner was broken and I missed the stalled launch.")],
            state=ACTIVE,
            decide=CONFIRMED,
            commands=incident_sources(CLOSED_0820),
        ): Allow(),
        Input(
            transcript=[T.user("why is it slow"), T.assistant("The fix lane opened its PR.")],
            state=ACTIVE,
            decide=CONFIRMED,
            commands=incident_sources(SAND_NOT_MIGRATED),
        ): Allow(),
    },
)
def narrated_defects_ship_their_fix(evt: BaseHookEvent) -> HookResult | None:
    if CompactionState.load(evt).active and DEFECT_ADMITTED.search(last_reply(evt)) and open_incidents(evt):
        return evt.block(NARRATED_DEFECT, confirm=DEFECT_JUDGE)
    return None


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests={
        Input(transcript=[T.user("how bad?"), T.assistant("About 300 requests per minute fail, and 12 runs crashed.")], state=ACTIVE, decide=CONFIRMED): Block(
            pattern=r"300 requests per minute"
        ),
        Input(transcript=[T.user("how bad?"), T.assistant("15 runs crashed in the last hour (#104701).")], state=ACTIVE, decide=CONFIRMED): Allow(),
        Input(transcript=[T.user("how bad?"), T.assistant("15 runs crashed in the last hour.")], state=ACTIVE, decide=UNCONFIRMED): Allow(),
        Input(transcript=[T.user("status?"), T.assistant("Three PRs are open and two lanes are working.")], state=ACTIVE, decide=CONFIRMED): Allow(),
        Input(transcript=[T.user("how bad?"), T.assistant("15 runs crashed.")], decide=CONFIRMED): Allow(),
    },
)
def owner_numbers_cite_their_query(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    lines = [line.strip() for line in last_reply(evt).splitlines() if MEASURED.search(line) and not QUERY_CITED.search(line)]
    return evt.block("\n".join([UNSOURCED_NUMBER, "", "```", *lines, "```"]), confirm=NUMBER_JUDGE) if lines else None
