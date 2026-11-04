from __future__ import annotations

import json
import re

from captain_hook import Allow, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, Tool, Warn, on

from .board_text_gate import published, ran
from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import LR, Bound, Communicator, CompactionState, communicator_tests
from .lane_slack_status import CC_SLACK
from .meta_records import posts
from .owner_facing import CODE, OWNER_QUOTE, REPLY_WINDOW, SENTENCE_BREAK, SLACK_CARD, URL, Reads, fenced, last_reply, shown, slack_card, started_boards
from .owner_replies_carry_news import dm_posts
from .register_injection import INCIDENT_NAME, drive_args
from .tests.handoff_fixtures import reply_transcript
from .tests.register_fixtures import DRIVE_CONFIG

URGENT = re.compile(r"(?i:(?<![\w-])(?:urgent(?:ly)?|urgency|asap|immediately|critical(?:ly)?|time[- ]sensitive|right (?:now|away)|emergency|top priority)(?![\w-]))|(?<![\w-])NOW(?![\w-])")
HARM = re.compile(r"\b(?:los[est]s?|losing|lost|delet\w*|drop\w*|break\w*|broke\w*|down|outage|fail\w*|wipe\w*|corrupt\w*|bricked|gone|stuck|blocked|miss\w*)\b", re.IGNORECASE)
WHO = re.compile(r"(?i:\b(?:customers?|teams?|clusters?|tenants?|users?|people|everyone|you|your|he|his|him|she|her|they|their|them)\b)|(?<=\s)[A-Z][a-z]\w*")
UNDONE = re.compile(r"\b(?:(?:ir)?revers\w*|permanent\w*|undo\w*|undone|restor\w*|recover\w*|lost|roll(?:s|ed|ing)? ?back|for good)\b", re.IGNORECASE)
NUMBER = re.compile(r"^\W*(?:#\d{3,}|\d{5,}|0\d{3})(?:'s)?\W*$")
PLAIN = re.compile(r"^[A-Za-z][a-z'-]{2,}$")
FILLER = frozenset("the and for with its his her this that was are has had have been now not but from into about then than also just still yet all any our you your".split())
PR_REF = re.compile(r"(?i)(?:#|\bpr ?#?|\bcard-)(\d{5})\b")
OTHER_REPO_REF = re.compile(r"(?i)(?<![\w./-])(?:(?!forge-ai/)[\w.-]+/[\w.-]+|cc-[\w-]+)(?:#| #| pr ?#?)\d+|(?<![\w./-])(?!monorepo#)[\w.-]+#\d+")
ESCALATES = re.compile(r"(?i)\b(?:releases?|deploy(?:s|ed|ing)?|rollouts?|resum\w*)\b")
RELAY = "owner-relay"
RAISING = frozenset({"ask", "blocker", "blocked", "hold", "urgent", "red", "decide"})
BESIDE = 4
PUNCTUATION = ".,:;()?!\"'"
URGENT_REFUSAL = (
    "A line to the owner that calls something urgent says, in that sentence or the next, what is lost, for whom, and whether it can be undone."
    " Rewrite each line below the way the example reads, then send it again."
)
EXAMPLE = "example: Urgent: the delete drops SoFi's old signal store for good, so their chats lose history unless you cancel first."
BARE_NUMBERS = (
    "Warning, nothing was blocked: a migration, release or PR number the owner reads carries plain words beside it saying what that change does."
    " Add them beside each number below, as in `the migration that drops the four teams columns`."
)
FOREIGN_REFUSAL = (
    "A line that calls another person's PR urgent, or escalates a release to the owner or owner-relay, names that person and the concrete harm:"
    " what is lost, for whom, and whether it can be undone. Their release is theirs to run; with no harm to name, state the fact plainly or say nothing."
)
DM_TOOLS = (f"{CC_SLACK}slack_reply", f"{CC_SLACK}slack_send")
ACTIVE = [CompactionState(active=True, communicator=True)]
UNEXPLAINED = "Approve Andrew's deploy?"
RESUMING = "12401 IS RESUMING NOW: 12414, 'Resume releases/2026-11-03/43', started by Andrew Benton about 1:45 PM. Its plan may carry the signal-valkey deletes on plat and SoFi, 0453 and SandSQL on SoFi and AIG."
NAMED = "Urgent: the delete drops SoFi's old signal store for good, so their chats lose history unless you cancel first."
WHOSE = "\n".join(
    json.dumps(row)
    for row in (
        {"number": 46018, "author": "andrew", "name": "Andrew Benton", "ours": False, "foreign": True},
        {"number": 49336, "author": "drive-bot", "lane": "chat-version-land-1", "ours": True},
        {"number": 2455, "author": "jason", "name": "Jason Lee", "ours": False, "foreign": True},
    )
)
WHOSE_STUB = {f"{LR} facts whose": WHOSE}
ESCALATED = "URGENT: 12401 is resuming now and runs #46018's column drop on SoFi and AIG; cancel it."
HIS_HARM = "Urgent: Andrew's resume of #46018 drops the four teams columns on SoFi for good, so their chats lose that history unless he cancels first."


