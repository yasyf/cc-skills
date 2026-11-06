from __future__ import annotations

import re
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, T, Tool, Warn, on
from captain_hook.cmd import Cmd

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import COMMUNICATOR_LANE, Bound, Communicator, CompactionState, communicator_tests
from .gate5_release import OWNER_LANES
from .lane_rotation import ROOT, alias_of
from .lane_slack_status import WRITE_TOOLS
from .meta_records import posts
from .owner_facing import CCI_READ, CODE, OWNER_QUOTE, REPLY_WINDOW, SENTENCE_BREAK, SLACK_CARD, URL, fenced, last_reply
from .owner_posts_carry_cards import posted_words
from .owner_returns import REPLY
from .session_tree import drive_lane
from .tests.handoff_fixtures import reply_transcript
from .tests.register_fixtures import DRIVE_CONFIG
from .urgent_lines_name_the_harm import ACTIVE, DM_TOOLS, card, tool_lines, updated

FENCE = re.compile(r"```.*?```", re.DOTALL)
CITE_AFTER_STOP = re.compile(r"([.!?])(\s*<!--.*?-->)", re.DOTALL)
LEAD = re.compile(r"^[\s>*_#-]*(?:\d+\.\s*)?")
MECHANISM = re.compile(
    r"\b(?:because|caus(?:e|ed|es|ing)|due to|mechanism|root[- ]cause|the flaw|which means|is why|led to|skew|so\b[^.;]*?\b(?:fail|crash|break|error)\w*|likely|probably|presumably)\b",
    re.IGNORECASE,
)
PR_CITATION = r"#\d{3,}\b|/pull/\d+|\bPR \d+\b"
RECORD_CITATION = re.compile(
    r"\bbuilds?\s+#?\d{3,}\b|/builds/\d+|[\w./-]+\.\w+:\d+\b|\bmonitors?\s+#?\d+\b|/monitors/\d+|\bnotebooks?\s+#?\d+\b|/notebook/\d+|\bcci #?\d+\b|\b(?:ccn|answer|ruling|note|grant|log|investigation) [0-9a-f]{7,}\b",
    re.IGNORECASE,
)
CITATION = re.compile(f"{PR_CITATION}|{RECORD_CITATION.pattern}", re.IGNORECASE)
PR_NAMED = re.compile(rf"{PR_CITATION}|\bPRs?\b", re.IGNORECASE)
BROKE = re.compile(r"\b(?:broke|breaks|redden\w*|(?:went|turned|goes|turns) red)\b", re.IGNORECASE)
UNPROVEN = "Unproven:"
UNCITED = (
    "Warning, nothing was blocked: A sentence to the owner saying why something happened cites the record, PR, build, file:line, monitor, notebook or ccn id that proves it, or opens with"
    f" `{UNPROVEN}`. Send him a correction now that cites, labels or withdraws each sentence below."
)
UNKNOWN = re.compile(r"\b(?:unanswered|unproven|nobody knows|no one knows)\b", re.IGNORECASE)
UNSEARCHED = (
    "Warning, nothing was blocked: A line to the owner called a question unanswered, unproven or unknown with no `cci grep` of the drive this turn, and a lane may have answered it."
    " Run `cci grep --drive <drive> -- <its terms>` now; if it finds the answer, send him a correction at once."
)
SILENT = re.compile(
    r"\b(?:(?:is|are|was|were|went|gone|goes|been|still|running) (?:quiet|late|silent)|silent (?:lanes?|since|for)|posted nothing|(?<!you )(?:has|have|had)(?:n't| not)(?: yet)? (?:answered|replied|posted)|no (?:answer|reply|word) (?:yet )?from)\b",
    re.IGNORECASE,
)
LANE_NAMED = re.compile(r"\blanes?\b|\b[a-z][a-z0-9]*(?:-[a-z0-9]+)+\b")
UNREAD = (
    "Warning, nothing was blocked: A line to the owner called a lane silent, late or quiet with no read of your mail this turn, and its answer may be waiting for you."
    " Run `cci tail --to <your lane> --since <the seq you asked at>` now; if it shows an answer, send him a correction at once."
)
GREPPED = T.tool_turn("Bash", result="#382682 report schema1-provenance-2 ...", command="cci grep --drive durable-agents-rebuild -- 'which Mac wrote'")
EXAMPLE = "example: The run crashed because the cloud runner reads only migration 0. <!-- cci #384714 -->"
SKEW = (
    "The evidence now says: a normal fleet sand on an FDE Mac writes chats at sessions migration 6 (dc94738, release 12280) and the old-tree cloud runner on plat reads only 0,"
    " so any FDE run that passes a chat to a deployed workflow crashes. That is a release skew between Mac sand and cloud runner, likely ours."
)
CITED = "The FDE run crashed because #49304 has not shipped to the cloud runner."
LABELLED = "Unproven: a release skew between Mac sand and the cloud runner, which a lane inferred."
LATE = "ask-lane-21 posted nothing in 10 minutes past its 4:58 PM clock. I told him it is late, launched ask-lane-22 at 4:59 PM and promised its answer by 5:05 PM."
ANSWERED = "ask-lane-21 answered at 4:52 PM: the link is Sikan's DM about dropping the steps log."
MAILED = T.tool_turn("Bash", result="#513613 answer ask-lane-21>owner-relay It is a plain DM from Sikan He", command="cci tail --drive durable-agents-rebuild --to owner-relay --since 513300")
COORDINATOR_POST = "cci post --drive d --lane root --kind report --to owner-relay --text 'ask-lane-21 is late.'"
ROOT_MAIL = T.tool_turn("Bash", result="#514224 ask owner-relay>root", command="cci tail --drive durable-agents-rebuild --to root --since 513300")


