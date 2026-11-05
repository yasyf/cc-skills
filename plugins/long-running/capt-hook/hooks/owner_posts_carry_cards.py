from __future__ import annotations

import json
import re

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Tool, Warn, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .gate5_release import OWNER_LANES
from .lane_slack_status import CC_SLACK, CLI, WRITE_TOOLS
from .owner_facing import HIDDEN_CITE, post_values, posted_text
from .owner_returns import DM_CARD, REPLY, dm_posts
from .tests.register_fixtures import DRIVE_CONFIG

NAMED_PR = re.compile(r"(?:(?<![\w/#-])|(?<=monorepo))#(\d{5})\b|\bPRs? #?(\d{5})\b", re.IGNORECASE)
LINKED_PR = re.compile(r"/monorepo/pull/(\d+)")
RELEASE_WORD = re.compile(r"\breleas(?:e[sd]?|ing)\b", re.IGNORECASE)
UPCOMING_RELEASE = re.compile(r"\b(?:next|future|upcoming|later)\s+releases?\b", re.IGNORECASE)
RELEASE_NAME = re.compile(r"(?i)\b(?:[\w-]+-releas\w*(?:-[\w-]+)*|releas\w*-[\w-]+|release lanes?)\b")
PLATY_CARD = re.compile(r"/archives/C0B75AL4XEW/p\d{16}")
RELEASE_BUILD = "/release/builds/"
SENTENCE = re.compile(r"[.!?](?:\s+|$)|\n")
TAP_ORDER = re.compile(r"(?i)^\s*(?:tap|approve)\b")
ASKS_TAP = re.compile(r"(?i)\b(?:waits?|waiting|ready|needs?|asks?)\b[^.\n]{0,60}\b(?:your (?:tap|approval|answer|yes)|on you\b)|\bapprove or hold\b|\b(?:approve|tap) (?:to land|it\b|here\b|#\d)")
DESCRIBES_TAP = re.compile(r"(?i)\b(?:refus\w*|buttons?)\b")
BARE_PR = "Warning, nothing was blocked: A post to the owner naming a monorepo PR links it, because a bare number shows no card unless a drive's ledger holds it. Send it through `lr reply` with the PR in `prs`, or put the PR's GitHub link in the post."
NO_PLATY_CARD = "Warning, nothing was blocked: A release word to the owner gives what users get in plain words and links the Platy release card. Add the card's #releases permalink, which the build's `release-slack-root` annotation names."
NOTHING_TO_TAP = "A message to the owner that asks him to tap, approve or answer carries the button to do it. Send it through `lr reply` with its picks or the ready PR in `prs`, pass each ready PR as `--tap-pr <url>` (`tap_prs`), draw it as a waiting card, or say what it waits on without asking him."
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
    return bool({first or second for first, second in NAMED_PR.findall(HIDDEN_CITE.sub(" ", words))} - set(LINKED_PR.findall(words)))


def names_a_release(words: str) -> bool:
    return bool(RELEASE_WORD.search(UPCOMING_RELEASE.sub(" ", RELEASE_NAME.sub(" ", HIDDEN_CITE.sub(" ", words)))))


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
        sent("The flaky test is fixed. <!-- #50026 -->"): Allow(),
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
        sent("The fix ships in the next release."): Allow(),
        sent("Lane release-brittle owns the class fix."): Allow(),
        sent("exempt-release-paths takes that code out of the class now."): Allow(),
        sent("The release lane answers by 10:40 PM."): Allow(),
        sent("Lane release-brittle says release 12881 is live."): Warn(),
        sent("The fix needs CI and a release, about an hour."): Warn(),
        sent("Release 12881 is live, and the rest ships in the next release."): Warn(),
        sent("The relay fix merged. <!-- release build 12890 -->"): Allow(),
        sent("Release 12881 live; 12890 cutting.", channel="C0RELEASES1"): Allow(),
        Input(command="cc-slack dm-status 'Release 12881 is live.'", env=LANE): Warn(),
        replied("Release 12881 is live."): Warn(pattern=r"A release word to the owner"),
        replied("Release 12881 is live.", links=[CARD_LINK]): Allow(),
        replied(f"Iris can rebase a draft, released in 12881: {CARD_LINK}"): Allow(),
    },
)
def release_words_link_the_platy_card(evt: BaseHookEvent) -> HookResult | None:
    return evt.context(NO_PLATY_CARD) if any(names_a_release(words) and not PLATY_CARD.search(words) for words in posted_words(evt)) else None


