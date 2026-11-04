from __future__ import annotations

import json
import re
import subprocess

from captain_hook import Allow, BaseHookEvent, Block, Event, FileFixture, FromSubagent, HookResult, Input, Tool, Warn, WorkflowState, on
from captain_hook.cmd import Call
from captain_hook.util import reqenv

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import LR, Bound
from .deadline_plans import posts
from .lane_landing import lane_name
from .owner_facing import board_bytes, board_sources, fenced
from .pr_ledger import OPENERS
from .register_injection import DRIVE_ENV, drive_args
from .root_context import CCN
from .settled_questions import cards

ASKING = frozenset({"ask", "decide"})
OPTIONS = re.compile(r"\b(?:options?|picks?|choices?|choose)\b|(?:^|[\s(])[A-D1-4][).:]\s.*(?:^|[\s(])[B-D2-4][).:]\s", re.IGNORECASE | re.DOTALL)
LANE = {"CLAUDE_LONG_RUNNING_LANE": "lane-a"}
ASK = "cci post --drive d --lane lane-a --kind ask --to meta-desk --text 'which store holds the topics? A) a ledger B) labels only'"
SEARCHED_FIRST = (
    "Warning, nothing was blocked: Search his answers before asking him to choose or opening a PR."
    " Run `ccn answer search <topic>` per subject and `ccn relevant <path>` per changed path, or `rulings.py answers`, and cite any answer that settles it instead of asking."
)


class AnswerSearch(WorkflowState):
    searched: bool = False


def searches(call: Call) -> bool:
    if call.name == "rulings.py":
        return call.args[:1] == ("answers",)
    words = tuple(CCN.bind(call).values.get("words", ())) if call.name in ("ccn", "cc-notes") else ()
    return words[:2] == ("answer", "search") or words[:1] in (("relevant",), ("search",))


class InLane:
    def check(self, evt: BaseHookEvent) -> bool:
        return bool(lane_name(evt))


class RunsAnswerSearch:
    def check(self, evt: BaseHookEvent) -> bool:
        return any(searches(call) for call in evt.command.calls())


class Searched:
    def check(self, evt: BaseHookEvent) -> bool:
        return AnswerSearch.load(evt).searched


class AsksOrOpensAPR:
    def check(self, evt: BaseHookEvent) -> bool:
        if any(kinds & ASKING and OPTIONS.search(text) for text, kinds, _ in posts(evt)):
            return True
        return evt.tool_name == "Bash" and any(tuple([call.name, *call.args])[: len(opener)] == opener for call in evt.command.calls() for opener in OPENERS)


@on(
    Event.PostToolUse,
    only_if=[Bound(), Tool("Bash"), InLane(), RunsAnswerSearch()],
    skip_if=[FromSubagent()],
    tests={
        Input(command="ccn answer search old-chats", env=LANE): Allow(),
        Input(command="git status", env=LANE): Allow(),
    },
)
def note_the_answer_search(evt: BaseHookEvent) -> None:
    with AnswerSearch.mutate(evt) as state:
        state.searched = True


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", POST_TOOL), InLane(), AsksOrOpensAPR()],
    skip_if=[FromSubagent(), Searched()],
    tests={
        Input(command=ASK, env=LANE): Warn(pattern=r"^Warning, nothing was blocked: Search his answers"),
        Input(command="cci post --drive d --lane lane-a --kind decide --to lane-b --text 'two options: keep the fallback or drop it'", env=LANE): Warn(),
        Input(command="gh pr create --repo yasyf/cc-skills --title t --body b", env=LANE): Warn(),
        Input(command="ccx vcs ship --new-branch", env=LANE): Warn(),
        Input(tool=POST_TOOL, tool_input={"kind": "ask", "text": "pick one: A) ledger, B) labels"}, env=LANE): Warn(),
        Input(command=ASK, env=LANE, state=[AnswerSearch(searched=True)]): Allow(),
        Input(command="cci post --drive d --lane lane-a --kind ask --to meta-desk --text 'is the Sprite still up?'", env=LANE): Allow(),
        Input(command="cci post --drive d --lane lane-a --kind report --to meta-desk --text 'options A) and B) both shipped'", env=LANE): Allow(),
        Input(command="gh pr view 2403", env=LANE): Allow(),
        Input(command=ASK): Allow(),
        Input(command=ASK, env=LANE, agent_id="a1b2c3"): Allow(),
    },
)
def search_his_answers_before_asking(evt: BaseHookEvent) -> HookResult:
    return evt.context(SEARCHED_FIRST)