def sentences(text: str) -> list[tuple[str, str]]:
    parts = filter(None, (part.strip() for part in SENTENCE_BREAK.split(CITE_AFTER_STOP.sub(r"\2\1", FENCE.sub("\n", text)))))
    return [(sentence, LEAD.sub("", URL.sub(" ", CODE.sub(" ", OWNER_QUOTE.sub(" ", sentence))))) for sentence in parts]


def uncited(texts: list[str]) -> list[str]:
    found = []
    for text in texts:
        previous = ""
        for sentence, said in sentences(text):
            blames = bool(PR_NAMED.search(sentence) and BROKE.search(said))
            if (blames or MECHANISM.search(said)) and not said.startswith(UNPROVEN) and not (RECORD_CITATION if blames else CITATION).search(f"{previous} {sentence}"):
                found.append(sentence)
            previous = sentence
    return found


def grepped(evt: BaseHookEvent) -> bool:
    return any(
        use.call.name == "Bash" and any(Path(cmd.argv[0]).name == "cci" and "grep" in cmd.argv[1:2] for cmd in use.call.command_line if cmd.argv)
        for use in evt.ctx.turn.tool_calls.items
    )


def refused(evt: BaseHookEvent, texts: list[str]) -> HookResult | None:
    if found := uncited(texts):
        return evt.context(fenced(UNCITED, [*found, EXAMPLE]))
    unsearched = [sentence for text in texts for sentence, said in sentences(text) if UNKNOWN.search(said)]
    return evt.context(fenced(UNSEARCHED, unsearched)) if unsearched and not grepped(evt) else None


def silent_claims(texts: list[str]) -> list[str]:
    found = []
    for text in texts:
        previous = ""
        for sentence, said in sentences(text):
            if SILENT.search(said.replace("*", "")) and LANE_NAMED.search(URL.sub(" ", f"{previous} {sentence}")):
                found.append(sentence)
            previous = sentence
    return found


def own_lanes(evt: BaseHookEvent) -> set[str]:
    state = CompactionState.load(evt)
    return {alias_of(lane) for lane in (drive_lane(evt), COMMUNICATOR_LANE if state.active and state.communicator else None, ROOT if state.active and state.coordinator else None) if lane}


