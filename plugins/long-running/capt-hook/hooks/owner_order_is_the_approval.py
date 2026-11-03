from __future__ import annotations

import json
import re
import subprocess

from captain_hook import CONFIRMED, Allow, BaseHookEvent, Block, Confirm, Event, FromSubagent, Input, Tool, hook

from .compaction_handoff import CompactionState
from .lane_rotation import DriveActive
from .owner_facing import REPLY_WINDOW, SLACK_CARD, last_reply, question_text
from .tests.handoff_fixtures import reply_transcript
from .window_lane import Coordinator, OwnerBridge, bridge_tests, coordinator_tests

ACTIVE = [CompactionState(active=True)]
QUESTION = re.compile(r"[^.?!\n]*\?")
APPROVAL = re.compile(r"\b(?:approve|approval|your word|go-ahead|sign[- ]off|ok to)\b", re.IGNORECASE)
RELEASE_STEP = re.compile(r"\b(?:release|deletes?|replaces?|migrations?|apply)\b", re.IGNORECASE)
ORDER_READ = ["cci", "grep", "--lane", "root", "--json", "-n", "1", "--since", "12h", "--", "RELEASE ORDER|owner-ordered"]
READ_SECONDS = 10
ORDER = json.dumps({"seq": 341020, "lane": "root", "kind": "go", "text": "RELEASE ORDER: owner 4:52 PM, do a full release of plat"})
MESSAGE = (
    "The owner's release order already carries every approval its steps need, deletes and migrations included."
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
        return asks_release_approval(card_text(evt) if evt.tool_name == SLACK_CARD else question_text(evt)) and release_ordered()


class RepliesOrderedApproval:
    def check(self, evt: BaseHookEvent) -> bool:
        return asks_release_approval(last_reply(evt)) and release_ordered()


def ask(question: str, order: str = ORDER, **extra: object) -> Input:
    return Input(tool="AskUserQuestion", tool_input={"questions": [{"question": question, "options": [{"label": "Approve"}, {"label": "Hold"}]}]}, state=ACTIVE, commands={"cci grep": order}, decide=CONFIRMED, **extra)


hook(
    Event.PreToolUse,
    only_if=[OwnerBridge(), Tool("AskUserQuestion", SLACK_CARD), DriveActive(), AsksOrderedApproval()],
    skip_if=[FromSubagent()],
    block=True,
    confirm=Confirm(rule="The owner already ordered this release, so nobody asks him to approve one of its steps."),
    message=MESSAGE,
    tests=bridge_tests({
        ask("Approve the 3 deletes on plat so release 12151 can apply?"): Block(pattern=r"^The owner's release order already carries"),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Your word on the migrations for the plat release?", "options": ["Approve", "Hold"]}}, state=ACTIVE, commands={"cci grep": ORDER}, decide=CONFIRMED): Block(),
        ask("Approve the 3 deletes on plat so release 12151 can apply?", ""): Allow(),
        ask("Merge #48401 now?"): Allow(),
        ask("Approve the 3 deletes on plat so release 12151 can apply?", agent_id="a1b2c3"): Allow(),
        Input(tool="AskUserQuestion", tool_input={"questions": [{"question": "Approve the deletes on plat?", "options": []}]}, commands={"cci grep": ORDER}): Allow(),
    }),
)

hook(  # capt-hook: no-escape a reply to the owner carries no tool call to confirm, and the order already answers the ask
    Event.Stop,
    only_if=[Coordinator(), DriveActive(), RepliesOrderedApproval()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    block=True,
    message=MESSAGE,
    tests=coordinator_tests({
        Input(transcript=reply_transcript("Release 12151 shows 3 deletes on plat. Approve them so the apply can go on?"), state=ACTIVE, commands={"cci grep": ORDER}): Block(pattern=r"report deletes and replaces in its done line"),
        Input(transcript=reply_transcript("Release 12151 applied with 3 deletes, which the release lane approved under the owner's order."), state=ACTIVE, commands={"cci grep": ORDER}): Allow(),
        Input(transcript=reply_transcript("Approve the 3 deletes on plat?"), state=ACTIVE, commands={"cci grep": ""}): Allow(),
    }),
)
