from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Call, CommandSchema, Event, HookResult, Input, Operand, Option, Rewrite, Tool, Warn, on
from captain_hook.util import reqenv

from .board_text_gate import calls
from .call_dir import located
from .compaction_handoff import FOREGROUND_JUDGE_SECONDS, LR, Bound, CompactionState, script_module
from .judge_metrics import judge_call
from .owner_facing import CC_SLACK, HIDDEN_CITE, SLACK_CARD, STDIN, Reads, as_read, board_bytes, fenced, readable, shown, slack_card, status_words
from .owner_lines_skip_refused_topics import reaches_him
from .owner_posts_carry_cards import CARD_LINK
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
UNNAMED = 'Name the feature: a card\'s title and first sentence say what it is about, and this one opens with "{opener}". Edit the reply to name it.'
UNANSWERED = "A post to the owner names the concrete things it changes and answers the first question he will ask, so nothing posted. Answer the question below in the text and send again, or open a line with `Unproven:` for a fact nobody has yet."
BARE_ID = re.compile(r"(?i)(?<![\w/.:-])#?\d{6,}\b|\bSentry\s+#?\d+\b")
RECORD_NUMBER = re.compile(r"(?is)(<!--.*?-->|\[[^\]]*\]\([^)]*\)|https?://\S+|\bSentry\s+#?\d+\b)|\s*\(#?(\d{6,})\)|\s*(?<![\w/.:=-])#?(\d{6,})\b(?!\.\d)")
RECORDS_MOVED = "Warning, nothing was blocked: {numbers} moved out of what he reads into a hidden cite, since a cci record number has no page. If one is a real count, edit this reply with its `id`."
BARE_NUMBER = "Nothing posted: a bare id shows him no page. Make each one below a link, and name no cci record number at all."
TAP_WORDS = re.compile(r"(?i)\b(?:tap|taps|tapping|(?:wait|waits|waiting|blocked) (?:on|for) (?:you|your|a yes or no|yes or no)|needs? a yes or no|yes or no from you|your (?:yes|no|picks?|answers?|word))\b|\byes or no\?|(?<!release )\bcards?\b")
CARDLESS = "Nothing posted: the words below read as asking him to tap and this post carries no card. If something waits on him, pass it in `picks` or `prs`, or `options` or `--tap-pr` on a lane's status card, and edit this reply with its `id`."
ALERT_HOST = r"[^)|>\s]*(?:sentry\.io/|datadoghq\.com/monitors|slack\.com/archives)[^)|>\s]*"
LABELLED_ALERT = re.compile(rf"\]\(<?https://{ALERT_HOST}>?\)|<https://{ALERT_HOST}\|")
INCIDENT = re.compile(r"(?i)\bincident\b")
BARE_ALERT = "Nothing posted: an incident line carries the alert's Slack message and its Sentry or Datadog issue as bare URLs so Slack unfurls them, and a label hides the URL. Write each one out."
SHORT_WORDS = 12
WHOLE_POST = "He reads the post below whole, with its urls, links and picks."
CHANGE = re.compile(r"(?i)\b(?:add|chang|clean|delet|drop|fix|migrat|mov|refactor|remov|renam|replac|rewrit|split)\w*")
DRAW_SCRIPT = "draw.zsh"
NAMED_PR = re.compile(r"(?i)(?<![\w/&])#(\d{2,})\b|/pull/(\d+)|\bPR (\d+)\b")
PORTS_READ = f"{LR} board ports"
PORT_OPENER = "Port, old tree to new: dev "
PORT_UNSAID = f"Nothing posted: a post naming a forward port opens its title with `{PORT_OPENER}#` and the number of the dev PR it ports. Open this one that way for PR {{sources}}."
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
SET_OPTIONS = "set-options"
SET_EVIDENCE = "A coding agent is about to send this card to the owner. Each url in `prs` draws on the card with its title and what it changes for a user, and its `diagram`, an architecture doc, draws as the architecture diagram component."
SET_UNNAMED = "Nothing posted: this card leaves out what he needs to pick. Give each set member its PR link and what it does for a user, pass `diagram` when it compares designs, name the branch of a code pick in `build`, and say what a thing the pick splits or shares is for, from the model doc."
BUGS_TITLE = "Pending next-tree bugs: what each needs from you"
BUGS_BODY = "Seven things wait on you, and the old-chat blocker waits on lanes."
BUGS_OPTIONS = ["Tap all four ready fixes", "Tap them after the old-chat fixes land", "Hold all of them"]
BUGS_PICKS = {"picks": BUGS_OPTIONS, "recommended": BUGS_OPTIONS[0]}
BUGS_CARD = json.dumps({"title": BUGS_TITLE, "body": BUGS_BODY, "options": BUGS_OPTIONS})
UNBUILT_PICK = judged(BUGS_OPTIONS[0], "The recommended option needs code and this card names no branch: it would add the retry.")
UNNAMED_SET = judged(BUGS_OPTIONS[0], "Name each of the four ready fixes, with its pull request link and what it does for a user.")


