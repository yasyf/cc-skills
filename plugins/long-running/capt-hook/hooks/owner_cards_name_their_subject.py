from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Call, CommandSchema, Event, HookResult, Input, Operand, Option, Tool, Warn, WorkflowState, on, workflow_state
from captain_hook.util import reqenv

from .board_text_gate import calls
from .call_dir import located
from .compaction_handoff import FOREGROUND_JUDGE_SECONDS, LR, Bound, CompactionState, script_module
from .judge_metrics import judge_call
from .owner_facing import CC_SLACK, SLACK_CARD, STDIN, Reads, as_read, board_bytes, fenced, readable, shown, slack_card, status_words
from .owner_lines_skip_refused_topics import reaches_him
from .owner_replies_carry_news import HIS_DM, PEER, dm_posts
from .owner_returns import REPLY
from .register_injection import drive_args
from .tests.board_fixtures import board, judged
from .tests.register_fixtures import DRIVE_CONFIG
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
TAP_WORDS = re.compile(r"(?i)\b(?:tap|taps|tapping|(?:wait|waits|waiting|blocked) (?:on|for) (?:you|your|a yes or no|yes or no)|needs? a yes or no|yes or no from you|your (?:yes|no|picks?|answers?|word))\b|\byes or no\?|(?<!release )\bcards?\b")
HIDDEN_CITE = re.compile(r"<!--.*?-->", re.DOTALL)
ALERT_HOST = r"[^)|>\s]*(?:sentry\.io/|datadoghq\.com/monitors|slack\.com/archives)[^)|>\s]*"
LABELLED_ALERT = re.compile(rf"\]\(<?https://{ALERT_HOST}>?\)|<https://{ALERT_HOST}\|")
INCIDENT = re.compile(r"(?i)\bincident\b")
BARE_ALERT = "Warning, nothing was blocked: an incident line carries the alert's Slack message and its Sentry or Datadog issue as bare URLs so Slack unfurls them, and a label hides the URL. Write each one out."
SHORT_WORDS = 12
HELD_CHARS = 400
CHANGE = re.compile(r"(?i)\b(?:add|chang|clean|delet|drop|fix|migrat|mov|refactor|remov|renam|replac|rewrit|split)\w*")
DRAW_SCRIPT = "draw.zsh"
NAMED_PR = re.compile(r"(?i)(?<![\w/&])#(\d{2,})\b|/pull/(\d+)|\bPR (\d+)\b")
PORTS_READ = f"{LR} board ports"
PORT_UNSAID = 'Warning, nothing was blocked: a post naming a forward port opens with "port of" and the number of the old-tree PR it copies. Open this one that way for PR {sources}.'
ASK_OWNER = CommandSchema(
    "lr",
    operands=(Operand("words", count="*"),),
    options=tuple(Option(name, (f"--{name}",)) for name in ("background", "branch", "card", "drive", "from", "id", "lane", "nothing-to-build", "option", "port-of", "reason", "session", "shot", "title", "until", "withdraw")),
)
ASK = "lr ask-owner --title 'Add the setting?' --background 'It adds one new setting for big chats.' --option 'Add it :: Big chats load faster.' --option 'Hold :: Nothing changes.' --nothing-to-build process"
HOOK_SCRATCH = str(Path(tempfile.gettempdir()) / "long-running-first-question")
HOOK_ENV = {"CLAUDE_CONFIG_DIR": HOOK_SCRATCH, "LONG_RUNNING_SCRATCH_ROOT": HOOK_SCRATCH, "CLAUDE_LONG_RUNNING_DRIVE": "first-question"}
LANE_ENV = HOOK_ENV | {"CLAUDE_LONG_RUNNING_LANE": "bugs-comms"}
LANE_DM_ENV = LANE_ENV | {"CLAUDE_CONFIG_DIR": DRIVE_CONFIG["CLAUDE_CONFIG_DIR"]}
VAGUE_POST = "It adds one new setting for big chats, and the sidebar stays as it is."
ABSTRACT = "It adds one new setting"
HIS_QUESTION = "Which fields, on which table, and only for old chats?"
HOLDS = judged(ABSTRACT, HIS_QUESTION)
CONCRETE = {"title": "Big chats open on their newest turns", "md": "A chat over 64 turns opens on its newest 64, and it adds `window_unsealed` to the chat's stored row. Older turns load as you scroll."}
LANDED = "Landed, live at 12:20 PM."
STATUS = "The nightly build is still running on the staging cluster, and it should finish within the hour."
REMINDER = "Six things still wait on your tap, so please open your board and go through them today."
WAITS_ON_HIM = "The support reply is separate and waits on your Yes or No."
THIRD_PARTY = "The new prompt asks the user yes or no before it deletes a chat, and the sidebar shows the result."
STATUS_POST = f"/plugins/cc-slack/bin/cc-slack dm-status --thread 1793829658.340949 --agent claude '{WAITS_ON_HIM}' 2>&1 | head -5"
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
    if DRAW_SCRIPT in {Path(word).name for word in (call.name, *call.args)}:
        return next((word for word in reversed(call.args) if word.endswith(".json")), None)
    return None


