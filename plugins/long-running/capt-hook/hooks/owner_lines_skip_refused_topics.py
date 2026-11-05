from __future__ import annotations

import json
import re

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Tool, Warn, on

from .compaction_handoff import Bound, Communicator, communicator_tests
from .owner_answers import read
from .owner_facing import REPLY_WINDOW, SLACK_CARD, Reads, fenced, last_reply
from .owner_replies_carry_news import dm_posts
from .owner_returns import shipped_cc_slack
from .register_capture import OPERATOR
from .register_injection import drive_args
from .tests.handoff_fixtures import reply_transcript
from .tests.register_fixtures import DRIVE_CONFIG
from .urgent_lines_name_the_harm import ACTIVE, DM_TOOLS, START_DRAFT, WRITE_DRAFT, owner_lines

REFUSED = "The owner ruled this class is never reported to him. Drop each line below and send the rest."
DROP_CLASS = "drop-class"
PHRASE = "match:"
ONLY_DROPS = re.compile(r"\b(?:rul(?:ing|ings|ed)|never|not-ours|stand(?:ing)?[ -]?down|classif\w+|stop chasing|(?:do not|don'?t) chase|dropp?\w*|ignor\w+|no (?:lane|read|look|fix work))\b", re.IGNORECASE)
NO_CLASSES = {"ccn -R": "[]"}


def refusals(reads: Reads) -> list[re.Pattern[str]]:
    def load() -> list[re.Pattern[str]]:
        if (listed := read(["ccn", "-R", reads.repo, "answer", "list", "--label", DROP_CLASS, "--limit", "0", "--json"])) is None:
            raise OSError("`ccn answer list` did not read")
        phrases = [tag.removeprefix(PHRASE) for answer in json.loads(listed) for tag in answer["tags"] if tag.startswith(PHRASE)]
        return [re.compile("|".join(map(re.escape, phrases)), re.IGNORECASE)] if phrases else []

    return reads.read("his drop classes", load) or []


def refused_lines(reads: Reads, texts: list[str]) -> list[str]:
    if not texts:
        return []
    listed = refusals(reads)
    return [line for text in texts for line in text.splitlines() if any(match.search(line) for match in listed) and not ONLY_DROPS.search(line)]


def reaches_him(reads: Reads, channel: str) -> bool:
    def load() -> bool:
        peer = read([str(shipped_cc_slack()), "dm-peer", "--channel", channel])
        if peer is None:
            raise OSError("`cc-slack dm-peer` did not read")
        return json.loads(peer)["peer"] == OPERATOR

    return bool(reads.read(f"who DM {channel} reaches", load))


def lane_dm_lines(evt: BaseHookEvent, reads: Reads) -> list[str]:
    if Communicator().check(evt):
        return []
    return [line for channel, text in dm_posts(evt) for line in refused_lines(reads, [text]) if reaches_him(reads, channel)]


@on(  # capt-hook: no-escape the owner ordered this class never reaches him
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", SLACK_CARD, *DM_TOOLS)],
    tests={
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Why is it failing?", "body": "An edge-vendor 403 on the tenant host."}}, env=DRIVE_CONFIG, commands=NO_CLASSES): Allow(),
        Input(tool=DM_TOOLS[0], tool_input={"channel_id": "D0B6Z", "thread_ts": "1.2", "text": "An edge-vendor 403 on the tenant host."}, state=ACTIVE, env=DRIVE_CONFIG, commands=NO_CLASSES): Allow(),
        Input(command="cc-present reply --block skew --body 'An edge-vendor 403 on the tenant host.'", env=DRIVE_CONFIG, commands=NO_CLASSES): Allow(),
        Input(command="ls", env=DRIVE_CONFIG): Allow(),
        Input(command=WRITE_DRAFT + START_DRAFT, env=DRIVE_CONFIG): Allow(),
        Input(command=START_DRAFT, env=DRIVE_CONFIG): Warn(pattern=r"^Warning, nothing was blocked: owner_lines_skip_refused_topics could not read what it checks"),
    },
)
def owner_lines_skip_refused_topics(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    reads = Reads(evt, which)
    if found := [*refused_lines(reads, owner_lines(evt, reads)), *lane_dm_lines(evt, reads)]:
        return evt.block(fenced(REFUSED, found))
    return evt.context(reads.warning("owner_lines_skip_refused_topics")) if reads.failures else None


@on(  # capt-hook: no-escape the owner ordered this class never reaches him
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests=communicator_tests({
        Input(transcript=reply_transcript("An edge-vendor 403 on the tenant host."), state=ACTIVE, env=DRIVE_CONFIG, commands=NO_CLASSES): Allow(),
    }),
)
def owner_replies_skip_refused_topics(evt: BaseHookEvent) -> HookResult | None:
    return evt.block(fenced(REFUSED, found)) if (found := refused_lines(Reads(evt, []), [last_reply(evt)])) else None