def sentences(text: str) -> list[str]:
    said = URL.sub(" ", CODE.sub(" ", OWNER_QUOTE.sub(" ", text)))
    return [part.strip() for part in SENTENCE_BREAK.split(said) if part.strip()]


def unexplained_urgency(texts: list[str]) -> list[str]:
    found = []
    for text in texts:
        parts = sentences(text)
        found += [part for index, part in enumerate(parts) if URGENT.search(part) and not all(pattern.search(" ".join(parts[index : index + 2])) for pattern in (HARM, WHO, UNDONE))]
    return found


def plain(word: str) -> bool:
    stripped = word.strip(PUNCTUATION)
    return bool(PLAIN.match(stripped)) and stripped.lower() not in FILLER


def bare(sentence: str) -> list[str]:
    words = sentence.split()
    return [
        f'{word.strip(PUNCTUATION)}: "{sentence[:160]}"'
        for index, word in enumerate(words)
        if NUMBER.match(word) and sum(map(plain, [*words[max(index - BESIDE, 0) : index], *words[index + 1 : index + 1 + BESIDE]])) < 2
    ]


def bare_numbers(texts: list[str]) -> list[str]:
    return [found for text in texts for sentence in sentences(text) for found in bare(sentence)]


def owner_lines(evt: BaseHookEvent, reads: Reads) -> list[str]:
    if evt.tool_name in DM_TOOLS:
        return [text for _, text in dm_posts(evt)] if Communicator().check(evt) else []
    if card := reads.read("the status card", lambda: slack_card(evt)):
        return [text for _, text in shown(card)]
    if evt.tool_name != "Bash":
        return []
    posts = [text for _, text in dm_posts(evt)] if Communicator().check(evt) else []
    boards = [text for item in published(evt, reads, []) for _, text in shown(item.doc)]
    return [*posts, *boards, *(text for _, text in started_boards(evt, reads))]


def tool_lines(evt: BaseHookEvent) -> list[str]:
    return owner_lines(evt, Reads(evt, which)) if (which := drive_args(evt)) else []


def relay_lines(evt: BaseHookEvent) -> list[tuple[str, str]]:
    return [(kind.lower(), text) for lane, recipients, kind, text, _, _ in posts(evt) if RELAY in (to.split("@", 1)[0] for to in recipients) and not INCIDENT_NAME.match(lane)]


def prs_named(texts: list[str]) -> list[int]:
    return sorted({int(number) for text in texts for number in PR_REF.findall(OTHER_REPO_REF.sub("", text))})


def whose(reads: Reads, numbers: list[int]) -> dict[int, dict]:
    if not numbers:
        return {}
    rows = reads.read("the pull requests' GitHub authors", lambda: [json.loads(line) for line in ran([str(LR), "facts", "whose", *map(str, numbers), *reads.which]).splitlines()])
    return {row["number"]: row for row in rows or []}


