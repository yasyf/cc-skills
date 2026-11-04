from __future__ import annotations

import json
import re
from pathlib import Path

from spawnllm import Binary, BinaryAnswer, Refused

from captain_hook import Allow, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, T, Tool, on

from .compaction_handoff import Communicator, CompactionState, communicator_tests, drive_entry
from .owner_facing import REPLY_WINDOW, last_reply
from .owner_replies_carry_news import dm_posts
from .owner_returns import EXPLAINER, REPLY, SHIPPED_CC_SLACK, OwnerReturns, dm_owed
from .tests.handoff_fixtures import reply_transcript
from .tests.register_fixtures import DRIVE_CONFIG
from .urgent_lines_name_the_harm import DM_TOOLS, sentences

ACK = re.compile(
    r"(?i)^(?:ok(?:ay)?|sure|yes|yep|got it|noted|understood|ack(?:nowledged)?|roger|will do|thanks?(?: you)?"
    r"|on it|doing (?:it|that|so)|(?:looking (?:into|at)|working on|fixing) (?:it|that|this)|(?:being )?fixed|launched [\w-]+ (?:for|on) (?:it|that|this))"
    r"(?: now)?\W*$"
)
PROMISE = re.compile(
    r"(?i)\b(?:within|in|by|under)\s+(?:about\s+)?(?:\d+|a few|an?|one|two|three|five|ten|fifteen|twenty|thirty)\s*(?:min(?:ute)?s?|hours?)\b"
    r"|\bby\s+\d{1,2}(?::\d\d)?\s?[ap]\.?m\b|\bETA\b|\byou(?:'ll| will) (?:get|have|hear|see)\b"
)
LIVE = re.compile(r"(?i)\b(?:is|are|went|now) live\b")
UPDATE = re.compile(r"^# Owner update:", re.MULTILINE)
DIAGRAM = re.compile(r"```(?:mermaid|arch)\b")
MECHANISM = Binary(
    "The assistant's reply to the owner is below."
    " Yes when it explains a mechanism, an incident or a design change to him: how something works, why something failed, or what a design now does differently."
    " No for a status line, a list of what landed or comes next, a question, an acknowledgement, a pick, or an answer that explains no mechanism."
)
ACKED = "An order gets no acknowledgement and no promised time: he hears back once, when it is live or decided, in one sentence. End this turn with no text, or `slack_react` +1 on his Slack message."
LONG = "A reply to him is at most two sentences, and an answer to his question is one; only a scheduled update, a return update or a pick card runs longer. Cut it to what is live or decided."
UNASKED = "He did not ask how or why, so the reply carries no mechanism, diagram or explainer link: say what is fixed or decided and when it is live."
ACTIVE = [CompactionState(active=True, communicator=True)]
CALM = {"mechanism": BinaryAnswer(p_yes=0.02, confidence=0.95)}
EXPLAINS = {"mechanism": BinaryAnswer(p_yes=0.97, confidence=0.94)}
ACKS = "Being fixed now; you get the live time within five minutes."
LIVE_NOW = "The typing box fix is live: your drafts stay put when a reply lands."
WORDY = "The typing box lost drafts. A render reset the input. The fix keeps the draft in the store. It is live now."
WHY = "The typing box lost your draft because each render reset its input."
PAGE = "Fixed and live. The details: https://mac.tail1.ts.net:8665/one-pagers/typing-box/"
UPDATE_MD = "# Owner update: brook, 10:40 PM\n\n## Workstreams\n\n| Workstream | State |\n| --- | --- |\n| Dashboard | typing box fix live |\n\nThree PRs merged. Two lanes are live. One waits on you."
QUIET = frozenset({"progress", "info"})
RECEIPT = "Its checks passed.\n\n- Land it (recommended)\n- Hold it\n\nReply r1; pass this id to edit it on every surface."


def said(text: str, **given: object) -> Input:
    return Input(transcript=reply_transcript(text), **({"state": ACTIVE, "decide": CALM} | given))


def replied(md: str, picks: tuple[str, ...] = (), ledger: dict | None = None, **given: object) -> Input:
    card = {"picks": list(picks), "recommended": picks[0]} if picks else {}
    return Input(tool=REPLY, tool_input={"title": "Typing box", "md": md, **card, **(ledger or {})}, **({"state": ACTIVE, "decide": CALM} | given))


def breaches(evt: BaseHookEvent, text: str, card: bool) -> list[str]:
    parts = sentences(text)
    found = [ACKED] if parts and all(ACK.match(part) or (PROMISE.search(part) and not LIVE.search(part)) for part in parts) else []
    if len(parts) > 2 and not (card or UPDATE.search(text) or OwnerReturns.load(evt).returned):
        found.append(LONG)
    if not (card or answering_him(evt)) and (EXPLAINER.search(text) or DIAGRAM.search(text) or explains(evt, text)):
        found.append(UNASKED)
    return found


def answering_him(evt: BaseHookEvent) -> bool:
    returns = OwnerReturns.load(evt)
    owed = dm_owed(evt)
    return returns.explain_due or returns.asked_why or bool(owed and owed["replies"])


def explains(evt: BaseHookEvent, text: str) -> bool:
    match (decision := evt.decide(text, {"mechanism": MECHANISM})) and decision.answers["mechanism"]:
        case BinaryAnswer(p_yes=p_yes) if p_yes > 0.5:
            return True
    return False


