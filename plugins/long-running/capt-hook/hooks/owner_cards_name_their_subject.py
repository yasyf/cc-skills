from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on

from .compaction_handoff import Bound, CompactionState
from .owner_returns import REPLY
from .urgent_lines_name_the_harm import sentences

BOUND = [CompactionState(active=True, coordinator=True)]
PICKS = {"picks": ["Keep them ours", "Open them"], "recommended": "Keep them ours"}
LAND_PR = "https://github.com/yasyf/cc-skills/pull/2196"

VAGUE = re.compile(r"(?i)^\W*(this|these|it|the change)\b")
NAMES_PR = re.compile(r"(?i)(?<![\w/&])#\d{2,}\b|/pull/\d+|\bPR \d+\b")
UNNAMED = 'Name the feature: a card\'s title and first sentence say what it is about, and this one opens with "{opener}". Edit the reply to name it.'
BARE_PR = "A pick card about a PR passes it in `prs`, which gives him its link, CI, description and four lines: edit the reply to add the PR's url to `prs`."


def given(evt: BaseHookEvent, field: str) -> str:
    return str(evt._tool_input.get(field) or "")


def is_card(evt: BaseHookEvent) -> bool:
    return bool(evt._tool_input.get("picks") or evt._tool_input.get("prs"))


def unnamed_opener(evt: BaseHookEvent) -> str | None:
    parts = sentences(given(evt, "md"))
    for text in (given(evt, "title"), parts[0] if parts else ""):
        if match := VAGUE.match(text):
            return match[1]
    return None


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool(REPLY)],
    tests={
        Input(tool=REPLY, tool_input={"title": "This adds three pieces of API. Keep them to our own apps?", "md": "Our apps call them.", **PICKS}, state=BOUND): Warn(pattern=r'Name the feature:[\s\S]*"This"'),
        Input(tool=REPLY, tool_input={"title": "Keep the board API private?", "md": "It adds three calls.", **PICKS}, state=BOUND): Warn(pattern=r'"It"'),
        Input(tool=REPLY, tool_input={"title": "The relay fix", "md": "The change is live.", "prs": [LAND_PR]}, state=BOUND): Warn(pattern=r'"The change"'),
        Input(tool=REPLY, tool_input={"title": "Keep the board API private?", "md": "The board API adds three calls.", **PICKS}, state=BOUND): Allow(),
        Input(tool=REPLY, tool_input={"title": "This landed", "md": "It is live."}, state=BOUND): Allow(),
    },
)
def cards_name_their_subject(evt: BaseHookEvent) -> HookResult | None:
    if is_card(evt) and (opener := unnamed_opener(evt)):
        return evt.context("Warning, nothing was blocked: " + UNNAMED.format(opener=opener))
    return None


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool(REPLY)],
    tests={
        Input(tool=REPLY, tool_input={"title": "Land #49722 tonight?", "md": "Its checks passed.", **PICKS}, state=BOUND): Warn(pattern=r"A pick card about a PR passes it in `prs`"),
        Input(tool=REPLY, tool_input={"title": "Land the relay fix?", "md": "Its checks passed on PR 49722.", **PICKS}, state=BOUND): Warn(pattern=r"`prs`"),
        Input(tool=REPLY, tool_input={"title": "Land #49722 tonight?", "md": "Its checks passed.", "prs": [LAND_PR], **PICKS}, state=BOUND): Allow(),
        Input(tool=REPLY, tool_input={"title": "Land the relay fix?", "md": "Its checks passed.", **PICKS}, state=BOUND): Allow(),
        Input(tool=REPLY, tool_input={"title": "#49722 landed", "md": "Its checks passed.", "status": "done"}, state=BOUND): Allow(),
    },
)
def pick_cards_pass_their_prs(evt: BaseHookEvent) -> HookResult | None:
    if evt._tool_input.get("picks") and not evt._tool_input.get("prs") and NAMES_PR.search(given(evt, "title") + "\n" + given(evt, "md")):
        return evt.context("Warning, nothing was blocked: " + BARE_PR)
    return None