CHECK_SECONDS = 300
REFUSED = "lr: refused: "
DRAW_REFUSED = "No card below reaches his board: each is explained only by a link, repeats a card on it, asks him to grade a reading of his words, or asks what his answer below settles. Embed the write-up's section, apply his answer, or act on your reading and say what you did."
CHECK = f"{LR} board --drive d --session s cards check -"
SETTLED_CARD = {"id": "card-old-snapshot-new-build-choice", "type": "card", "title": "An old chat will not open on the new build: what should happen to chats like this?", "children": [{"id": "pick", "type": "choice"}]}
DRAWN = {"command": "cc-present push ~/owner-board.json --session s", "file": FileFixture(home=True, name="owner-board.json", content=json.dumps({"blocks": [SETTLED_CARD]})), "env": {DRIVE_ENV: "d"}}


class PushesABoard:
    def check(self, evt: BaseHookEvent) -> bool:
        return any(source is not None for _, _, source, _ in board_sources(evt))


@on(  # capt-hook: no-escape the owner ordered that no card asks what his answers already settle, at every draw site
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash"), PushesABoard()],
    tests={
        Input(**DRAWN, commands={CHECK: (1, 'lr: refused: his answer to "When does an old-build chat convert?" already settles it: each old chat gets one background upgrade attempt on open (cc-notes answer 29b2e59)')}): Block(
            pattern=r"^No card below reaches his board[\s\S]*his answer to \"When does an old-build chat convert\?\" already settles it"
        ),
        Input(**DRAWN, commands={CHECK: (1, "lr: refused: its Explain more is only a link, and he reads cards where they are: put the write-up's section in the card")}): Block(pattern=r"^No card below reaches his board[\s\S]*its Explain more is only a link"),
        Input(**DRAWN, commands={CHECK: "WARNING the card draws unchecked against his answers: claude: overloaded\n"}): Warn(pattern=r"draws unchecked against his answers"),
        Input(**DRAWN, commands={CHECK: (1, "lr: drive d: no such drive")}): Warn(pattern=r"no such drive"),
        Input(**DRAWN, commands={CHECK: ""}): Allow(),
        Input(**{**DRAWN, "command": "cc-present ask ~/owner-board.json --session s"}, commands={CHECK: (1, "lr: refused: its background is only a link")}): Block(pattern=r"its background is only a link"),
        Input(**{**DRAWN, "env": {}}): Allow(),
        Input(**{**DRAWN, "command": "cc-present push ~/owner-board.json --session s --dry-run"}): Allow(),
    },
)
def no_card_asks_what_is_settled(evt: BaseHookEvent) -> HookResult | None:
    if not (flags := drive_args(evt)):
        return None
    refusals, warnings = [], []
    for call, values, source, piped in board_sources(evt):
        if source is None or "--dry-run" in call.args:
            continue
        session = [flag for name in values.get("session", ()) for flag in ("--session", name)]
        for card in cards(json.loads(board_bytes(evt, call, source, piped))):
            done = subprocess.run(
                [str(LR), "board", *flags, *session, "cards", "check", "-"], input=json.dumps(card), capture_output=True, text=True, timeout=CHECK_SECONDS, env=dict(reqenv.env_map())
            )
            if done.stderr.startswith(REFUSED):
                refusals.append(done.stderr.strip())
            elif said := (done.stdout + done.stderr).strip():
                warnings.append(said)
    if refusals:
        return evt.block(fenced(DRAW_REFUSED, refusals))
    return evt.context("\n".join(warnings)) if warnings else None