def card_text(evt: BaseHookEvent, call: Call, source: str, reads: Reads) -> list[str]:
    doc = reads.read(f"the card {source}", lambda: json.loads(located(evt, call, source).read_text()))
    return [text for _, text in shown(doc or {})]


def asked_lines(evt: BaseHookEvent, call: Call, reads: Reads) -> list[str]:
    values = ASK_OWNER.bind(call).values
    if values.get("words", ())[:1] != ("ask-owner",) or values.get("withdraw"):
        return []
    file = next(iter(values.get("card", ())), STDIN)
    flags = [str(text) for name in ("title", "background", "option") for text in values.get(name, ()) if text]
    flags += [f"port of #{source}" for source in values.get("port-of", ())]
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
    return "\n\n".join(HIDDEN_CITE.sub(" ", line) for line in lines if line.strip())


def draws_tap_cards(evt: BaseHookEvent) -> bool:
    if evt.tool_name != "Bash":
        return bool(evt._tool_input.get("tap_prs"))
    return any(CC_SLACK.bind(call).values.get("tap-pr") for call in evt.command.calls("cc-slack"))


def carries_cards(evt: BaseHookEvent, reads: Reads) -> bool:
    if evt.tool_name == REPLY:
        return bool(evt._tool_input.get("picks") or evt._tool_input.get("prs"))
    if draws_tap_cards(evt):
        return True
    if card := reads.read("the status card", lambda: slack_card(evt)):
        return bool(card.get("options"))
    return not (dm_posts(evt) or status_words(evt))


def lane_dm_words(evt: BaseHookEvent, reads: Reads) -> str:
    return "\n\n".join(text for channel, text in dm_posts(evt) if TAP_WORDS.search(readable(text)) and reaches_him(reads, channel))


def unlinked_or_cardless(evt: BaseHookEvent, posted: str, said: str, reads: Reads) -> list[str]:
    bare = [f"bare number: {hit}: make it a link, and name no cci record number at all" for hit in dict.fromkeys(found[0] for found in BARE_ID.finditer(posted))]
    named = TAP_WORDS.search(said)
    return [*bare, *([f'no cards: "{named[0]}": put the yes or no itself in this post, as `picks` or `prs` on a reply, `options` on a dm-status card, or `--tap-pr`; a link to a card only where the post cannot carry one; and if you removed the card, say so here with its replacement'] if named and not carries_cards(evt, reads) else [])]


def hold(evt: BaseHookEvent, drive: str, key: str, lines: list[str]) -> HookResult:
    with HeldPosts.mutate(evt) as state:
        state.held = [*state.held, key]
    JUDGE.record(drive, "first-question-hold", "none", "held", {"seconds": 0.0, "cost_usd": 0.0, "tokens": 0, "asks": 0, "p_yes": None, "error": ""}, " | ".join(lines)[:HELD_CHARS], env=reqenv.env_map())
    return evt.block(fenced(HOLD, lines))


def ports_of(drive: list[str], numbers: list[str]) -> dict[str, int]:
    done = subprocess.run([str(LR), "board", "ports", *drive, *numbers], capture_output=True, text=True, timeout=FOREGROUND_JUDGE_SECONDS, env=dict(reqenv.env_map()), check=True)
    return json.loads(done.stdout)


