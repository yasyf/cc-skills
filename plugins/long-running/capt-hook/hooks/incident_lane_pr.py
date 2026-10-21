from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from captain_hook import Allow, Annotated, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, on

from .compaction_handoff import CompactionState
from .lane_landing import drive_entry, lane_name
from .lane_reports import is_lane
from .lane_rotation import DriveActive
from .tests.lane_pr_fixtures import AGENT, BLOCKER, DRIVE, MECHANISM, OPENED, STATUS, TAIL

INCIDENT_LANE = re.compile(r"^(?:inc|incident|outage)-")
EVIDENCE_LANE = re.compile(r"-evidence$")
MARK = re.compile(r"^(?:MECHANISM|CANDIDATE)\b")
CLEAR = re.compile(r"^(?:OPENED|BLOCKER|READY|NOT-OURS)\b")
TEXT_KINDS = frozenset({"report", "incident", "state"})
CLEARS = frozenset({"opened", "head", "ready", "blocker", "blocked", "fix-live", "not-ours", "stopped", "stand-down"})
LOOKBACK = "12h"
TAIL_BUDGET = "400000"
CCI_TIMEOUT_SECONDS = 20
ACTIVE = [CompactionState(active=True)]
UNSHIPPED = (
    "An incident lane may not stop after posting its mechanism with no pushed branch. "
    "Run the commands below and post `opened` with the PR, or post `blocker` naming what stops the fix."
)


def mechanism_waits(records: list[dict], lane: str) -> bool:
    waiting = False
    for found in records:
        if found["lane"] != lane:
            continue
        kind, text = found["kind"], found.get("text") or ""
        if kind == "mechanism" or (kind in TEXT_KINDS and MARK.match(text)):
            waiting = True
        elif kind in CLEARS or (kind in TEXT_KINDS and CLEAR.match(text)):
            waiting = False
    return waiting


def lane_records(drive: str, lane: str) -> list[dict] | None:
    done = subprocess.run(["cci", "tail", "--drive", drive, "--lane", lane, "--since", LOOKBACK, "--json", "--budget", TAIL_BUDGET], capture_output=True, text=True, timeout=CCI_TIMEOUT_SECONDS)
    if done.returncode:
        return None
    return [found for line in done.stdout.splitlines() if line.startswith("{") and (found := json.loads(line))["drive"] == drive]


def worktree_branch(worktree: Path) -> str | None:
    done = subprocess.run(["git", "-C", str(worktree), "status", "--short", "--branch"], capture_output=True, text=True)
    if done.returncode:
        return None
    return done.stdout


def next_commands(drive: str, lane: str, worktree: Path, status: str | None) -> str:
    branch, *dirty = (status or "").splitlines() or [""]
    detached = branch.startswith("## HEAD (no branch)")
    name = lane if detached or not branch else branch.removeprefix("## ").split("...")[0]
    steps = [] if status is not None else [f"ccx vcs worktree add {lane}", f"write the fix in {worktree}, then:"]
    steps += [f"git -C {worktree} switch -c {lane}"] if detached else []
    steps += [f'git -C {worktree} add -A && git -C {worktree} commit -m "<component>: 🐛 <what the fix does>"'] if dirty or status is None else []
    steps += [
        f"git -C {worktree} push -u origin {name}",
        f"invoke the submit-pr skill from {worktree}",
        f'cci post --drive {drive} --lane {lane} --kind opened --pr <n> --to root --text "OPENED #<n> <what it fixes>"',
        f'or: cci post --drive {drive} --lane {lane} --kind blocker --to root --text "BLOCKER <what stops the fix>"',
    ]
    return "\n".join(steps)


def pushed(status: str | None) -> bool:
    branch = (status or "").partition("\n")[0]
    return "..." in branch and "[ahead" not in branch


@on(
    Event.Stop | Event.SubagentStop,
    only_if=[FromSubagent(), DriveActive()],
    skip_if=[Annotated("role", "evidence", scope="session")],
    tests={
        Input(agent_id=AGENT, state=ACTIVE, env=DRIVE, commands={TAIL: MECHANISM, STATUS: "## HEAD (no branch)\n"}): Block(pattern=r"^An incident lane may not stop after posting its mechanism with no pushed branch"),
        Input(agent_id=AGENT, state=ACTIVE, env=DRIVE, commands={TAIL: MECHANISM, STATUS: "## inc-liam-load-opus\n M rust/chain.rs\n"}): Block(pattern=r"git -C \S+ push -u origin inc-liam-load-opus"),
        Input(agent_id=AGENT, state=ACTIVE, env=DRIVE, commands={TAIL: f"{MECHANISM}\n{OPENED}", STATUS: "## HEAD (no branch)\n"}): Allow(),
        Input(agent_id=AGENT, state=ACTIVE, env=DRIVE, commands={TAIL: f"{MECHANISM}\n{BLOCKER}"}): Allow(),
        Input(agent_id=AGENT, state=ACTIVE, env=DRIVE, commands={TAIL: MECHANISM, STATUS: "## inc-liam-load-opus...origin/inc-liam-load-opus\n"}): Allow(),
        Input(agent_id="aenum-local-client-0123456789abcdef", state=ACTIVE, env=DRIVE): Allow(),
        Input(agent_id="ainc-fleet-evidence-0123456789abcdef", state=ACTIVE, env=DRIVE): Allow(),
        Input(agent_id=AGENT, env=DRIVE): Allow(),
    },
)
def incident_lane_ships_its_mechanism(evt: BaseHookEvent) -> HookResult | None:
    lane = lane_name(evt)
    if not is_lane(evt) or not lane or not INCIDENT_LANE.match(lane) or EVIDENCE_LANE.search(lane):
        return None
    entry = drive_entry(evt)
    if not entry or not entry.get("cci_drive"):
        return None
    records = lane_records(entry["cci_drive"], lane)
    if records is None or not mechanism_waits(records, lane):
        return None
    worktree = Path.home() / ".claude" / "worktrees" / Path(entry["git_common_dir"]).parent.name / lane
    status = worktree_branch(worktree)
    if pushed(status):
        return None
    return evt.block(f"{UNSHIPPED}\n\n```\n{next_commands(entry['cci_drive'], lane, worktree, status)}\n```")