def read_mail(evt: BaseHookEvent) -> bool:
    tails = [
        values
        for use in evt.ctx.turn.tool_calls.items
        if use.call.name == "Bash" and (command := Cmd.parse(use.call.command)) is not None
        for call in command.calls("cci")
        if (values := CCI_READ.bind(call).values).get("words", ())[:1] == ("tail",)
    ]
    own = own_lanes(evt)
    return grepped(evt) or any(values.get("mail") or own & {alias_of(lane) for value in (*values.get("to", ()), *values.get("reader", ())) for lane in str(value).split(",")} for values in tails)


def owner_bound_lines(evt: BaseHookEvent) -> list[str]:
    return [*posted_words(evt), *(text for _, recipients, _, text, _, _ in posts(evt) if OWNER_LANES & {to.split("@", 1)[0] for to in recipients})]


def unread(evt: BaseHookEvent, texts: list[str]) -> HookResult | None:
    found = silent_claims(texts)
    return evt.context(fenced(UNREAD, found)) if found and not read_mail(evt) else None


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", SLACK_CARD, *DM_TOOLS)],
    tests={
        card(title="Why did the FDE run crash?", body=SKEW): Warn(pattern=r"^Warning, nothing was blocked: A sentence to the owner saying why[\s\S]*migration 6[\s\S]*That is a release skew[\s\S]*example: "),
        card(title="Why did the FDE run crash?", body=CITED): Allow(),
        card(title="Why did the FDE run crash?", body="The FDE run crashed because the runner reads only format 0. <!-- cci #384714 -->"): Allow(),
        card(title="Why did the FDE run crash?", body="Build 12280 ships the old reader. So every FDE run on it fails at the first chat."): Allow(),
        card(title="Why did the FDE run crash?", body=LABELLED): Warn(pattern=r"^Warning, nothing was blocked: A line to the owner called a question unanswered[\s\S]*Unproven: a release skew"),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Why did the FDE run crash?", "body": LABELLED}}, env=DRIVE_CONFIG, transcript=[T.user("why"), *GREPPED]): Allow(),
        card(title="Which Mac wrote it?", body="Nobody knows which Mac wrote the chat."): Warn(pattern=r"Nobody knows which Mac"),
        card(title="Why is dev red?", body="#49475 broke dev's sand-next vet job."): Warn(pattern=r"#49475 broke dev's sand-next vet job\."),
        card(title="Why is dev red?", body="#49475 broke dev's sand-next vet job: build 12290 logs its missing import."): Allow(),
        card(title="Did the lane retract it?", body='It withdrew "a release skew caused it" and reads the chat rows now.'): Allow(),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Why?", "body": SKEW}}): Allow(),
        Input(command=updated({"type": "card", "id": "skew", "title": "Why did the FDE run crash?", "summary": SKEW}), env=DRIVE_CONFIG): Warn(pattern=r"That is a release skew"),
        Input(command="cc-present reply --block skew --body 'It crashed because of a release skew.'", env=DRIVE_CONFIG): Warn(pattern=r"It crashed because of a release skew\."),
        Input(command=f"cc-present reply --block skew --body '{CITED}'", env=DRIVE_CONFIG): Allow(),
        Input(tool=DM_TOOLS[0], tool_input={"channel_id": "D0B6Z", "thread_ts": "1.2", "text": "It crashed because of a release skew."}, state=ACTIVE, env=DRIVE_CONFIG): Warn(),
        Input(tool=DM_TOOLS[0], tool_input={"channel_id": "D0B6Z", "thread_ts": "1.2", "text": "It crashed because of a release skew."}, env=DRIVE_CONFIG): Allow(),
    },
)
def mechanism_lines_cite(evt: BaseHookEvent) -> HookResult | None:
    return refused(evt, tool_lines(evt))


@on(
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests=communicator_tests({
        Input(transcript=reply_transcript(SKEW), state=ACTIVE): Warn(pattern=r"writes chats at sessions migration 6[\s\S]*That is a release skew"),
        Input(transcript=reply_transcript(f"{CITED} The fix is up as `go/sand/internal/vm/chat.go:88`, which reads migration 6."), state=ACTIVE): Allow(),
        Input(transcript=reply_transcript(LABELLED), state=ACTIVE): Warn(pattern=r"^Warning, nothing was blocked: A line to the owner called a question unanswered"),
        Input(transcript=[T.user("which Mac wrote it?"), *GREPPED, T.assistant("Which Mac wrote the chat is still unanswered; no lane record names it.")], state=ACTIVE): Allow(),
    }),
)
def mechanism_replies_cite(evt: BaseHookEvent) -> HookResult | None:
    return refused(evt, [last_reply(evt)])