def card_about(title: str, body: str, commands: dict[str, object]) -> Input:
    return Input(tool=SLACK_CARD, tool_input={"card": {"title": title, "body": body}}, env=HOOK_ENV, commands=commands)


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", SLACK_CARD, *DM_TOOLS)],
    tests={
        card_about("Land #50254?", "Its checks passed.", {PORTS_READ: json.dumps({"50254": 49412})}): Warn(pattern=r"for PR 49412\."),
        card_about("port of #49412: land #50254?", "Its checks passed.", {PORTS_READ: json.dumps({"50254": 49412})}): Allow(),
        card_about("port of [#49412](https://github.com/Forge-AI/monorepo/pull/49412): land it?", "See [#50254](https://github.com/Forge-AI/monorepo/pull/50254).", {PORTS_READ: json.dumps({"50254": 49412})}): Allow(),
        card_about("Land it?", "See https://github.com/Forge-AI/monorepo/pull/50254.", {PORTS_READ: json.dumps({"50254": 49412})}): Warn(pattern=r"for PR 49412\."),
        card_about("Land #50333?", "Its checks passed.", {PORTS_READ: "{}"}): Allow(),
        card_about("Land #50254?", "Its checks passed.", {PORTS_READ: (1, "store unreadable")}): Warn(pattern=r"could not read"),
        card_about("Relay fix", "Its checks passed.", {}): Allow(),
        Input(command="lr ask-owner --title 'Land PR 50254?' --port-of 49412 --option 'Land :: It goes.' --option 'Hold :: It waits.' --nothing-to-build pick", env=HOOK_ENV, commands={PORTS_READ: json.dumps({"50254": 49412})}): Allow(),
        Input(command="lr ask-owner --title 'Land PR 50254?' --option 'Land :: It goes.' --option 'Hold :: It waits.' --nothing-to-build pick", env=HOOK_ENV, commands={PORTS_READ: json.dumps({"50254": 49412})}): Warn(pattern=r"for PR 49412\."),
        Input(command="ls -la", env=HOOK_ENV): Allow(),
    },
)
def posts_say_when_they_name_a_port(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    reads = Reads(evt, which)
    text = post_text(evt, reads)
    numbers = list(dict.fromkeys(next(filter(None, found)) for found in NAMED_PR.findall(text)))
    if not numbers:
        return None
    sources = reads.read("the PR ledger", lambda: ports_of(which, numbers))
    if sources is None:
        return evt.context(reads.warning("posts_say_when_they_name_a_port"))
    unsaid = [source for source in dict.fromkeys(sources.values()) if not re.search(rf"(?i)port of \[?#{source}\b", text)]
    return evt.context(PORT_UNSAID.format(sources=", ".join(map(str, unsaid)))) if unsaid else None


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
        reply("Your list", REMINDER): Block(pattern=r'no cards: "wait on your"'),
        reply("Your list", REMINDER, given={"prs": [LAND_PR]}, commands=judged()): Allow(),
        reply("Your list", REMINDER, given={"picks": ["Land", "Hold"], "recommended": "Land"}, commands=judged()): Allow(),
        reply("Your list", REMINDER, state=resent("Your list", REMINDER)): Allow(),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Your list", "body": REMINDER}}, env=HOOK_ENV): Block(pattern=r"no cards"),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Your list", "body": REMINDER, "options": ["Land", "Hold"]}}, env=HOOK_ENV, commands=judged()): Allow(),
        reply("Release", "The Platy release card is up, and it is live on plat.", commands=judged()): Allow(),
        reply("Alert", "Sentry 4821 fired again."): Block(pattern=r"bare number: Sentry 4821"),
        reply("Alert", f"[Sentry 4821]({SENTRY_LINK}) fired again."): Allow(),
        reply("Release", f"Live as [the relay fix]({LAND_PR}).", commands=HOLDS): Block(pattern=r"first question: Which fields"),
        reply("Release", f"Live as <{LAND_PR}|the relay fix>.", commands=HOLDS): Block(pattern=r"first question: Which fields"),
        reply("Alert", "Record #427030 says so."): Block(pattern=r"bare number: #427030"),
        reply("Why", "It dropped because both chats shared a replica. <!-- cci #384714 -->", commands=judged()): Allow(),
        Input(command="cc-slack reply --channel D0B6Z --text 'Sentry 4821 fired again.'", env=HOOK_ENV): Allow(),
        Input(tool=REPLY, tool_input={"title": "Big chats", "md": VAGUE_POST}, commands=HOLDS): Allow(),
        reply("Support reply", WAITS_ON_HIM): Block(pattern=r'no cards: "waits on your"'),
        reply("Support reply", "It needs your yes before it posts."): Block(pattern=r'no cards: "your yes"'),
        reply("Support reply", "This is blocked on you."): Block(pattern=r'no cards: "blocked on you"'),
        reply("Support reply", "Your pick decides the wording."): Block(pattern=r'no cards: "Your pick"'),
        reply("Support reply", "It holds for your answer."): Block(pattern=r'no cards: "your answer"'),
        reply("Support reply", "Say your word and it goes."): Block(pattern=r'no cards: "your word"'),
        reply("Support reply", "The reply waits on yes or no."): Block(pattern=r'no cards: "waits on yes or no"'),
        reply("Support reply", "Send the reply, yes or no?"): Block(pattern=r'no cards: "yes or no\?"'),
        reply("Support reply", WAITS_ON_HIM, given=PICKS, commands=judged()): Allow(),
        reply("Support reply", WAITS_ON_HIM, given={"prs": [LAND_PR]}, commands=judged()): Allow(),
        reply("Delete prompt", THIRD_PARTY, commands=judged()): Allow(),
        reply("Support reply", WAITS_ON_HIM, state=resent("Support reply", WAITS_ON_HIM)): Allow(),
        Input(command=STATUS_POST, env=LANE_ENV): Block(pattern=r'no cards: "waits on your"'),
        Input(command=f"cc-slack dm-status --tap-pr {LAND_PR} '{WAITS_ON_HIM}'", env=LANE_ENV, commands=judged()): Allow(),
        Input(command=f"cc-slack dm-status --about ask-1 '{WAITS_ON_HIM}'", env=LANE_ENV): Allow(),
        Input(tool=SLACK_CARD, tool_input={"text": WAITS_ON_HIM}, env=LANE_ENV): Block(pattern=r"no cards"),
        Input(tool=SLACK_CARD, tool_input={"text": WAITS_ON_HIM, "tap_prs": [LAND_PR]}, env=LANE_ENV, commands=judged()): Allow(),
        Input(tool=DM_TOOLS[1], tool_input={"channel_id": "D0B6Z", "text": WAITS_ON_HIM}, env=LANE_DM_ENV, commands={PEER: HIS_DM}): Block(pattern=r"no cards"),
        Input(tool=DM_TOOLS[1], tool_input={"channel_id": "D0B6Z", "text": WAITS_ON_HIM}, env=LANE_DM_ENV, commands={PEER: json.dumps({"channel_id": "D0B6Z", "peer": "U0SOMEONE", "peer_name": "Sam"})}): Allow(),
        Input(tool=DM_TOOLS[1], tool_input={"channel_id": "C0RELEASES1", "text": WAITS_ON_HIM}, env=LANE_DM_ENV): Allow(),
    },
)
def posts_answer_his_first_question(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    reads = Reads(evt, which)
    posted = post_text(evt, reads)
    said = posted or lane_dm_words(evt, reads)
    if not said or (key := post_key(as_read(said))) in HeldPosts.load(evt).held:
        return None
    if lines := unlinked_or_cardless(evt, readable(posted), readable(said), reads):
        return hold(evt, which[1], key, lines)
    text = as_read(posted)
    if plain_answer(text):
        return None
    env = reqenv.env_map()
    verdict = judge_call(JUDGE, "first-question", picked_out(text), text, backend=JUDGE.backend_from(env), drive=which[1], timeout=reqenv.clamp_timeout(FOREGROUND_JUDGE_SECONDS), env=env)
    if verdict is None or not (verdict.flagged or verdict.verdict == "unsure" and describes_change(text)):
        return evt.context(reads.warning("posts_answer_his_first_question")) if reads.failures else None
    return hold(evt, which[1], key, [f'sentence: "{verdict.artifact_phrase}"', f"first question: {verdict.sentence}"])


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool(REPLY, SLACK_CARD, *DM_TOOLS)],
    tests={
        reply("Incident: run timeline", f"It fails. [Sentry 4821]({SENTRY_LINK})"): Warn(pattern=r"bare URLs"),
        reply("Incident: run timeline", f"It fails. <{SENTRY_LINK}|Sentry 4821>"): Warn(pattern=r"bare URLs"),
        reply("Incident: run timeline", "It fails. [alert](https://acme.slack.com/archives/C1/p1793833513167859)"): Warn(pattern=r"bare URLs"),
        reply("Incident: run timeline", f"It fails. {SENTRY_LINK} https://acme.slack.com/archives/C1/p1793833513167859"): Allow(),
        reply("Incident: run timeline", f"It fails. <{SENTRY_LINK}>"): Allow(),
        reply("Alert", f"[Sentry 4821]({SENTRY_LINK}) fired again."): Allow(),
        reply("Incident: run timeline", f"Live as [the relay fix]({LAND_PR})."): Allow(),
    },
)
def incident_lines_carry_bare_links(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    posted = post_text(evt, Reads(evt, which))
    return evt.context(BARE_ALERT) if INCIDENT.search(posted) and LABELLED_ALERT.search(posted) else None
