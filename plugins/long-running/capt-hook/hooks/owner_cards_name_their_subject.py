from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Call, CommandSchema, Event, HookResult, Input, Operand, Option, Tool, Warn, WorkflowState, on, workflow_state
from captain_hook.util import reqenv

from .board_text_gate import calls
from .compaction_handoff import FOREGROUND_JUDGE_SECONDS, Bound, CompactionState, script_module
from .judge_metrics import judge_call
from .owner_facing import SLACK_CARD, STDIN, Reads, board_bytes, fenced, readable, shown, slack_card
from .owner_replies_carry_news import dm_posts
from .owner_returns import REPLY
from .register_injection import drive_args
from .tests.board_fixtures import board, judged
from .urgent_lines_name_the_harm import DM_TOOLS, owner_lines, sentences

BOUND = [CompactionState(active=True, coordinator=True)]
PICKS = {"picks": ["Keep them ours", "Open them"], "recommended": "Keep them ours"}
LAND_PR = "https://github.com/yasyf/cc-skills/pull/2196"

JUDGE = script_module("long_running/judge")
NAMES_PR = re.compile(r"(?i)(?<![\w/&])#\d{2,}\b|/pull/\d+|\bPR \d+\b")
UNNAMED = 'Name the feature: a card\'s title and first sentence say what it is about, and this one opens with "{opener}". Edit the reply to name it.'
BARE_PR = "A pick card about a PR passes it in `prs`, which gives him its link, CI, description and four lines: edit the reply to add the PR's url to `prs`."
HOLD = "A post to the owner links what it names, carries the cards it asks him to tap, names the concrete things it changes and answers his first question. Fix each line below, or send the same post again unchanged and it goes through."
BARE_ID = re.compile(r"(?i)(?<![\w/.:-])#?\d{6,}\b|\bSentry\s+#?\d+\b")
TAP_WORDS = re.compile(r"(?i)\b(?:tap|taps|tapping|waiting on you|waits on you)\b|(?<!release )\bcards?\b")
HIDDEN_CITE = re.compile(r"<!--.*?-->", re.DOTALL)
SHORT_WORDS = 12
CHANGE = re.compile(r"(?i)\b(?:add|chang|clean|delet|drop|fix|migrat|mov|refactor|remov|renam|replac|rewrit|split)\w*")
DRAW_SCRIPT = "draw.zsh"
ASK_OWNER = CommandSchema(
    "lr",
    operands=(Operand("words", count="*"),),
    options=tuple(Option(name, (f"--{name}",)) for name in ("background", "branch", "card", "drive", "from", "id", "lane", "nothing-to-build", "option", "reason", "session", "shot", "title", "until", "withdraw")),
)
ASK = "lr ask-owner --title 'Add the setting?' --background 'It adds one new setting for big chats.' --option 'Add it :: Big chats load faster.' --option 'Hold :: Nothing changes.' --nothing-to-build process"
HOOK_SCRATCH = str(Path(tempfile.gettempdir()) / "long-running-first-question")
HOOK_ENV = {"CLAUDE_CONFIG_DIR": HOOK_SCRATCH, "LONG_RUNNING_SCRATCH_ROOT": HOOK_SCRATCH, "CLAUDE_LONG_RUNNING_DRIVE": "first-question"}
VAGUE_POST = "It adds one new setting for big chats, and the sidebar stays as it is."
ABSTRACT = "It adds one new setting"
HIS_QUESTION = "Which fields, on which table, and only for old chats?"
HOLDS = judged(ABSTRACT, HIS_QUESTION)
CONCRETE = {"title": "Big chats open on their newest turns", "md": "A chat over 64 turns opens on its newest 64, and it adds `window_unsealed` to the chat's stored row. Older turns load as you scroll."}
LANDED = "Landed, live at 12:20 PM."
STATUS = "The nightly build is still running on the staging cluster, and it should finish within the hour."
REMINDER = "Six things still wait on your tap, so please open your board and go through them today."
SENTRY_LINK = "https://poetic.sentry.io/issues/4821"


def sonnet_says(verdict: str, evidence: str = "") -> dict[str, str]:
    answer = {"verdict": verdict, "artifact_phrase": ABSTRACT, "evidence_phrase": evidence, "sentence": HIS_QUESTION}
    return {**HOLDS, "claude -p": json.dumps({"structured_output": answer, "total_cost_usd": 0.01, "usage": {}})}


@workflow_state("long_running_first_question")
class HeldPosts(WorkflowState):
    held: list[str] = []


def given(evt: BaseHookEvent, field: str) -> str:
    return str(evt._tool_input.get(field) or "")


def is_card(evt: BaseHookEvent) -> bool:
    return bool(evt._tool_input.get("picks") or evt._tool_input.get("prs"))


def unnamed_opener(evt: BaseHookEvent) -> str | None:
    parts = sentences(given(evt, "md"))
    for text in (given(evt, "title"), parts[0] if parts else ""):
        if match := JUDGE.VAGUE_OPENER.match(text):
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


