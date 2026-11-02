from __future__ import annotations

import json
import re
import subprocess
from datetime import timedelta

from captain_hook import Allow, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, T, on
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState, bus_name, drive_entry
from .lane_rotation import DriveActive
from .session_tree import ROOT_NAMES, drive_lane
from .tests.register_fixtures import DRIVE_CONFIG

FINAL_TEXT_CAP = 300
LANE_TYPES = frozenset({"long-running:lane", "long-running:lane-ship"})
TEAMMATE_ID = re.compile(r"^a(?P<name>.+)-[0-9a-f]{16}$")
ONE_LINE = (
    f"Warning, nothing was blocked: Final text exceeds {FINAL_TEXT_CAP} characters. Replace it with "
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
    max_fires=1,
    tests={
        Input(agent_id="alanding-desk-1a1a1a1a1a1a1a1a", state=ACTIVE): Allow(),
        Input(agent_id="a44fd2b6534c7cc71", agent_type="general-purpose", state=ACTIVE): Allow(),
        Input(agent_id="alanding-desk-1a1a1a1a1a1a1a1a"): Allow(),
        Input(state=ACTIVE): Allow(),
    },
)
def cap_lane_final_text(evt: BaseHookEvent) -> HookResult | None:
    if is_lane(evt) and len(final_text(evt)) > FINAL_TEXT_CAP:
        return evt.context(ONE_LINE)
    return None


REPORT_WINDOW = timedelta(minutes=10)
RECORD_KINDS = ("report", "head", "handoff", "fix-live", "done")
ROOT_ADDRESSES = ROOT_NAMES | {"root"}
CCI_SECONDS = 15
UNRECORDED_REPORT = (
    "A lane's report or deliverable is a cci record; a SendMessage to root alone gets lost. "
    "Post it with `cci post --drive {drive} --lane {lane} --kind report --to root --text '<outcome + pointer>'`, then stop again."
)
LANE_ENV = DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": "fix-lane-1"}
RECORDS_SINCE = "cci tail --drive brook-drive --lane fix-lane-1 --kind report --kind head --kind handoff --kind fix-live --kind done --json -n 1 --since"
REPORTED = json.dumps({"seq": 7, "lane": "fix-lane-1", "kind": "report", "text": "DONE the fix merged"})


def lane_of(evt: BaseHookEvent) -> str | None:
    return drive_lane(evt) or ((found := TEAMMATE_ID.match(evt.agent_id or "")) and found["name"]) or None


def reported_to_root_by_message(evt: BaseHookEvent) -> str | None:
    """The RFC 3339 start of the window when the lane's last REPORT_WINDOW of calls holds a SendMessage to root, else None."""
    calls = evt.ctx.t.tool_calls.with_errors.items
    if not calls:
        return None
    start = max(use.ts for use in calls) - REPORT_WINDOW
    sent = any(use.ts >= start and use.call.name == "SendMessage" and use.call.raw.get("to") in ROOT_ADDRESSES for use in calls)
    return start.isoformat() if sent else None


@on(
    Event.Stop | Event.SubagentStop,
    only_if=[FromSubagent(), DriveActive()],
    tests={
        Input(
            agent_id="a1b2c3", state=ACTIVE, env=LANE_ENV, transcript=[T.user("go"), T.assistant(T.tool("SendMessage", to="main", message="DONE: the fix merged"))], commands={RECORDS_SINCE: ""}
        ): Block(pattern=r"--lane fix-lane-1 --kind report"),
        Input(
            agent_id="a1b2c3", state=ACTIVE, env=LANE_ENV, transcript=[T.user("go"), T.assistant(T.tool("SendMessage", to="main", message="DONE: the fix merged"))], commands={RECORDS_SINCE: REPORTED}
        ): Allow(),
        Input(
            agent_id="a1b2c3", state=ACTIVE, env=LANE_ENV, transcript=[T.user("go"), T.assistant(T.tool("SendMessage", to="desk-2", message="your turn"))], commands={RECORDS_SINCE: ""}
        ): Allow(),
        Input(
            agent_id="a1b2c3",
            state=ACTIVE,
            env=LANE_ENV,
            transcript=[T.user("go"), T.assistant(T.tool("SendMessage", to="main", message="DONE"), timestamp="2026-01-01T00:00:00Z"), T.assistant(T.tool("Bash", command="ls"), timestamp="2026-01-01T00:30:00Z")],
            commands={RECORDS_SINCE: ""},
        ): Allow(),
        Input(agent_id="a1b2c3", state=ACTIVE, env=LANE_ENV, transcript=[T.user("go"), T.assistant(T.tool("SendMessage", to="main", message="DONE"))], commands={RECORDS_SINCE: (1, "cci down")}): Allow(),
        Input(agent_id="a1b2c3", env=LANE_ENV, transcript=[T.user("go"), T.assistant(T.tool("SendMessage", to="main", message="DONE"))], commands={RECORDS_SINCE: ""}): Allow(),
    },
)
def lane_reports_are_records(evt: BaseHookEvent) -> HookResult | None:
    """A lane that sent root its report by SendMessage in its last 10 minutes stops only once a `report`, `head`, `handoff`, `fix-live` or `done` cci record of that lane falls in the same window (owner, 9:55 PM Nov 1). A cci read that fails blocks nothing."""
    if not (start := reported_to_root_by_message(evt)) or not (lane := lane_of(evt)) or not (entry := drive_entry(evt)):
        return None
    argv = ["cci", "tail", "--drive", bus_name(entry), "--lane", lane, *(arg for kind in RECORD_KINDS for arg in ("--kind", kind)), "--json", "-n", "1", "--since", start]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=CCI_SECONDS, env=dict(reqenv.env_map()))
    except (OSError, subprocess.SubprocessError):
        return None
    return None if done.returncode or done.stdout.strip() else evt.block(UNRECORDED_REPORT.format(drive=bus_name(entry), lane=lane))