def is_pick_card(evt: BaseHookEvent) -> bool:
    given = evt._tool_input
    if given.get("picks"):
        return True
    entry = given.get("id") and drive_entry(evt)
    return bool(entry) and has_picks(Path(entry["state_dir"]) / "replies" / f"{given['id']}.json")


def has_picks(stored: Path) -> bool:
    return stored.exists() and bool(json.loads(stored.read_text()).get("picks"))


def refused(evt: BaseHookEvent, texts: list[str], card: bool = False) -> HookResult | None:
    found = [rule for text in texts if text.strip() for rule in breaches(evt, text, card)]
    return evt.block(" ".join(dict.fromkeys(found))) if found else None


@on(  # capt-hook: no-escape the owner ordered every chatty reply refused before it reaches him
    Event.PreToolUse,
    only_if=[Communicator(), Tool("Bash", REPLY, *DM_TOOLS)],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        replied(ACKS): Block(pattern=r"^An order gets no acknowledgement and no promised time"),
        replied("On it."): Block(pattern=r"no acknowledgement"),
        replied(LIVE_NOW): Allow(),
        replied("Yes, the relay keeps one replica per chat."): Allow(),
        replied(WORDY): Block(pattern=r"^A reply to him is at most two sentences"),
        replied(WORDY, picks=("Land it", "Hold it")): Allow(),
        replied(UPDATE_MD): Allow(),
        replied(WORDY, state=[*ACTIVE, OwnerReturns(returned=True)]): Allow(),
        replied(WHY, decide=EXPLAINS): Block(pattern=r"^He did not ask how or why"),
        replied(WHY, decide=EXPLAINS, state=[*ACTIVE, OwnerReturns(explain_due=True)]): Allow(),
        replied(WHY, picks=("Build it", "Hold it"), decide=EXPLAINS): Allow(),
        replied(WHY, decide=EXPLAINS, state=[*ACTIVE, OwnerReturns(asked_why=True)]): Allow(),
        replied(WHY, decide=EXPLAINS, env=DRIVE_CONFIG, commands={f"{SHIPPED_CC_SLACK} dm-owed": json.dumps({"replies": [{"ts": "1.2"}], "holder_session": "s"})}): Allow(),
        Input(tool=REPLY, tool_input={"id": "r1", "title": "Typing box", "md": WHY}, state=ACTIVE, decide=EXPLAINS): Block(pattern=r"^He did not ask how or why"),
        replied(WHY, decide={"mechanism": Refused()}): Allow(),
        replied(PAGE): Block(pattern=r"no mechanism, diagram or explainer link"),
        replied("Fixed and live.\n```mermaid\ngraph LR; a-->b\n```"): Block(pattern=r"no mechanism, diagram"),
        replied("Fixed and live.\n```arch\n{\"arch\":1}\n```"): Block(pattern=r"no mechanism, diagram"),
        Input(tool=DM_TOOLS[0], tool_input={"channel_id": "D0B6Z", "thread_ts": "1.2", "text": ACKS}, state=ACTIVE, decide=CALM): Block(pattern=r"no acknowledgement"),
        Input(tool=DM_TOOLS[0], tool_input={"channel_id": "C0RELEASES1", "thread_ts": "1.2", "text": ACKS}, state=ACTIVE, decide=CALM): Allow(),
        Input(command=f"cc-slack send --channel D0B6Z --text '{WORDY}'", state=ACTIVE, decide=CALM): Block(pattern=r"at most two sentences"),
        Input(command="cci post --lane owner-relay --kind note --text 'On it.'", state=ACTIVE, decide=CALM): Allow(),
        replied(ACKS, agent_id="a1b2c3"): Allow(),
        replied("Due by 11:30 PM.", ledger={"order": "m447", "status": "progress", "by": "11:30 PM"}): Allow(),
        replied("Due by 11:30 PM.", ledger={"order": "m447", "status": "done"}): Block(pattern=r"no acknowledgement"),
    }),
)
def owner_writes_stay_short(evt: BaseHookEvent) -> HookResult | None:
    if evt.tool_name == REPLY:
        if evt._tool_input.get("order") and evt._tool_input.get("status") in QUIET and not evt._tool_input.get("picks"):
            return None
        return refused(evt, [str(evt._tool_input.get("md") or "")], card=is_pick_card(evt))
    return refused(evt, [text for _, text in dm_posts(evt)])


@on(  # capt-hook: no-escape the owner ordered every chatty reply refused before it reaches him
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests=communicator_tests({
        said(ACKS): Block(pattern=r"^An order gets no acknowledgement"),
        said(LIVE_NOW): Allow(),
        said(WORDY): Block(pattern=r"^A reply to him is at most two sentences"),
        said(WHY, decide=EXPLAINS): Block(pattern=r"^He did not ask how or why"),
        Input(transcript=[T.user("land the relay fix"), *T.tool_turn(REPLY, result=RECEIPT, title="Land it?", md="Its checks passed.", picks=["Land it", "Hold it"], recommended="Land it"), T.assistant(RECEIPT)], state=ACTIVE, decide=CALM): Allow(),
        said(ACKS, agent_id="a1b2c3"): Allow(),
    }),
)
def owner_replies_stay_short(evt: BaseHookEvent) -> HookResult | None:
    if any(use.call.name == REPLY and use.result and not use.result.is_error for use in evt.ctx.turn.tool_calls.items):
        return None
    return refused(evt, [last_reply(evt)])