def theirs(reads: Reads, texts: list[str]) -> list[tuple[str, dict]]:
    found = whose(reads, prs_named(texts))
    return [(text, row) for text in texts for number in prs_named([text]) if (row := found.get(number)) and row.get("foreign")]


def person(row: dict) -> str:
    return f"{row['name']} ({row['author']})" if row.get("name") else row["author"]


def names(text: str, row: dict) -> bool:
    words = {row["author"].lower(), *(part.lower() for part in (row.get("name") or "").split())}
    return any(re.search(rf"(?i)\b{re.escape(word)}\b", text) for word in words if word)


def unexplained_escalations(reads: Reads, owner: list[str], relayed: list[tuple[str, str]]) -> list[str]:
    raised = [text for text in owner if URGENT.search(text)] + [text for kind, text in relayed if URGENT.search(text) or kind in RAISING and ESCALATES.search(text)]
    return [
        f"#{row['number']} is {person(row)}'s PR: \"{text[:160]}\""
        for text, row in theirs(reads, raised)
        if not (names(text, row) and HARM.search(text) and UNDONE.search(text))
    ]


def refused(evt: BaseHookEvent, texts: list[str]) -> HookResult | None:
    return evt.block(fenced(URGENT_REFUSAL, [*found, EXAMPLE])) if (found := unexplained_urgency(texts)) else None