def drawn_file(call: Call) -> str | None:
    if call.name == "lr" and call.args[:2] == ("board", "draw"):
        return next((word for word in call.args[2:] if word.endswith(".json")), None)
    if DRAW_SCRIPT in {Path(word).name for word in (call.name, *call.args)}:
        return next((word for word in reversed(call.args) if word.endswith(".json")), None)
    return None


def card_text(evt: BaseHookEvent, call: Call, source: str, reads: Reads) -> list[str]:
    doc = reads.read(f"the card {source}", lambda: json.loads(Path(str(call.cwd or evt.cwd), Path(source).expanduser()).read_text()))
    return [text for _, text in shown(doc or {})]


def asked_lines(evt: BaseHookEvent, call: Call, reads: Reads) -> list[str]:
    values = ASK_OWNER.bind(call).values
    if values.get("words", ())[:1] != ("ask-owner",) or values.get("withdraw"):
        return []
    file = next(iter(values.get("card", ())), STDIN)
    flags = [str(text) for name in ("title", "background", "option") for text in values.get(name, ()) if text]
    return flags if file == STDIN else [*flags, *card_text(evt, call, str(file), reads)]


def urgent_asks(evt: BaseHookEvent, reads: Reads) -> list[str]:
    docs = [reads.read(f"the asked board {source}", lambda call=call, source=source, piped=piped: json.loads(board_bytes(evt, call, source, piped))) for call, _, source, piped in calls(evt, ("ask",)) if source]
    return [text for doc in docs if doc for _, text in shown(doc)]


def command_lines(evt: BaseHookEvent, reads: Reads) -> list[str]:
    drawn = [text for call in evt.command.calls() if (file := drawn_file(call)) for text in card_text(evt, call, file, reads)]
    asked = [text for call in evt.command.calls("lr") for text in asked_lines(evt, call, reads)]
    return [*drawn, *asked, *urgent_asks(evt, reads)]


def post_text(evt: BaseHookEvent, reads: Reads) -> str:
    if evt.tool_name == REPLY:
        lines = [given(evt, "title"), given(evt, "md"), given(evt, "action"), *map(str, evt._tool_input.get("picks") or ())]
    else:
        lines = [*owner_lines(evt, reads), *(command_lines(evt, reads) if evt.tool_name == "Bash" else ())]
    return "\n\n".join(readable(HIDDEN_CITE.sub(" ", line)) for line in lines if line.strip())


def carries_cards(evt: BaseHookEvent, reads: Reads) -> bool:
    if evt.tool_name == REPLY:
        return bool(evt._tool_input.get("picks") or evt._tool_input.get("prs"))
    if card := reads.read("the status card", lambda: slack_card(evt)):
        return bool(card.get("options"))
    return not dm_posts(evt)


def unlinked_or_cardless(evt: BaseHookEvent, text: str, reads: Reads) -> list[str]:
    bare = [f"bare number: {hit}: make it a link, and name no cci record number at all" for hit in dict.fromkeys(found[0] for found in BARE_ID.finditer(text))]
    named = TAP_WORDS.search(text)
    return [*bare, *([f'no cards: "{named[0]}": re-post each thing that waits on him as its card, in `picks` or `prs`'] if named and not carries_cards(evt, reads) else [])]


def hold(evt: BaseHookEvent, key: str, lines: list[str]) -> HookResult:
    with HeldPosts.mutate(evt) as state:
        state.held = [*state.held, key]
    return evt.block(fenced(HOLD, lines))


def describes_change(text: str) -> bool:
    return bool(CHANGE.search(text) or JUDGE.VAGUE.search(text))


def plain_answer(text: str) -> bool:
    return len(text.split()) < SHORT_WORDS and "?" not in text and not describes_change(text)


def post_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def picked_out(text: str) -> str:
    hits = list(dict.fromkeys(hit[0] for hit in JUDGE.VAGUE.finditer(text)))
    return f"A pattern picked these out of the post as possibly vague: {', '.join(hits)}." if hits else "A pattern picked no vague words out of the post."


def reply(title: str, md: str, **fields: object) -> Input:
    return Input(tool=REPLY, tool_input={"title": title, "md": md} | fields.pop("given", {}), env=HOOK_ENV, **fields)


def resent(title: str, md: str) -> list[HeldPosts]:
    return [HeldPosts(held=[post_key(f"{title}\n\n{md}")])]


