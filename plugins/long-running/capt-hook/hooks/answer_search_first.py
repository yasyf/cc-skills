from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Tool, Warn, WorkflowState, on
from captain_hook.cmd import Call

from .cci_mcp_posts import POST_TOOL
from .deadline_plans import posts
from .lane_landing import lane_name
from .pr_ledger import OPENERS
from .root_context import CCN

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
    only_if=[Tool("Bash"), InLane(), RunsAnswerSearch()],
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
    only_if=[Tool("Bash", POST_TOOL), InLane(), AsksOrOpensAPR()],
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