def owes_card(values: dict[str, tuple]) -> bool:
    if "release" in values.get("kind", ()):
        return True
    addressed = {lane for to in values.get("to", ()) for lane in str(to).split(",")}
    return any(RELEASE_BUILD in str(build) for build in values.get("build", ())) and bool(addressed & OWNER_LANES)


def release_records(evt: BaseHookEvent) -> list[str]:
    if evt.tool_name == POST_TOOL:
        return [json.dumps(evt._tool_input)] if evt._tool_input.get("kind") == "release" else []
    posts = [values for call in evt.command.calls("cci") if (values := post_values(evt, call)).get("words", ())[:1] == ("post",)]
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


def asks_his_tap(words: str) -> bool:
    return any(TAP_ORDER.search(said) or ASKS_TAP.search(said) and not DESCRIBES_TAP.search(said) for said in SENTENCE.split(HIDDEN_CITE.sub(" ", words)))


def carries_tap_cards(evt: BaseHookEvent) -> bool:
    if evt.tool_name != "Bash":
        return bool(evt._tool_input.get("tap_prs"))
    return any(CLI.bind(call).values.get("tap-pr") for call in evt.command.calls("cc-slack"))


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", *WRITE_TOOLS)],
    tests={
        sent("#50921 is green and waits on your tap."): Block(pattern=r"^A message to the owner that asks him to tap.*`--tap-pr <url>`"),
        sent("Six PRs wait only on your answer to a card already on your board."): Block(),
        Input(tool=f"{CC_SLACK}{DM_CARD}", tool_input={"card": {"title": "Fork side-chat bug", "body": "#50644 is proven green and waits on your approval."}}, env=LANE): Block(),
        Input(tool=f"{CC_SLACK}{DM_CARD}", tool_input={"card": {"title": "Brittleness, the count", "body": "Three fixes are ready for your approval."}}, env=LANE): Block(),
        Input(command="cc-slack dm-status 'Tap: #51168, the run grant answers 503'", env=LANE): Block(),
        Input(tool=f"{CC_SLACK}{DM_CARD}", tool_input={"card": {"title": "Tap: #51168", "body": "It passed."}, "tap_prs": [PR_LINK]}, env=LANE): Allow(),
        Input(command=f"cc-slack dm-status --tap-pr {PR_LINK} 'Tap: #50026, it passed'", env=LANE): Allow(),
        Input(tool=f"{CC_SLACK}{DM_CARD}", tool_input={"card": {"title": "Which fix?", "body": "Both wait on your answer.", "options": ["A", "B"], "waits": "card-x"}}, env=LANE): Allow(),
        sent("Your pick is recorded, and the port state follows."): Allow(),
        sent("#50346 is in the merge queue on your approval."): Allow(),
        sent("#50026 waits on CI, then it lands."): Allow(),
        sent("It is live. <!-- waits on your tap -->"): Allow(),
        sent("Since 1:10 PM a message from me that asks for your approval with no button is refused before it posts."): Allow(),
        sent("Since 1:10 PM a message that reads as waiting on you and carries no button is refused before it posts."): Allow(),
        sent("A message with no button is refused now. #50921 is green and needs your tap."): Block(),
        sent("It passed. Tap the button on the card above."): Block(),
        sent("#50921 waits on your tap.", channel="C0RELEASES1"): Allow(),
        Input(command="ls", env=LANE): Allow(),
    },
)
def tap_asks_carry_the_button(evt: BaseHookEvent) -> HookResult | None:
    if carries_tap_cards(evt):
        return None
    asks = any(asks_his_tap(words) and not posted.get("waits") for words, posted in dm_posts(evt))
    return evt.block(NOTHING_TO_TAP) if asks else None
