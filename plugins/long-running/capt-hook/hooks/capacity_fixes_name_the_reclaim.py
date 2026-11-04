from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Tool, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .lane_landing import incident_lane
from .meta_records import posts
from .owner_facing import fenced

RESOURCE = r"(?:volumes?|disks?|pvcs?|storage|memory|limits?|replicas?)"
GREW = r"(?:grow|grew|grown|growing|enlarg\w*|expand\w*|resiz\w*|increas\w*|rais\w*|bump\w*|doubl\w*|scal\w*)"
CAPACITY = re.compile(rf"\b{GREW}\b[^.;]{{0,60}}?\b{RESOURCE}\b|\b{RESOURCE}\b[^.;]{{0,40}}?\b{GREW}\b|(?:\bto|->|→)\s*\d+(?:\.\d+)?\s*[GMT]i\b", re.IGNORECASE)
RECLAIM = re.compile(r"\b(?:evict\w*|reclaim\w*|clean-?ups?|clean(?:s|ed|ing)? up|cleaner|GC|garbage[- ]collect\w*|retention|leak\w*|prun\w*|compact\w*|rotat\w*)\b", re.IGNORECASE)
CHANGE = re.compile(r"#\d+\b|/pull/\d+|\bPR \d+\b|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
REFUSAL = (
    "A FIX-LIVE that grows capacity names what stopped reclaiming it and the PR or commit that stopped it, as in `eviction runs only at startup since #2612`,"
    " because the capacity is headroom and the reclaim fix is the fix: add both to the line below and post it again."
)
LANE = "incident-journal-disk"
GROWN = "FIX-LIVE incident-journal-disk: grew the journal volume from 5Gi to 32Gi; disk at 18% on the released build"
NAMED = "FIX-LIVE incident-journal-disk: grew the journal volume from 5Gi to 32Gi as headroom; #2612 left eviction running only at startup, and #2690 runs it hourly again"


def post(text: str, lane: str = LANE, kind: str = "fix-live") -> Input:
    return Input(command=f"cci post --drive d --lane {lane} --kind {kind} --to deploy --text \"{text}\"", env={"CLAUDE_LONG_RUNNING_LANE": lane})


@on(  # capt-hook: no-escape the owner ordered an incident fix that adds capacity to name what stopped reclaiming it before it claims fix-live
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", POST_TOOL)],
    tests={
        post(GROWN): Block(pattern=r"^A FIX-LIVE that grows capacity names what stopped reclaiming it[\s\S]*5Gi to 32Gi"),
        post(NAMED): Allow(),
        post("FIX-LIVE incident-api-500s: #2690 released and the reporter's run passes on build 12345"): Allow(),
        post(GROWN, lane="lane-a"): Allow(),
        post(GROWN, kind="report"): Allow(),
    },
)
def capacity_fixes_name_the_reclaim(evt: BaseHookEvent) -> HookResult | None:
    found = [
        text
        for lane, _, kind, text, _, _ in posts(evt)
        if "fix-live" in kind.lower().split(",") and CAPACITY.search(text) and not (RECLAIM.search(text) and CHANGE.search(text)) and incident_lane(evt, lane)
    ]
    return evt.block(fenced(REFUSAL, found)) if found else None
