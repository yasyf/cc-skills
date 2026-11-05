from __future__ import annotations

import re

from captain_hook import Allow, And, BaseHookEvent, Event, HookResult, Input, Or, Runs, Tool, Warn, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .meta_records import Post, post_records
from .pr_ledger import LANE_ENV

FULL_SHA_LENGTH = 40
FULL = "b" * FULL_SHA_LENGTH
SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
SHORT_HEAD = "Warning, nothing was blocked: a short sha leaves the PR's row at its old head until the next read. Name the full 40-character sha from `git rev-parse <sha>`."


def named_shas(post: Post) -> list[str]:
    named = [head.rpartition("=")[2] for head in post.heads if head is not None] + [value for key, value in post.attrs.items() if key == "head" or key.startswith("head:")]
    if post.heads or named or post.kind != "head":
        return [head.split()[0].rpartition("..")[2] for head in named if head.strip()]
    return SHA.findall(post.text)[:1]


@on(
    Event.PreToolUse,
    only_if=[Bound(), Or(Tool(POST_TOOL), And(Tool("Bash"), Runs("cci", "post")))],
    tests={
        Input(command="cci post --lane x --kind head --head 12=aaaaaaa..bbbbbbb --attr delta=rebase", env=LANE_ENV): Warn(pattern=r"40-character"),
        Input(command="cci post --lane x --kind opened --pr 12 --attr head=bbbbbbb", env=LANE_ENV): Warn(pattern=r"40-character"),
        Input(tool=POST_TOOL, tool_input={"lane": "x", "kind": "head", "text": "HEAD #12 bbbbbbb"}, env=LANE_ENV): Warn(pattern=r"40-character"),
        Input(command="cci post --lane x --kind head --head '12=aaaaaaa..bbbbbbb patch-identical and a long note after the sha' --attr delta=rebase", env=LANE_ENV): Warn(pattern=r"40-character"),
        Input(command="cci post --lane x --kind head --head 12=bbbbbbb --text 'HEAD #12 bbbbbbb carried'", env=LANE_ENV): Warn(pattern=r"40-character"),
        Input(tool=POST_TOOL, tool_input={"lane": "x", "kind": "head", "head": "12=aaaaaaa..bbbbbbb", "text": f"HEAD #12 {FULL}"}, env=LANE_ENV): Warn(pattern=r"40-character"),
        Input(command=f"cci post --lane x --kind head --head 12=aaaaaaa..{FULL} --attr delta=rebase", env=LANE_ENV): Allow(),
        Input(command="cci post --lane x --kind head --pr 12 --head \"12=$(git rev-parse aaaaaaa)..$(git rev-parse HEAD) review fix\" --text 'HEAD #12 bbbbbbb'", env=LANE_ENV): Allow(),
        Input(command="head=$(git rev-parse HEAD); cci post --lane x --kind head --pr 12 --head \"12=$head\" --text 'HEAD #12 bbbbbbb'", env=LANE_ENV): Allow(),
        Input(tool=POST_TOOL, tool_input={"lane": "x", "kind": "head", "head": f"12=aaaaaaa..{FULL}", "text": "HEAD #12 bbbbbbb"}, env=LANE_ENV): Allow(),
        Input(tool=POST_TOOL, tool_input={"lane": "x", "kind": "head", "text": f"HEAD #12 {FULL}"}, env=LANE_ENV): Allow(),
        Input(command="cci post --lane x --kind note --text 'bbbbbbb moved'", env=LANE_ENV): Allow(),
        Input(command="cci post --lane x --kind head --head 12=bbbbbbb"): Allow(),
    },
)
def head_records_name_full_shas(evt: BaseHookEvent) -> HookResult | None:
    short = any(post.kind in ("head", "opened") and any(len(sha) < FULL_SHA_LENGTH for sha in named_shas(post)) for post in post_records(evt))
    return evt.context(SHORT_HEAD) if short else None