def sonnet_says(verdict: str, evidence: str = "") -> dict[str, str]:
    answer = {"verdict": verdict, "artifact_phrase": ABSTRACT, "evidence_phrase": evidence, "sentence": HIS_QUESTION}
    return {**HOLDS, "claude -p": json.dumps({"structured_output": answer, "total_cost_usd": 0.01, "usage": {}})}


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
        Input(tool=REPLY, tool_input={"title": "This adds three pieces of API. Keep them to our own apps?", "md": "Our apps call them.", **PICKS}, state=BOUND): Block(pattern=r'Name the feature:[\s\S]*"This"'),
        Input(tool=REPLY, tool_input={"title": "Keep the board API private?", "md": "It adds three calls.", **PICKS}, state=BOUND): Block(pattern=r'"It"'),
        Input(tool=REPLY, tool_input={"title": "The relay fix", "md": "The change is live.", "prs": [LAND_PR]}, state=BOUND): Block(pattern=r'"The change"'),
        Input(tool=REPLY, tool_input={"title": "Keep the board API private?", "md": "The board API adds three calls.", **PICKS}, state=BOUND): Allow(),
        Input(tool=REPLY, tool_input={"title": "This landed", "md": "It is live."}, state=BOUND): Allow(),
    },
)
def cards_name_their_subject(evt: BaseHookEvent) -> HookResult | None:
    if is_card(evt) and (opener := unnamed_opener(evt)):
        return evt.block(UNNAMED.format(opener=opener))
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
    flags += [f"{PORT_OPENER}#{source}" for source in values.get("port-of", ())]
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


def hide_records(md: str) -> tuple[str, list[str]]:
    moved: list[str] = []

    def hidden(found: re.Match[str]) -> str:
        if found[1]:
            return found[1]
        moved.append(found[2] or found[3])
        return ""

    kept = RECORD_NUMBER.sub(hidden, md).strip()
    numbers = list(dict.fromkeys(moved))
    return " ".join([kept, *(f"<!-- cci #{number} -->" for number in numbers)]), numbers


def ports_of(drive: list[str], numbers: list[str]) -> dict[str, int]:
    done = subprocess.run([str(LR), "board", "ports", *drive, *numbers], capture_output=True, text=True, timeout=FOREGROUND_JUDGE_SECONDS, env=dict(reqenv.env_map()), check=True)
    return json.loads(done.stdout)


def lr_opened(evt: BaseHookEvent, sources: dict[str, int]) -> set[int]:
    opened: set[int] = set()
    for call in evt.command.calls("lr") if evt.tool_name == "Bash" else ():
        values = ASK_OWNER.bind(call).values
        if values.get("words", ())[:1] != ("ask-owner",) or values.get("card") or values.get("port-of"):
            continue
        said = "\n".join(str(text) for name in ("title", "background", "option") for text in values.get(name, ()))
        named = [number for number in dict.fromkeys(next(filter(None, found)) for found in NAMED_PR.findall(said)) if int(number) not in sources.values()]
        if len(named) == 1 and named[0] in sources:
            opened.add(sources[named[0]])
    return opened