def refused_tool(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    reads = Reads(evt, which)
    owner, relayed = owner_lines(evt, reads), relay_lines(evt)
    if blocked := refused(evt, [*owner, *(text for _, text in relayed)]):
        return blocked
    if found := unexplained_escalations(reads, owner, relayed):
        return evt.block(fenced(FOREIGN_REFUSAL, [*found, f"example: {HIS_HARM}"]))
    return evt.context(reads.warning("urgent_lines_name_the_harm")) if reads.failures else None


def flagged(evt: BaseHookEvent, texts: list[str]) -> HookResult | None:
    return evt.context(fenced(BARE_NUMBERS, found)) if (found := bare_numbers(texts)) else None


def card(**fields: object) -> Input:
    return Input(tool=SLACK_CARD, tool_input={"card": fields}, env=DRIVE_CONFIG)


def updated(block: dict) -> str:
    return f"cc-present update-block - <<'EOF'\n{json.dumps(block)}\nEOF"


@on(  # capt-hook: no-escape the owner ordered every urgent line to him that names no harm refused
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", SLACK_CARD, POST_TOOL, *DM_TOOLS)],
    tests={
        card(title=UNEXPLAINED, body="This is urgent."): Block(pattern=r"^A line to the owner that calls something urgent[\s\S]*This is urgent\.[\s\S]*example: "),
        Input(command=f"cci post --kind urgent --to owner-relay --pr 46018 --text {json.dumps(ESCALATED)}", env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"^A line to the owner that calls something urgent"),
        Input(command=f"cci post --kind ask --to owner-relay --text {json.dumps(ESCALATED.replace('URGENT: ', ''))}", env=DRIVE_CONFIG, commands=WHOSE_STUB): Block(pattern=r"^A line that calls another person's PR urgent[\s\S]*#46018 is Andrew Benton \(andrew\)'s PR: [\s\S]*example: "),
        Input(command=f"cci post --kind urgent --to owner-relay --text {json.dumps(HIS_HARM)}", env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command=f"cci post --kind urgent --to owner-relay --text {json.dumps(HIS_HARM + ' Closed #2455 carried the first half.')}", env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command="cci post --kind report --to root --text \"not ours, Andrew's #46018 resolves #382199\"", env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command="cci post --kind report --to owner-relay --text \"12414 is Andrew's release and ran his #46018; no action\"", env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command="cci post --kind report --to owner-relay --text '#49336 lands with the release; its stores stay.'", env=DRIVE_CONFIG, commands=WHOSE_STUB): Allow(),
        Input(command="cci post --lane incident-sofi-1 --kind urgent --to owner-relay --text 'CRITICAL: SandSQL Below-Floor Database Count on SoFi, now.'", env=DRIVE_CONFIG): Allow(),
        card(title=UNEXPLAINED, body=NAMED): Allow(),
        card(title="Did the SoFi alert clear?", body='The "CRITICAL: sandsql latency" alert fired and cleared on its own.'): Allow(),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": UNEXPLAINED, "body": "This is urgent."}}): Allow(),
        Input(command=updated({"type": "card", "id": "resume-12414", "title": "Cancel the resumed release?", "summary": RESUMING}), env=DRIVE_CONFIG): Block(pattern=r"12401 IS RESUMING NOW"),
        Input(command=updated({"type": "card", "id": "resume-12414", "title": "Cancel the resumed release?", "summary": NAMED}), env=DRIVE_CONFIG): Allow(),
        Input(command="cc-present reply --block deploy-ask --body 'Merge it right now.'", env=DRIVE_CONFIG): Block(pattern=r"Merge it right now\."),
        Input(command="cc-present reply --block deploy-ask --body 'Merge it right now. Otherwise every customer loses the four teams columns for good.'", env=DRIVE_CONFIG): Allow(),
        Input(command="cci post --kind ask --to root --text 'Owner-relay: he needs to hear this now, it is urgent.'", env=DRIVE_CONFIG): Allow(),
        card(title="Merge the urgent-harm-1 hook?", body="Its checks passed."): Allow(),
        Input(tool=DM_TOOLS[0], tool_input={"channel_id": "D0B6Z", "thread_ts": "1.2", "text": "Andrew's deploy is urgent."}, state=ACTIVE, env=DRIVE_CONFIG): Block(),
        Input(tool=DM_TOOLS[0], tool_input={"channel_id": "D0B6Z", "thread_ts": "1.2", "text": "Andrew's deploy is urgent."}, env=DRIVE_CONFIG): Allow(),
    },
)
def urgent_lines_name_the_harm(evt: BaseHookEvent) -> HookResult | None:
    return refused_tool(evt)


@on(  # capt-hook: no-escape the owner ordered every urgent line to him that names no harm refused
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests=communicator_tests({
        Input(transcript=reply_transcript("Andrew's deploy is urgent. Can you look?"), state=ACTIVE): Block(pattern=r"Andrew's deploy is urgent\."),
        Input(transcript=reply_transcript(NAMED), state=ACTIVE): Allow(),
        Input(transcript=reply_transcript('The "Critical: Watch red" alert on SoFi cleared; nothing waits on you.'), state=ACTIVE): Allow(),
    }),
)
def urgent_replies_name_the_harm(evt: BaseHookEvent) -> HookResult | None:
    return refused(evt, [last_reply(evt)])


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", SLACK_CARD, *DM_TOOLS)],
    tests={
        card(title="Land #49335?"): Warn(pattern=r"^Warning, nothing was blocked: a migration[\s\S]*#49335: \"Land #49335\?\""),
        card(title="Land the chat-version fix, #49335?"): Allow(),
        Input(command="cc-present reply --block drop-ask --body '0453 ran.'", env=DRIVE_CONFIG): Warn(pattern=r"0453: "),
        Input(command="cc-present reply --block drop-ask --body '0453 ran: the four teams columns are gone.'", env=DRIVE_CONFIG): Allow(),
    },
)
def owner_numbers_carry_plain_words(evt: BaseHookEvent) -> HookResult | None:
    return flagged(evt, tool_lines(evt))


@on(
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests=communicator_tests({
        Input(transcript=reply_transcript("12414 is held. Approve 0453?"), state=ACTIVE): Warn(pattern=r"12414: [\s\S]*0453: "),
        Input(transcript=reply_transcript("Andrew's release 12414 waits on his approval to drop the four teams columns."), state=ACTIVE): Allow(),
    }),
)
def owner_reply_numbers_carry_plain_words(evt: BaseHookEvent) -> HookResult | None:
    return flagged(evt, [last_reply(evt)])
