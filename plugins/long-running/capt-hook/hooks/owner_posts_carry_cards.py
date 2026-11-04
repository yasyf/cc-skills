from __future__ import annotations

import json
import re

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .gate5_release import OWNER_LANES
from .lane_slack_status import CC_SLACK, WRITE_TOOLS
from .owner_facing import CCI_POST, posted_text
from .owner_returns import DM_CARD, REPLY, dm_posts
from .tests.register_fixtures import DRIVE_CONFIG

NAMED_PR = re.compile(r"(?:(?<![\w/#-])|(?<=monorepo))#(\d{5})\b|\bPRs? #?(\d{5})\b", re.IGNORECASE)
LINKED_PR = re.compile(r"/monorepo/pull/(\d+)")
RELEASE_WORD = re.compile(r"\breleas(?:e[sd]?|ing)\b", re.IGNORECASE)
PLATY_CARD = re.compile(r"/archives/C0B75AL4XEW/p\d{16}")
RELEASE_BUILD = "/release/builds/"
BARE_PR = "Warning, nothing was blocked: A post to the owner naming a monorepo PR shows its card, and a bare number draws none. Send it through `lr reply` with the PR in `prs`, or put the PR's GitHub link in the post."
NO_PLATY_CARD = "Warning, nothing was blocked: A release word to the owner gives what users get in plain words and links the Platy release card. Add the card's #releases permalink, which the build's `release-slack-root` annotation names."
UNCARDED_RECORD = "Warning, nothing was blocked: A release record carries the Platy release card, so the relay can show it to the owner. Add `--attr card=<permalink>`, read from the build's `release-slack-root` annotation."

LANE = DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": "flaky-tests-1"}
PR_LINK = "https://github.com/Forge-AI/monorepo/pull/50026"
CARD_LINK = "https://poetic-technology.slack.com/archives/C0B75AL4XEW/p1793822082203289"
OTHER_CHANNEL = "https://poetic-technology.slack.com/archives/C0OUTAGE01/p1793822082203289"
DM_THREAD = "https://poetic-technology.slack.com/archives/D0B6Z/p1793635190051179"
RELEASE_RUN = "https://buildkite.com/forge/release/builds/12890"
TEST_RUN = "https://buildkite.com/forge/test/builds/110254"
CUT = f"cci post --drive d --lane release-6 --kind release --target api --build {RELEASE_RUN} --to root"


def sent(text: str, channel: str = "D0B6Z", tool: str = "slack_send", **given: object) -> Input:
    return Input(tool=f"{CC_SLACK}{tool}", tool_input={"channel_id": channel, "text": text} | given, env=LANE)


def replied(md: str, **given: object) -> Input:
    return Input(tool=REPLY, tool_input={"title": "Flaky tests", "md": md} | given, env=LANE)


def recorded(command: str) -> Input:
    return Input(command=command, env=LANE)


def posted_words(evt: BaseHookEvent) -> list[str]:
    if evt.tool_name != REPLY:
        return [words for words, _ in dm_posts(evt)]
    given = evt._tool_input
    return ["\n".join([str(given.get("title") or ""), str(given.get("md") or ""), *map(str, given.get("links") or ()), *map(str, given.get("prs") or ())])]


