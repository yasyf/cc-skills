from __future__ import annotations

import re
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, T, Tool, on

from .compaction_handoff import Communicator, communicator_tests
from .owner_facing import CODE, OWNER_QUOTE, REPLY_WINDOW, SENTENCE_BREAK, SLACK_CARD, URL, fenced, last_reply
from .tests.handoff_fixtures import reply_transcript
from .tests.register_fixtures import DRIVE_CONFIG
from .urgent_lines_name_the_harm import ACTIVE, DM_TOOLS, card, tool_lines, updated

FENCE = re.compile(r"```.*?```", re.DOTALL)
LEAD = re.compile(r"^[\s>*_#-]*(?:\d+\.\s*)?")
MECHANISM = re.compile(
    r"\b(?:because|caus(?:e|ed|es|ing)|due to|mechanism|root[- ]cause|the flaw|which means|is why|led to|skew|so\b[^.;]*?\b(?:fail|crash|break|error)\w*|likely|probably|presumably)\b",
    re.IGNORECASE,
)
CITATION = re.compile(
    r"#\d{3,}\b|/pull/\d+|\bPR \d+\b|\bbuilds?\s+#?\d{3,}\b|/builds/\d+|[\w./-]+\.\w+:\d+\b|\bmonitors?\s+#?\d+\b|/monitors/\d+|\bnotebooks?\s+#?\d+\b|/notebook/\d+|\b(?:ccn|answer|ruling|note|grant|log|investigation) [0-9a-f]{7,}\b",
    re.IGNORECASE,
)
UNPROVEN = "Unproven:"
UNCITED_REFUSAL = (
    "A sentence to the owner that says why something happened cites, in it or the sentence before, the record, PR, build, file:line, monitor, notebook or ccn id that proves it,"
    f" or opens with `{UNPROVEN}`; otherwise it is not written. Cite, label or drop each sentence below, then send it again."
)
UNKNOWN = re.compile(r"\b(?:unanswered|unproven|nobody knows|no one knows)\b", re.IGNORECASE)
UNSEARCHED = (
    "A line to the owner calling a question unanswered, unproven or unknown follows a `cci grep` of the drive for it this turn; a lane may have answered it."
    " Run `cci grep --drive <drive> -- <its terms>`, cite what it finds or keep the label, then send it again."
)
GREPPED = T.tool_turn("Bash", result="#382682 report schema1-provenance-2 ...", command="cci grep --drive durable-agents-rebuild -- 'which Mac wrote'")
EXAMPLE = "example: The run crashed because the cloud runner reads only migration 0 (#384714)."
SKEW = (
    "The evidence now says: a normal fleet sand on an FDE Mac writes chats at sessions migration 6 (dc94738, release 12280) and the old-tree cloud runner on plat reads only 0,"
    " so any FDE run that passes a chat to a deployed workflow crashes. That is a release skew between Mac sand and cloud runner, likely ours."
)
CITED = "The FDE run crashed because #49304 has not shipped to the cloud runner."
LABELLED = "Unproven: a release skew between Mac sand and the cloud runner, which a lane inferred."


def sentences(text: str) -> list[tuple[str, str]]:
    parts = filter(None, (part.strip() for part in SENTENCE_BREAK.split(FENCE.sub("\n", text))))
    return [(sentence, LEAD.sub("", URL.sub(" ", CODE.sub(" ", OWNER_QUOTE.sub(" ", sentence))))) for sentence in parts]


def uncited(texts: list[str]) -> list[str]:
    found = []
    for text in texts:
        previous = ""
        for sentence, said in sentences(text):
            if MECHANISM.search(said) and not said.startswith(UNPROVEN) and not CITATION.search(f"{previous} {sentence}"):
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
        return evt.block(fenced(UNCITED_REFUSAL, [*found, EXAMPLE]))
    unsearched = [sentence for text in texts for sentence, said in sentences(text) if UNKNOWN.search(said)]
    return evt.block(fenced(UNSEARCHED, unsearched)) if unsearched and not grepped(evt) else None


@on(  # capt-hook: no-escape the owner ordered a mechanism sentence to him that cites no record refused
    Event.PreToolUse,
    only_if=[Tool("Bash", SLACK_CARD, *DM_TOOLS)],
    tests={
        card(title="Why did the FDE run crash?", body=SKEW): Block(pattern=r"^A sentence to the owner that says why[\s\S]*migration 6[\s\S]*That is a release skew[\s\S]*example: "),
        card(title="Why did the FDE run crash?", body=CITED): Allow(),
        card(title="Why did the FDE run crash?", body="Build 12280 ships the old reader. So every FDE run on it fails at the first chat."): Allow(),
        card(title="Why did the FDE run crash?", body=LABELLED): Block(pattern=r"^A line to the owner calling a question unanswered[\s\S]*Unproven: a release skew"),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Why did the FDE run crash?", "body": LABELLED}}, env=DRIVE_CONFIG, transcript=[T.user("why"), *GREPPED]): Allow(),
        card(title="Which Mac wrote it?", body="Nobody knows which Mac wrote the chat."): Block(pattern=r"Nobody knows which Mac"),
        card(title="Did the lane retract it?", body='It withdrew "a release skew caused it" and reads the chat rows now.'): Allow(),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Why?", "body": SKEW}}): Allow(),
        Input(command=updated({"type": "card", "id": "skew", "title": "Why did the FDE run crash?", "summary": SKEW}), env=DRIVE_CONFIG): Block(pattern=r"That is a release skew"),
        Input(command="cc-present reply --block skew --body 'It crashed because of a release skew.'", env=DRIVE_CONFIG): Block(pattern=r"It crashed because of a release skew\."),
        Input(command=f"cc-present reply --block skew --body '{CITED}'", env=DRIVE_CONFIG): Allow(),
        Input(tool=DM_TOOLS[0], tool_input={"channel_id": "D0B6Z", "thread_ts": "1.2", "text": "It crashed because of a release skew."}, state=ACTIVE, env=DRIVE_CONFIG): Block(),
        Input(tool=DM_TOOLS[0], tool_input={"channel_id": "D0B6Z", "thread_ts": "1.2", "text": "It crashed because of a release skew."}, env=DRIVE_CONFIG): Allow(),
    },
)
def mechanism_lines_cite(evt: BaseHookEvent) -> HookResult | None:
    return refused(evt, tool_lines(evt))


@on(  # capt-hook: no-escape the owner ordered a mechanism sentence to him that cites no record refused
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests=communicator_tests({
        Input(transcript=reply_transcript(SKEW), state=ACTIVE): Block(pattern=r"writes chats at sessions migration 6[\s\S]*That is a release skew"),
        Input(transcript=reply_transcript(f"{CITED} The fix is up as `go/sand/internal/vm/chat.go:88`, which reads migration 6."), state=ACTIVE): Allow(),
        Input(transcript=reply_transcript(LABELLED), state=ACTIVE): Block(pattern=r"^A line to the owner calling a question unanswered"),
        Input(transcript=[T.user("which Mac wrote it?"), *GREPPED, T.assistant("Which Mac wrote the chat is still unanswered; no lane record names it.")], state=ACTIVE): Allow(),
    }),
)
def mechanism_replies_cite(evt: BaseHookEvent) -> HookResult | None:
    return refused(evt, [last_reply(evt)])
