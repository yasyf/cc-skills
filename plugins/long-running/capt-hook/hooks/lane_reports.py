from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, on

from .compaction_handoff import CompactionState
from .lane_rotation import DriveActive

FINAL_TEXT_CAP = 300
LANE_TYPES = frozenset({"long-running:lane", "long-running:lane-ship"})
TEAMMATE_ID = re.compile(r"^a.+-[0-9a-f]{16}$")
ONE_LINE = (
    f"Final text exceeds {FINAL_TEXT_CAP} characters. Replace it with "
    "one line: outcome + pointer; the report went by SendMessage."
)
ACTIVE = [CompactionState(active=True)]


def is_lane(evt: BaseHookEvent) -> bool:
    return evt._raw.get("agent_type") in LANE_TYPES or bool(TEAMMATE_ID.match(evt.agent_id or ""))


def final_text(evt: BaseHookEvent) -> str:
    return (evt._raw.get("last_assistant_message") or "").strip()


@on(
    Event.Stop | Event.SubagentStop,
    only_if=[FromSubagent(), DriveActive()],
    tests={
        Input(agent_id="alanding-desk-1a1a1a1a1a1a1a1a", state=ACTIVE): Allow(),
        Input(agent_id="a44fd2b6534c7cc71", agent_type="general-purpose", state=ACTIVE): Allow(),
        Input(agent_id="alanding-desk-1a1a1a1a1a1a1a1a"): Allow(),
        Input(state=ACTIVE): Allow(),
    },
)
def cap_lane_final_text(evt: BaseHookEvent) -> HookResult | None:
    if is_lane(evt) and len(final_text(evt)) > FINAL_TEXT_CAP:
        return evt.block(ONE_LINE)
    return None