@on(  # capt-hook: no-escape the same post sent again goes through, so the hold is its own way past
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", REPLY, SLACK_CARD, *DM_TOOLS)],
    tests={
        reply("Big chats", VAGUE_POST, commands=HOLDS): Block(pattern=r"^A post to the owner links what it names[\s\S]*It adds one new setting[\s\S]*Which fields, on which table"),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Add the setting?", "body": VAGUE_POST}}, env=HOOK_ENV, commands=HOLDS): Block(pattern=r"first question: Which fields"),
        Input(command=ASK, env=HOOK_ENV, commands=HOLDS): Block(pattern=r"first question: Which fields"),
        Input(command=f"lr ask-owner --card {board('clean-card.json')} --until at:3d", env=HOOK_ENV, commands=HOLDS): Block(),
        Input(command="lr ask-owner --withdraw card-add-the-setting --lane first-question", env=HOOK_ENV, commands=HOLDS): Allow(),
        Input(command=f"cc-present update-block {board('clean-card.json')}", env=HOOK_ENV, commands=HOLDS): Block(),
        Input(command=f"CARD_NO_CODE=1 zsh draw.zsh --new-question 'not settled' {board('clean-card.json')}", env=HOOK_ENV, commands=HOLDS): Block(),
        Input(command=f"lr board draw {board('clean-card.json')} --lane board-refresher", env=HOOK_ENV, commands=HOLDS): Block(),
        Input(command=f"cc-present ask {board('clean-card.json')}", env=HOOK_ENV, commands=HOLDS): Block(),
        reply("Added fields", "Added a few fields.", commands=HOLDS): Block(),
        reply("Big chats", VAGUE_POST, commands=HOLDS, state=resent("Big chats", VAGUE_POST)): Allow(),
        reply("Big chats", f"{VAGUE_POST} Reworded.", commands=HOLDS, state=resent("Big chats", VAGUE_POST)): Block(),
        reply(CONCRETE["title"], CONCRETE["md"], commands=judged()): Allow(),
        reply("Released", LANDED, commands=HOLDS): Allow(),
        reply("Big chats", VAGUE_POST, commands=sonnet_says("unsure")): Block(pattern=r"It adds one new setting[\s\S]*Which fields"),
        reply("Big chats", VAGUE_POST, commands=sonnet_says("yes", evidence="words no record carries")): Block(pattern=r"Which fields"),
        reply("Nightly build", STATUS, commands=sonnet_says("unsure")): Allow(),
        reply("Big chats", VAGUE_POST, commands=sonnet_says("no")): Allow(),
        reply("Big chats", VAGUE_POST, commands={"uv run --no-project --quiet": (1, "overloaded")}): Allow(),
        reply("Big chats", VAGUE_POST, commands={**HOLDS, "claude -p": (1, "claude: overloaded")}): Allow(),
        Input(command="cci post --kind note --to meta-desk --text 'It adds one new setting for big chats, and the sidebar stays as it is.'", env=HOOK_ENV, commands=HOLDS): Allow(),
        Input(command="ls -la", env=HOOK_ENV, commands=HOLDS): Allow(),
        reply("Your list", REMINDER): Block(pattern=r"no cards: \"tap\""),
        reply("Your list", REMINDER, given={"prs": [LAND_PR]}, commands=judged()): Allow(),
        reply("Your list", REMINDER, given={"picks": ["Land", "Hold"], "recommended": "Land"}, commands=judged()): Allow(),
        reply("Your list", REMINDER, state=resent("Your list", REMINDER)): Allow(),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Your list", "body": REMINDER}}, env=HOOK_ENV): Block(pattern=r"no cards"),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Your list", "body": REMINDER, "options": ["Land", "Hold"]}}, env=HOOK_ENV, commands=judged()): Allow(),
        reply("Release", "The Platy release card is up, and it is live on plat.", commands=judged()): Allow(),
        reply("Alert", "Sentry 4821 fired again."): Block(pattern=r"bare number: Sentry 4821"),
        reply("Alert", f"[Sentry 4821]({SENTRY_LINK}) fired again."): Allow(),
        reply("Alert", "Record #427030 says so."): Block(pattern=r"bare number: #427030"),
        reply("Why", "It dropped because both chats shared a replica. <!-- cci #384714 -->", commands=judged()): Allow(),
        Input(command="cc-slack reply --channel D0B6Z --text 'Sentry 4821 fired again.'", env=HOOK_ENV): Allow(),
        Input(tool=REPLY, tool_input={"title": "Big chats", "md": VAGUE_POST}, commands=HOLDS): Allow(),
    },
)
def posts_answer_his_first_question(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    reads = Reads(evt, which)
    text = post_text(evt, reads)
    if not text or (key := post_key(text)) in HeldPosts.load(evt).held:
        return None
    if lines := unlinked_or_cardless(evt, text, reads):
        return hold(evt, key, lines)
    if plain_answer(text):
        return None
    env = reqenv.env_map()
    verdict = judge_call(JUDGE, "first-question", picked_out(text), text, backend=JUDGE.backend_from(env), drive=which[1], timeout=reqenv.clamp_timeout(FOREGROUND_JUDGE_SECONDS), env=env)
    if verdict is None or not (verdict.flagged or verdict.verdict == "unsure" and describes_change(text)):
        return evt.context(reads.warning("posts_answer_his_first_question")) if reads.failures else None
    return hold(evt, key, [f'sentence: "{verdict.artifact_phrase}"', f"first question: {verdict.sentence}"])