def names_bare_pr(words: str) -> bool:
    return bool({first or second for first, second in NAMED_PR.findall(words)} - set(LINKED_PR.findall(words)))


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", REPLY, *WRITE_TOOLS)],
    tests={
        sent("#50026 fixes the flaky test."): Warn(pattern=r"^Warning, nothing was blocked: A post to the owner naming a monorepo PR.*`lr reply` with the PR in `prs`"),
        sent(f"{PR_LINK} fixes the flaky test."): Allow(),
        sent(f"<{PR_LINK}|#50026> fixes the flaky test."): Allow(),
        sent(f"#50026 and #50027 fix the flaky test: {PR_LINK}"): Warn(),
        sent("PR 50026 fixes the flaky test."): Warn(),
        sent("monorepo#50026 fixes the flaky test."): Warn(),
        sent("cc-skills#2890 fixes the flaky test."): Allow(),
        sent("Record #425825 says the flaky test is fixed."): Allow(),
        sent("#50026 fixes the flaky test.", channel="C0RELEASES1"): Allow(),
        Input(tool=f"{CC_SLACK}slack_reply", tool_input={"url": DM_THREAD, "text": "#50026 fixes the flaky test."}, env=LANE): Warn(),
        Input(tool=f"{CC_SLACK}{DM_CARD}", tool_input={"card": {"title": "Flaky tests", "body": "#50026 fixes them."}}, env=LANE): Warn(),
        Input(tool=f"{CC_SLACK}{DM_CARD}", tool_input={"card": {"title": "Flaky tests", "body": f"{PR_LINK} fixes them."}}, env=LANE): Allow(),
        Input(command="cc-slack dm-status 'PR 50026 fixes the flaky test.'", env=LANE): Warn(),
        Input(command=f"cc-slack send --channel D0B6Z --text '{PR_LINK} fixes the flaky test.'", env=LANE): Allow(),
        replied("#50026 fixes the flaky test."): Warn(pattern=r"`lr reply` with the PR in `prs`"),
        replied("#50026 fixes the flaky test.", prs=[PR_LINK]): Allow(),
        replied("The flaky test is fixed."): Allow(),
        Input(tool=f"{CC_SLACK}slack_send", tool_input={"channel_id": "D0B6Z", "text": "#50026 fixes the flaky test."}): Allow(),
        Input(command="ls", env=LANE): Allow(),
    },
)
def posts_naming_a_pr_show_its_card(evt: BaseHookEvent) -> HookResult | None:
    return evt.context(BARE_PR) if any(names_bare_pr(words) for words in posted_words(evt)) else None


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", REPLY, *WRITE_TOOLS)],
    tests={
        sent("Release 12881 live; 12890 cutting."): Warn(pattern=r"^Warning, nothing was blocked: A release word to the owner.*`release-slack-root`"),
        sent(f"Iris can rebase a draft onto a moved build, live in release 12881: {CARD_LINK}"): Allow(),
        sent(f"Release 12881 is live: {OTHER_CHANNEL}"): Warn(),
        sent("The relay fix merged, so each chat keeps its own replica."): Allow(),
        sent("Release 12881 live; 12890 cutting.", channel="C0RELEASES1"): Allow(),
        Input(command="cc-slack dm-status 'Release 12881 is live.'", env=LANE): Warn(),
        replied("Release 12881 is live."): Warn(pattern=r"A release word to the owner"),
        replied("Release 12881 is live.", links=[CARD_LINK]): Allow(),
        replied(f"Iris can rebase a draft, released in 12881: {CARD_LINK}"): Allow(),
    },
)
def release_words_link_the_platy_card(evt: BaseHookEvent) -> HookResult | None:
    return evt.context(NO_PLATY_CARD) if any(RELEASE_WORD.search(words) and not PLATY_CARD.search(words) for words in posted_words(evt)) else None


def owes_card(values: dict[str, tuple]) -> bool:
    if "release" in values.get("kind", ()):
        return True
    addressed = {lane for to in values.get("to", ()) for lane in str(to).split(",")}
    return any(RELEASE_BUILD in str(build) for build in values.get("build", ())) and bool(addressed & OWNER_LANES)


def release_records(evt: BaseHookEvent) -> list[str]:
    if evt.tool_name == POST_TOOL:
        return [json.dumps(evt._tool_input)] if evt._tool_input.get("kind") == "release" else []
    posts = [values for call in evt.command.calls("cci") if (values := CCI_POST.bind(call).values).get("words", ())[:1] == ("post",)]
    return ["\n".join([posted_text(values), *map(str, values.get("attr", ()))]) for values in posts if owes_card(values)]


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", POST_TOOL)],
    tests={
        recorded(f"{CUT} 'CUT releases/whimsical-akins build 12890'"): Warn(pattern=r"^Warning, nothing was blocked: A release record carries the Platy release card.*`--attr card=<permalink>`"),
        recorded(f"{CUT} --attr card={CARD_LINK} 'CUT releases/whimsical-akins build 12890'"): Allow(),
        recorded(f"{CUT} 'Platy card for 12890: {CARD_LINK}'"): Allow(),
        recorded(f"cci post --drive d --lane release-6 --kind report --build {RELEASE_RUN} --to owner-relay --to root 'Release 12890 finished.'"): Warn(),
        recorded(f"cci post --drive d --lane release-6 --kind report --build {RELEASE_RUN} --to owner-relay,root 'Release 12890 finished.'"): Warn(),
        recorded(f"cci post --drive d --lane release-6 --kind report --build {RELEASE_RUN} --to dd-1-fix 'Release facts for your window.'"): Allow(),
        recorded(f"cci post --drive d --lane release-6 --kind report --build {TEST_RUN} --to owner-relay 'Dev build passed.'"): Allow(),
        recorded("cci tail --drive d --kind release"): Allow(),
        Input(tool=POST_TOOL, tool_input={"kind": "release", "text": "CUT releases/whimsical-akins build 12890"}, env=LANE): Warn(),
        Input(tool=POST_TOOL, tool_input={"kind": "release", "text": "CUT build 12890", "attr": {"card": CARD_LINK}}, env=LANE): Allow(),
        Input(tool=POST_TOOL, tool_input={"kind": "report", "text": "merged a fix"}, env=LANE): Allow(),
    },
)
def release_records_carry_the_platy_card(evt: BaseHookEvent) -> HookResult | None:
    return evt.context(UNCARDED_RECORD) if any(not PLATY_CARD.search(text) for text in release_records(evt)) else None
