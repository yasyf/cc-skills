from __future__ import annotations

import json

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Tool, WorkflowState, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound, Communicator, communicator_tests
from .deadline_plans import posts
from .owner_answers import read
from .owner_facing import REPLY_WINDOW, SLACK_CARD, Reads, last_reply
from .owner_replies_carry_news import dm_posts
from .register_injection import drive_args
from .tests.handoff_fixtures import reply_transcript
from .tests.register_fixtures import DRIVE_CONFIG
from .urgent_lines_name_the_harm import ACTIVE, DM_TOOLS, owner_lines

RULED = "Warning, nothing was blocked: he has ruled on what this line says. Where a ruling settles it, drop the line and stop that work:"
MATCHED_RULINGS = "3"
TEXT_CAP = 6000
MEANING_CAP = 300


class RulingsShown(WorkflowState):
    ids: list[str] = []


def unseen_rulings(evt: BaseHookEvent, reads: Reads, text: str) -> list[str]:
    def load() -> list[dict]:
        listed = read(["ccn", "-R", reads.repo, "answer", "match", "--json", "--label", "scope:durable", "--limit", MATCHED_RULINGS, "--", text[:TEXT_CAP]])
        return json.loads(listed) if listed else []

    answers = (reads.read("ccn answer match", load) or []) if text.strip() else []
    with RulingsShown.mutate(evt) as shown:
        fresh = [answer for answer in answers if answer["id"] not in shown.ids]
        shown.ids = [*shown.ids, *(answer["id"] for answer in fresh)]
    return [f"- {answer['id'][:7]} {answer['title']} → {answer['body'].strip()[:MEANING_CAP]}" for answer in fresh]


def warned(evt: BaseHookEvent, reads: Reads, text: str) -> HookResult | None:
    return evt.context("\n".join([RULED, *lines])) if (lines := unseen_rulings(evt, reads, text)) else None


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", SLACK_CARD, POST_TOOL, *DM_TOOLS)],
    tests={
        Input(command="ls", env=DRIVE_CONFIG): Allow(),
        Input(command="cci post --kind report --text 'the api answers 500'", env=DRIVE_CONFIG, commands={"ccn -R": "[]"}): Allow(),
    },
)
def lines_name_a_ruling(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    reads = Reads(evt, which)
    posted = [text for text, *_ in posts(evt)]
    return warned(evt, reads, "\n".join([*posted, *owner_lines(evt, reads), *(text for _, text in dm_posts(evt))]))


@on(
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests=communicator_tests({
        Input(transcript=reply_transcript("The api answers 500."), state=ACTIVE, env=DRIVE_CONFIG, commands={"ccn -R": "[]"}): Allow(),
    }),
)
def replies_name_a_ruling(evt: BaseHookEvent) -> HookResult | None:
    return warned(evt, Reads(evt, []), last_reply(evt))
