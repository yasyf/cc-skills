from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, Tool, on

from .compaction_handoff import Bound, Communicator, CompactionState, communicator_tests
from .owner_facing import REPLY_WINDOW, SENTENCE_BREAK, fenced, last_reply
from .owner_returns import REPLY
from .tests.handoff_fixtures import reply_transcript
from .urgent_lines_name_the_harm import ACTIVE

BOUND = [CompactionState(active=True, coordinator=True)]

FORMAT_NUMBER = re.compile(r"\b(?:format|sessions head(?: to)?|migrat(?:e|es|ed|ing) to)\s+(\d+)\b", re.IGNORECASE)
FORMAT_REFUSAL = (
    "A chat format reaches the owner as format 0 or format 1 only; a sessions migration number is internal."
    " Rewrite each line below with format 0, format 1 or plain words for the change, then send it again."
)
INTERNAL = "Old Macs crash on chats at sessions format 6, so we migrate to 7 tonight."
PLAIN = "Old Macs cannot open format 1 chats, so they show the update screen; the format of the table is unchanged."


def internal_formats(texts: list[str]) -> list[str]:
    return [line.strip() for text in texts for line in SENTENCE_BREAK.split(text) if any(number not in {"0", "1"} for number in FORMAT_NUMBER.findall(line))]


def refused(evt: BaseHookEvent, texts: list[str]) -> HookResult | None:
    return evt.block(fenced(FORMAT_REFUSAL, found)) if (found := internal_formats(texts)) else None


@on(  # capt-hook: no-escape the owner ordered owner-facing text to name chat format 0 or 1 only
    Event.PreToolUse,
    only_if=[Bound(), Tool(REPLY)],
    tests={
        Input(tool=REPLY, tool_input={"title": "Old Macs and new chats", "md": INTERNAL}, state=BOUND): Block(pattern=r"^A chat format reaches the owner[\s\S]*sessions format 6"),
        Input(tool=REPLY, tool_input={"title": "Old Macs and new chats", "md": PLAIN}, state=BOUND): Allow(),
        Input(tool=REPLY, tool_input={"title": "Next", "md": "Next raises the shared catalog's sessions head to 7."}, state=BOUND): Block(pattern=r"^A chat format reaches the owner"),
    },
)
def reply_formats_are_0_or_1(evt: BaseHookEvent) -> HookResult | None:
    return refused(evt, [str(evt._tool_input.get(field) or "") for field in ("title", "md")])


@on(  # capt-hook: no-escape the owner ordered owner-facing text to name chat format 0 or 1 only
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests=communicator_tests({
        Input(transcript=reply_transcript(INTERNAL), state=ACTIVE): Block(pattern=r"sessions format 6"),
        Input(transcript=reply_transcript(PLAIN), state=ACTIVE): Allow(),
    }),
)
def chat_formats_are_0_or_1(evt: BaseHookEvent) -> HookResult | None:
    return refused(evt, [last_reply(evt)])