def replied(md: str, **extra: object) -> Input:
    return Input(tool=REPLY, tool_input={"title": "Who added the steps log?", "md": md}, state=ACTIVE, env=DRIVE_CONFIG, **extra)


def recorded(to: str, text: str) -> Input:
    return Input(command=f"cci post --drive d --lane flaky-tests-2 --kind report --to {to} --text '{text}'", env=DRIVE_CONFIG)


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", REPLY, POST_TOOL, *WRITE_TOOLS)],
    tests={
        replied(LATE): Warn(pattern=r"^Warning, nothing was blocked: A line to the owner called a lane silent, late or quiet[\s\S]*ask-lane-21 posted nothing[\s\S]*I told him it is late"),
        replied(LATE, transcript=[T.user("who added it?"), *MAILED]): Allow(),
        replied(LATE, transcript=[T.user("who added it?"), *GREPPED]): Allow(),
        replied(LATE, transcript=[T.user("who added it?"), *ROOT_MAIL]): Warn(pattern=r"ask-lane-21 posted nothing"),
        replied("`ask-lane-21` is late."): Warn(),
        replied(ANSWERED): Allow(),
        replied("The release is late; its card says it cuts at 6 PM."): Allow(),
        replied("You have not answered the land-gate card."): Allow(),
        recorded("owner-relay", "tap-rows has not answered since 3 PM."): Warn(pattern=r"tap-rows has not answered"),
        recorded("root", "tap-rows has not answered since 3 PM."): Allow(),
        replied("ask-lane-21 is not silent; it answered at 4:52 PM."): Allow(),
        replied("ask-lane-21 has not reported any errors."): Allow(),
        replied("ask-lane-21 posted **nothing** in 10 minutes."): Warn(pattern=r"posted \*\*nothing\*\*"),
        replied("ask-lane-22 has been silent for 10 minutes."): Warn(pattern=r"ask-lane-22 has been silent"),
        replied("The ship --amend body refusal on a Sprite is relay-link-read's hook fix: it stays silent on --amend and on a branch that already has an open PR."): Allow(),
        replied("The read check stays quiet on a thread the owner wrote in, so owner-relay reads it inline."): Allow(),
        Input(command=COORDINATOR_POST, state=[CompactionState(active=True, coordinator=True)], env=DRIVE_CONFIG): Warn(pattern=r"ask-lane-21 is late"),
        Input(command=COORDINATOR_POST, state=[CompactionState(active=True, coordinator=True)], env=DRIVE_CONFIG, transcript=[T.user("where is it?"), *ROOT_MAIL]): Allow(),
        Input(tool=DM_TOOLS[1], tool_input={"channel_id": "D0B6Z", "text": "tap-rows went quiet an hour ago."}, env=DRIVE_CONFIG): Warn(),
        Input(tool=DM_TOOLS[1], tool_input={"channel_id": "C0RELEASES1", "text": "tap-rows went quiet an hour ago."}, env=DRIVE_CONFIG): Allow(),
    },
)
def silent_lane_lines_read_mail(evt: BaseHookEvent) -> HookResult | None:
    return unread(evt, owner_bound_lines(evt))


@on(
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests=communicator_tests({
        Input(transcript=reply_transcript(LATE), state=ACTIVE): Warn(pattern=r"^Warning, nothing was blocked: A line to the owner called a lane silent[\s\S]*ask-lane-21 posted nothing"),
        Input(transcript=[T.user("is it back?"), *MAILED, T.assistant(LATE)], state=ACTIVE): Allow(),
        Input(transcript=reply_transcript(ANSWERED), state=ACTIVE): Allow(),
    }),
)
def silent_lane_replies_read_mail(evt: BaseHookEvent) -> HookResult | None:
    return unread(evt, [last_reply(evt)])
