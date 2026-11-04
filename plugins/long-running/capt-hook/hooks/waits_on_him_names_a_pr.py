from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .meta_records import posts
from .owner_facing import CCI_POST

SAYS_HE_WAITS = re.compile(r"\bwait(?:s|ing)?\b[^.;]{0,60}\b(?:his|your|the owner'?s?)\s+(?:\w+\s+)?(?:tap|approval|landing call)\b", re.IGNORECASE)
NAMES_PR = re.compile(r"#\d{1,5}\b|/pull/\d+|\bpr#?\s*\d+\b", re.IGNORECASE)
WARNING = "Warning, nothing was refused: a record saying a PR waits on the owner names that PR, or the review tab cannot be checked against it. Add --pr, and tell root if the landing store does not know the PR."
ENV = {"LR_DRIVE": "d", "CLAUDE_LONG_RUNNING_LANE": "lane-a"}


def post(text: str, extra: str = "") -> Input:
    return Input(command=f'cci post --drive d --lane lane-a --kind blocked{extra} --text "{text}"', env=ENV)


def pr_flag(evt: BaseHookEvent) -> bool:
    if evt.tool_name == POST_TOOL:
        return bool(evt._tool_input.get("pr"))
    return any(CCI_POST.bind(call).values.get("pr") for call in evt.command.calls("cci"))


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", POST_TOOL)],
    tests={
        post("this change waits on his tap"): Warn(pattern=r"names that PR"),
        post("#49730 waits on his tap"): Allow(),
        post("this waits on his tap", " --pr 49730"): Allow(),
        post("waiting on CI"): Allow(),
    },
)
def waits_on_him_names_a_pr(evt: BaseHookEvent) -> HookResult | None:
    if pr_flag(evt):
        return None
    for _, _, _, text, _, _ in posts(evt):
        if SAYS_HE_WAITS.search(text) and not NAMES_PR.search(text):
            return evt.context(WARNING)
    return None
