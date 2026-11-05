from __future__ import annotations

import re

from captain_hook import Allow, And, BaseHookEvent, Event, HookResult, Input, Or, Runs, Tool, Warn, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .meta_records import post_records
from .pr_ledger import LANE_ENV

POOL_LANES = ("finder-desk", "fidelity-pipeline")
PR_NUMBER = re.compile(r"#\d+")
GRADE_FIRST = "Warning, nothing was blocked: the grading pool starts passes by its queue, so an ask to a desk moves no PR up. Run `lr grade first <n>` to put that PR at the front."


@on(
    Event.PreToolUse,
    only_if=[Bound(), Or(Tool(POST_TOOL), And(Tool("Bash"), Runs("cci", "post")))],
    tests={
        Input(command="cci post --lane x --kind ask --to finder-desk --text 'grade #12 now'", env=LANE_ENV): Warn(pattern=r"lr grade first"),
        Input(tool=POST_TOOL, tool_input={"lane": "x", "kind": "ask", "to": ["fidelity-pipeline"], "text": "grade #12 now"}, env=LANE_ENV): Warn(pattern=r"lr grade first"),
        Input(command="cci post --lane x --kind ask --to finder-desk --text 'is the pool healthy'", env=LANE_ENV): Allow(),
        Input(command="cci post --lane x --kind ask --to meta-desk --text 'grade #12 now'", env=LANE_ENV): Allow(),
        Input(command="cci post --lane x --kind report --to finder-desk --text '#12 moved'", env=LANE_ENV): Allow(),
    },
)
def pool_asks_name_grade_first(evt: BaseHookEvent) -> HookResult | None:
    asked = any(post.kind == "ask" and PR_NUMBER.search(post.text) and set(post.recipients).intersection(POOL_LANES) for post in post_records(evt))
    return evt.context(GRADE_FIRST) if asked else None
