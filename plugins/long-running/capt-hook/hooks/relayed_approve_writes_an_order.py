from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .meta_records import post_records

RELAYED_APPROVE = re.compile(r"=\s*Approve\s+#\d+\b")
NO_ORDER = "Warning, nothing was blocked: A pick relayed as Approve writes no land order, so the landing pass never reads his tap. After this post, run `lr landing order <PR> --record <seq it prints>`."
RELAY = {"CLAUDE_LONG_RUNNING_LANE": "owner-relay"}
PICK = "Owner: \"Retire it behind a tombstone\" = Approve #52060 at 03d865a (card-rmw5myh8a, 4:00 PM)"


def relayed(text: str, kind: str = "owner") -> Input:
    return Input(command=f"cci post --drive d --lane owner-relay --kind {kind} --pr Forge-AI/monorepo#52060 --text '{text}'", env=RELAY)


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", POST_TOOL)],
    tests={
        relayed(PICK): Warn(pattern=r"^Warning, nothing was blocked: A pick relayed as Approve.*`lr landing order <PR> --record <seq it prints>`"),
        Input(tool=POST_TOOL, tool_input={"lane": "owner-relay", "kind": "owner", "text": PICK}, env=RELAY): Warn(),
        relayed("Owner: \"Approve\" #52183 at 345c683 (card-tap-52183-345c683, 4:56 PM)"): Allow(),
        relayed("Owner 4:00 PM on card-rmw5myh8a: picked option A, #52060 as built."): Allow(),
        relayed(PICK, kind="note"): Allow(),
        Input(command=f"cci post --drive d --lane owner-relay --kind owner --text '{PICK}'"): Allow(),
    },
)
def relayed_approve_writes_an_order(evt: BaseHookEvent) -> HookResult | None:
    return evt.context(NO_ORDER) if any(post.kind == "owner" and RELAYED_APPROVE.search(post.text) for post in post_records(evt)) else None
