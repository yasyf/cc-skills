from __future__ import annotations

import json
import re
import subprocess

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Tool, Warn, on

from .compaction_handoff import Communicator, communicator_tests, CompactionState
from .lane_rotation import DriveActive
from .owner_facing import SLACK_CARD

ACTIVE = [CompactionState(active=True, coordinator=True)]
QUESTION = re.compile(r"[^.?!\n]*\?")
APPROVAL = re.compile(r"\b(?:approve|approval|your word|go-ahead|sign[- ]off|ok to)\b", re.IGNORECASE)
RELEASE_STEP = re.compile(r"\b(?:release|deletes?|replaces?|migrations?|apply)\b", re.IGNORECASE)
ORDER_READ = ["cci", "grep", "--lane", "root", "--json", "-n", "1", "--since", "12h", "--", "RELEASE ORDER|owner-ordered"]
READ_SECONDS = 10
ORDER = json.dumps({"seq": 341020, "lane": "root", "kind": "go", "text": "RELEASE ORDER: owner 4:52 PM, do a full release of plat"})
MESSAGE = (
    "Warning, nothing was blocked: The owner's release order already carries every approval its steps need, deletes and migrations included."
    " Let the release lane approve the step itself and report deletes and replaces in its done line."
)


def asks_release_approval(text: str) -> bool:
    return any(APPROVAL.search(question) and RELEASE_STEP.search(question) for question in QUESTION.findall(text))


def release_ordered() -> bool:
    done = subprocess.run(ORDER_READ, capture_output=True, text=True, timeout=READ_SECONDS)
    return done.returncode == 0 and bool(done.stdout.strip())


def card_text(evt: BaseHookEvent) -> str:
    card = evt._tool_input.get("card") or {}
    return "\n".join([card.get("title") or "", card.get("body") or "", *(card.get("options") or [])])


class AsksOrderedApproval:
    def check(self, evt: BaseHookEvent) -> bool:
        return asks_release_approval(card_text(evt)) and release_ordered()


def ask(question: str, order: str = ORDER, **extra: object) -> Input:
    return Input(tool=SLACK_CARD, tool_input={"card": {"title": question, "options": ["Approve", "Hold"]}}, state=ACTIVE, commands={"cci grep": order}, **extra)


@on(
    Event.PreToolUse,
    only_if=[Communicator(), Tool(SLACK_CARD), DriveActive(), AsksOrderedApproval()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        ask("Approve the 3 deletes on plat so release 12151 can apply?"): Warn(pattern=r"^Warning, nothing was blocked: The owner's release order already carries"),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Your word on the migrations for the plat release?", "options": ["Approve", "Hold"]}}, state=ACTIVE, commands={"cci grep": ORDER}): Warn(),
        ask("Approve the 3 deletes on plat so release 12151 can apply?", ""): Allow(),
        ask("Merge #48401 now?"): Allow(),
        ask("Approve the 3 deletes on plat so release 12151 can apply?", agent_id="a1b2c3"): Allow(),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Approve the deletes on plat?"}}, commands={"cci grep": ORDER}): Allow(),
    }),
)
def ordered_releases_ask_no_approval(evt: BaseHookEvent) -> HookResult:
    return evt.context(MESSAGE)