def card_about(title: str, body: str, commands: dict[str, object]) -> Input:
    return Input(tool=SLACK_CARD, tool_input={"card": {"title": title, "body": body}}, env=HOOK_ENV, commands=commands)


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", SLACK_CARD, *DM_TOOLS)],
    tests={
        card_about("Land #50254?", "Its checks passed.", {PORTS_READ: json.dumps({"50254": 49412})}): Block(pattern=r"for PR 49412\."),
        card_about("Port, old tree to new: dev #49412: land #50254?", "Its checks passed.", {PORTS_READ: json.dumps({"50254": 49412})}): Allow(),
        card_about("Port, old tree to new: dev [#49412](https://github.com/Forge-AI/monorepo/pull/49412): land it?", "See [#50254](https://github.com/Forge-AI/monorepo/pull/50254).", {PORTS_READ: json.dumps({"50254": 49412})}): Allow(),
        card_about("port of #49412: land #50254?", "Its checks passed.", {PORTS_READ: json.dumps({"50254": 49412})}): Block(pattern=r"for PR 49412\."),
        card_about("Land it?", "See https://github.com/Forge-AI/monorepo/pull/50254.", {PORTS_READ: json.dumps({"50254": 49412})}): Block(pattern=r"for PR 49412\."),
        card_about("Land #50333?", "Its checks passed.", {PORTS_READ: "{}"}): Allow(),
        card_about("Land #50254?", "Its checks passed.", {PORTS_READ: (1, "store unreadable")}): Warn(pattern=r"could not read"),
        card_about("Relay fix", "Its checks passed.", {}): Allow(),
        Input(command="lr ask-owner --title 'Land PR 50254?' --port-of 49412 --option 'Land :: It goes.' --option 'Hold :: It waits.' --nothing-to-build pick", env=HOOK_ENV, commands={PORTS_READ: json.dumps({"50254": 49412})}): Allow(),
        Input(command="lr ask-owner --title 'Land PR 50254?' --option 'Land :: It goes.' --option 'Hold :: It waits.' --nothing-to-build pick", env=HOOK_ENV, commands={PORTS_READ: json.dumps({"50254": 49412})}): Allow(),
        Input(command="lr ask-owner --title 'Land PR 50254 before PR 50333?' --option 'Land :: It goes.' --option 'Hold :: It waits.' --nothing-to-build pick", env=HOOK_ENV, commands={PORTS_READ: json.dumps({"50254": 49412})}): Block(pattern=r"for PR 49412\."),
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
    opened = lr_opened(evt, sources)
    unsaid = [source for source in dict.fromkeys(sources.values()) if source not in opened and not re.search(rf"(?i){re.escape(PORT_OPENER)}\[?#{source}\b", text)]
    return evt.block(PORT_UNSAID.format(sources=", ".join(map(str, unsaid)))) if unsaid else None


def describes_change(text: str) -> bool:
    return bool(CHANGE.search(text) or JUDGE.VAGUE.search(text))


def plain_answer(text: str) -> bool:
    return len(text.split()) < SHORT_WORDS and "?" not in text and not describes_change(text)


def whole_post(evt: BaseHookEvent, posted: str) -> str:
    return "\n\n".join([posted, *(str(extra) for field in ("links", "prs") for extra in evt._tool_input.get(field) or ())])


def reply(title: str, md: str, **fields: object) -> Input:
    return Input(tool=REPLY, tool_input={"title": title, "md": md} | fields.pop("given", {}), env=HOOK_ENV, **fields)


@on(  # capt-hook: no-escape the owner ordered every post to him to answer his first question, and the fix the message names is the only way through
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", REPLY, SLACK_CARD, *DM_TOOLS)],
    tests={
        reply("Big chats", VAGUE_POST, commands=HOLDS): Block(pattern=r"^A post to the owner names the concrete things[\s\S]*It adds one new setting[\s\S]*Which fields, on which table"),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Add the setting?", "body": VAGUE_POST}}, env=HOOK_ENV, commands=HOLDS): Block(pattern=r"first question: Which fields"),
        Input(command=ASK, env=HOOK_ENV, commands=HOLDS): Block(pattern=r"first question: Which fields"),
        Input(command=f"lr ask-owner --card {board('clean-card.json')} --until at:3d", env=HOOK_ENV, commands=HOLDS): Block(),
        Input(command="lr ask-owner --withdraw card-add-the-setting --lane first-question", env=HOOK_ENV, commands=HOLDS): Allow(),
        Input(command=f"cc-present update-block {board('clean-card.json')}", env=HOOK_ENV, commands=HOLDS): Block(),
        Input(command=f"CARD_NO_CODE=1 zsh draw.zsh --new-question 'not settled' {board('clean-card.json')}", env=HOOK_ENV, commands=HOLDS): Block(),
        Input(command=f"cc-present ask {board('clean-card.json')}", env=HOOK_ENV, commands=HOLDS): Block(),
        reply("Added fields", "Added a few fields.", commands=HOLDS): Block(),
        reply(CONCRETE["title"], CONCRETE["md"], commands=judged()): Allow(),
        reply("Landed", LANDED, commands=HOLDS): Allow(),
        reply("Big chats", VAGUE_POST, commands=sonnet_says("unsure")): Block(pattern=r"It adds one new setting[\s\S]*Which fields"),
        reply("Big chats", VAGUE_POST, commands=sonnet_says("yes", evidence="words no record carries")): Block(pattern=r"Which fields"),
        reply("Nightly build", STATUS, commands=sonnet_says("unsure")): Allow(),
        reply("Big chats", VAGUE_POST, commands=sonnet_says("no")): Allow(),
        reply("Big chats", VAGUE_POST, commands={"uv run --no-project --quiet": (1, "overloaded")}): Allow(),
        reply("Big chats", VAGUE_POST, commands={**HOLDS, "claude -p": (1, "claude: overloaded")}): Allow(),
        Input(command="cci post --kind note --to meta-desk --text 'It adds one new setting for big chats, and the sidebar stays as it is.'", env=HOOK_ENV, commands=HOLDS): Allow(),
        Input(command="ls -la", env=HOOK_ENV, commands=HOLDS): Allow(),
        reply("Your list", REMINDER, given={"prs": [LAND_PR]}, commands=judged()): Allow(),
        reply("Your list", REMINDER, given={"picks": ["Land", "Hold"], "recommended": "Land"}, commands=judged()): Allow(),
        reply("Why", "It dropped because both chats shared a replica. <!-- cci #384714 -->", commands=judged()): Allow(),
        Input(tool=REPLY, tool_input={"title": "Big chats", "md": VAGUE_POST}, commands=HOLDS): Allow(),
        reply("Landed", f"Live as [the relay fix]({LAND_PR}).", commands=HOLDS): Block(pattern=r"first question: Which fields"),
        reply("Landed", f"Live as <{LAND_PR}|the relay fix>.", commands=HOLDS): Block(pattern=r"first question: Which fields"),
        reply("Bugs", f"{VAGUE_POST} With the answer.", given={"order": "m1"}, commands=HOLDS): Block(pattern=r"first question: Which fields"),
        reply("Delete prompt", THIRD_PARTY, commands=judged()): Allow(),
    },
)
def posts_answer_his_first_question(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    reads = Reads(evt, which)
    posted = post_text(evt, reads)
    text = as_read(posted)
    if plain_answer(text):
        return None
    env = reqenv.env_map()
    verdict = judge_call(JUDGE, "first-question", WHOLE_POST, whole_post(evt, posted), backend=JUDGE.backend_from(env), drive=which[1], timeout=reqenv.clamp_timeout(FOREGROUND_JUDGE_SECONDS), env=env)
    if verdict is None or not (verdict.flagged or verdict.verdict == "unsure" and describes_change(text)):
        return evt.context(reads.warning("posts_answer_his_first_question")) if reads.failures else None
    return evt.block(fenced(UNANSWERED, [f'sentence: "{verdict.artifact_phrase}"', f"first question: {verdict.sentence}"]))


def asking_card(evt: BaseHookEvent, reads: Reads) -> dict | None:
    if evt.tool_name == REPLY:
        return evt._tool_input if evt._tool_input.get("picks") else None
    card = reads.read("the status card", lambda: slack_card(evt))
    return card if card and card.get("options") else None


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", REPLY, SLACK_CARD)],
    tests={
        reply(BUGS_TITLE, BUGS_BODY, given=BUGS_PICKS, commands=UNBUILT_PICK): Block(pattern=r"name the branch of a code pick[\s\S]*Tap all four ready fixes[\s\S]*names no branch"),
        reply(BUGS_TITLE, BUGS_BODY, given=BUGS_PICKS, commands=UNNAMED_SET): Block(pattern=r"^Nothing posted: this card leaves out[\s\S]*Tap all four ready fixes[\s\S]*Name each of the four ready fixes"),
        Input(tool=SLACK_CARD, tool_input={"card": json.loads(BUGS_CARD)}, env=HOOK_ENV, commands=UNNAMED_SET): Block(pattern=r"Tap all four ready fixes"),
        Input(command=f"cc-slack dm-status --card '{BUGS_CARD}'", env=LANE_ENV, commands=UNNAMED_SET): Block(pattern=r"Tap all four ready fixes"),
        reply("Land the relay fix?", "It stops a dropped reply when a chat reconnects.", given={"prs": [LAND_PR], **PICKS}, commands=judged()): Allow(),
        reply("Land the two relay fixes?", f"[#2196]({LAND_PR}) stops a dropped reply. [#2197](https://github.com/yasyf/cc-skills/pull/2197) shows the retry button.", given={"picks": ["Land both", "Hold both"], "recommended": "Land both"}, commands=judged()): Allow(),
        reply(BUGS_TITLE, BUGS_BODY, given=BUGS_PICKS, commands={"uv run --no-project --quiet": (1, "overloaded")}): Allow(),
        reply(BUGS_TITLE, BUGS_BODY, given=BUGS_PICKS, commands={**UNNAMED_SET, "claude -p": (1, "claude: overloaded")}): Allow(),
        reply(BUGS_TITLE, BUGS_BODY, commands=UNNAMED_SET): Allow(),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": BUGS_TITLE, "body": BUGS_BODY}}, env=HOOK_ENV, commands=UNNAMED_SET): Allow(),
        Input(tool=REPLY, tool_input={"title": BUGS_TITLE, "md": BUGS_BODY, **BUGS_PICKS}, commands=UNNAMED_SET): Allow(),
        Input(command="ls -la", env=HOOK_ENV, commands=UNNAMED_SET): Allow(),
    },
)
def options_name_their_set(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)) or not (card := asking_card(evt, Reads(evt, which))):
        return None
    env = reqenv.env_map()
    verdict = judge_call(JUDGE, SET_OPTIONS, SET_EVIDENCE, json.dumps(card), backend=JUDGE.backend_from(env), drive=which[1], timeout=reqenv.clamp_timeout(FOREGROUND_JUDGE_SECONDS), env=env)
    return evt.block(fenced(SET_UNNAMED, [f'option: "{verdict.artifact_phrase}"', verdict.sentence])) if verdict and verdict.flagged else None


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool(REPLY)],
    tests={
        reply("Incident", "With root as an incident at 9:06 PM (450686): the fix lane has it."): Rewrite(None, md="With root as an incident at 9:06 PM: the fix lane has it. <!-- cci #450686 -->"),
        reply("Alert", "Record #427030 says so."): Rewrite(None, md="Record says so. <!-- cci #427030 -->"),
        reply("Alert", "It dropped because both chats shared a replica. <!-- cci #384714 -->"): Allow(),
        reply("Alert", f"[Sentry 4821]({SENTRY_LINK}) fired again."): Allow(),
        reply("Alert", "See [427030](https://acme.slack.com/archives/C1/p1793833513167859)."): Allow(),
        reply("Alert", "It is on https://buildkite.com/forge/release/builds/1234567 now."): Allow(),
        reply("Alert", "Sentry 4821123 fired again."): Allow(),
        reply("Alert", "Thread 1793829658.340949 is the one."): Allow(),
        Input(tool=REPLY, tool_input={"title": "Alert", "md": "Record #427030 says so."}): Allow(),
    },
)
def reply_record_numbers_move_to_hidden_cites(evt: BaseHookEvent) -> HookResult | None:
    if not drive_args(evt):
        return None
    md, numbers = hide_records(given(evt, "md"))
    return evt.rewrite(evt._tool_input | {"md": md}, note=RECORDS_MOVED.format(numbers=", ".join(numbers))) if numbers else None


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", REPLY, SLACK_CARD, *DM_TOOLS)],
    tests={
        reply("Alert", "Sentry 4821 fired again."): Block(pattern=r"^Nothing posted: a bare id[\s\S]*Sentry 4821"),
        reply("Alert", f"[Sentry 4821]({SENTRY_LINK}) fired again."): Allow(),
        reply("Alert", "It fired.", given={"action": "Read record #427030."}): Block(pattern=r"a bare id[\s\S]*#427030"),
        reply("Alert", "Record #427030 says so."): Allow(),
        Input(command="cc-slack dm-status 'Record #427030 says so.'", env=LANE_ENV): Block(pattern=r"#427030"),
        Input(command="cc-slack reply --channel D0B6Z --text 'Sentry 4821 fired again.'", env=HOOK_ENV): Allow(),
        Input(tool=REPLY, tool_input={"title": "Alert", "md": "Sentry 4821 fired again."}): Allow(),
    },
)
def posts_link_the_ids_they_name(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    moved = hide_records(given(evt, "md"))[1] if evt.tool_name == REPLY else []
    reads = Reads(evt, which)
    bare = [hit for hit in dict.fromkeys(found[0] for found in BARE_ID.finditer(readable(post_text(evt, reads)))) if hit.lstrip("#") not in moved]
    if not bare:
        return None
    return evt.context(reads.warning("posts_link_the_ids_they_name")) if reads.failures else evt.block(fenced(BARE_NUMBER, bare))


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", REPLY, SLACK_CARD, *DM_TOOLS)],
    tests={
        reply("Your list", REMINDER): Block(pattern=r"^Nothing posted: the words below[\s\S]*wait on your"),
        reply("Your list", REMINDER, given={"prs": [LAND_PR]}): Allow(),
        reply("Your list", REMINDER, given={"picks": ["Land", "Hold"], "recommended": "Land"}): Allow(),
        reply("Support reply", WAITS_ON_HIM): Block(pattern=r"waits on your"),
        reply("Support reply", "It needs your yes before it posts."): Block(pattern=r"your yes"),
        reply("Support reply", "This is blocked on you."): Block(pattern=r"blocked on you"),
        reply("Support reply", "Your pick decides the wording."): Block(pattern=r"Your pick"),
        reply("Support reply", "Send the reply, yes or no?"): Block(pattern=r"yes or no\?"),
        reply("Pick card", "PR card: redrawing as our own Block Kit."): Block(pattern=r"```\ncard\n```"),
        reply("Delete prompt", THIRD_PARTY): Allow(),
        reply("Live", f"The Platy release card is up at {CARD_LINK}."): Allow(),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Your list", "body": REMINDER}}, env=HOOK_ENV): Block(pattern=r"carries no card"),
        Input(tool=SLACK_CARD, tool_input={"card": {"title": "Your list", "body": REMINDER, "options": ["Land", "Hold"]}}, env=HOOK_ENV): Allow(),
        Input(command=STATUS_POST, env=LANE_ENV): Block(pattern=r"waits on your"),
        Input(command=f"cc-slack dm-status --tap-pr {LAND_PR} '{WAITS_ON_HIM}'", env=LANE_ENV): Allow(),
        Input(command=f"cc-slack dm-status --about ask-1 '{WAITS_ON_HIM}'", env=LANE_ENV): Allow(),
        Input(tool=SLACK_CARD, tool_input={"text": WAITS_ON_HIM}, env=LANE_ENV): Block(pattern=r"carries no card"),
        Input(tool=SLACK_CARD, tool_input={"text": WAITS_ON_HIM, "tap_prs": [LAND_PR]}, env=LANE_ENV): Allow(),
        Input(tool=DM_TOOLS[1], tool_input={"channel_id": "D0B6Z", "text": WAITS_ON_HIM}, env=LANE_DM_ENV, commands={PEER: HIS_DM}): Block(pattern=r"carries no card"),
        Input(tool=DM_TOOLS[1], tool_input={"channel_id": "D0B6Z", "text": WAITS_ON_HIM}, env=LANE_DM_ENV, commands={PEER: json.dumps({"channel_id": "D0B6Z", "peer": "U0SOMEONE", "peer_name": "Sam"})}): Allow(),
        Input(tool=DM_TOOLS[1], tool_input={"channel_id": "C0RELEASES1", "text": WAITS_ON_HIM}, env=LANE_DM_ENV): Allow(),
        Input(tool=REPLY, tool_input={"title": "Pick card", "md": "The card redraws."}): Allow(),
        Input(command="ls -la", env=HOOK_ENV): Allow(),
    },
)
def posts_naming_a_card_carry_it(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    reads = Reads(evt, which)
    named = TAP_WORDS.search(readable(post_text(evt, reads) or lane_dm_words(evt, reads)))
    if not named or carries_cards(evt, reads):
        return None
    return evt.context(reads.warning("posts_naming_a_card_carry_it")) if reads.failures else evt.block(fenced(CARDLESS, [named[0]]))


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool(REPLY, SLACK_CARD, *DM_TOOLS)],
    tests={
        reply("Incident: run timeline", f"It fails. [Sentry 4821]({SENTRY_LINK})"): Block(pattern=r"bare URLs"),
        reply("Incident: run timeline", f"It fails. <{SENTRY_LINK}|Sentry 4821>"): Block(pattern=r"bare URLs"),
        reply("Incident: run timeline", "It fails. [alert](https://acme.slack.com/archives/C1/p1793833513167859)"): Block(pattern=r"bare URLs"),
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
    return evt.block(BARE_ALERT) if INCIDENT.search(posted) and LABELLED_ALERT.search(posted) else None
