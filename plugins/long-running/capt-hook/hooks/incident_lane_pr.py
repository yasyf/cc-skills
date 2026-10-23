from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from captain_hook import Allow, Annotated, BaseHookEvent, Event, FromSubagent, HookResult, Input, Warn, on

from .compaction_handoff import CompactionState, script
from .lane_landing import drive_entry, lane_name
from .lane_reports import is_lane
from .lane_rotation import DriveActive
from .tests.lane_pr_fixtures import AGENT, DETACHED, DRIVE, NO_WORKTREE, NOT_OWED, OWED_MECHANISM, PUSHED, STATUS

INCIDENT_LANE = re.compile(r"^(?:inc|incident|outage)-")
FIX_LANE = re.compile(r"-(?:fix(?:-\d+)?|opus|case\d+)$")
NO_WORKTREE_FOUND = "no worktree found for lane"
WORKTREE_LINE = re.compile(r"^- (?P<path>/\S+) \(")
PUSHED_STATE = ", pushed, "
LANE_STATUS = f"{sys.executable} {script('drive.py')} lane-status"
PR_OWED = f"{sys.executable} {script('drive.py')} pr-owed"
OWED = "owed: "
CCI_TIMEOUT_SECONDS = 20
ACTIVE = [CompactionState(active=True)]
UNSHIPPED = (
    "Warning, nothing was blocked: An incident lane may not stop after posting its mechanism with no pushed branch. "
    "Run the commands below and post `opened` with the PR, or post `blocker` naming what stops the fix."
)


def pr_owed(drive: str, lane: str) -> bool:
    done = subprocess.run([sys.executable, str(script("drive.py")), "pr-owed", lane, "--drive", drive], capture_output=True, text=True, timeout=CCI_TIMEOUT_SECONDS)
    return done.returncode == 0 and done.stdout.startswith(OWED)


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


def lane_status(drive: str, lane: str) -> str | None:
    done = subprocess.run([sys.executable, str(script("drive.py")), "lane-status", lane, "--drive", drive], capture_output=True, text=True, timeout=CCI_TIMEOUT_SECONDS)
    return done.stdout if done.returncode in (0, 1) else None


@on(
    Event.Stop | Event.SubagentStop,
    only_if=[FromSubagent(), DriveActive()],
    skip_if=[Annotated("role", "evidence", scope="session")],
    tests={
        Input(agent_id=AGENT, state=ACTIVE, env=DRIVE, commands={PR_OWED: OWED_MECHANISM, LANE_STATUS: DETACHED, STATUS: "## HEAD (no branch)\n"}): Warn(pattern=r"^Warning, nothing was blocked: An incident lane may not stop after posting its mechanism with no pushed branch"),
        Input(agent_id=AGENT, state=ACTIVE, env=DRIVE, commands={PR_OWED: OWED_MECHANISM, LANE_STATUS: DETACHED, STATUS: "## inc-liam-load-opus\n M rust/chain.rs\n"}): Warn(pattern=r"git -C \S+ push -u origin inc-liam-load-opus"),
        Input(agent_id=AGENT, state=ACTIVE, env=DRIVE, commands={PR_OWED: OWED_MECHANISM, LANE_STATUS: NO_WORKTREE}): Warn(pattern=r"ccx vcs worktree add inc-liam-load-opus"),
        Input(agent_id=AGENT, state=ACTIVE, env=DRIVE, commands={PR_OWED: NOT_OWED}): Allow(),
        Input(agent_id=AGENT, state=ACTIVE, env=DRIVE, commands={PR_OWED: NOT_OWED, LANE_STATUS: DETACHED}): Allow(),
        Input(agent_id=AGENT, state=ACTIVE, env=DRIVE, commands={PR_OWED: OWED_MECHANISM, LANE_STATUS: f"{DETACHED}{PUSHED}"}): Allow(),
        Input(agent_id="aincident-closeout-0123456789abcdef", state=ACTIVE, env=DRIVE, commands={PR_OWED: NOT_OWED, LANE_STATUS: NO_WORKTREE}): Allow(),
        Input(agent_id="aenum-local-client-0123456789abcdef", state=ACTIVE, env=DRIVE): Allow(),
        Input(agent_id="ainc-fleet-evidence-0123456789abcdef", state=ACTIVE, env=DRIVE, commands={PR_OWED: NOT_OWED}): Allow(),
        Input(agent_id=AGENT, env=DRIVE): Allow(),
    },
)
def incident_lane_ships_its_mechanism(evt: BaseHookEvent) -> HookResult | None:
    lane = lane_name(evt)
    if not is_lane(evt) or not lane or not INCIDENT_LANE.match(lane):
        return None
    entry = drive_entry(evt)
    if not entry or not entry.get("cci_drive") or not pr_owed(entry["drive"], lane):
        return None
    found = lane_status(entry["drive"], lane)
    if found is None or PUSHED_STATE in found or (NO_WORKTREE_FOUND in found and not FIX_LANE.search(lane)):
        return None
    paths = [match["path"] for line in found.splitlines() if (match := WORKTREE_LINE.match(line))]
    worktree = Path(paths[0]) if paths else Path.home() / ".claude" / "worktrees" / Path(entry["git_common_dir"]).parent.name / lane
    status = worktree_branch(worktree) if paths else None
    return evt.context(f"{UNSHIPPED}\n\n```\n{found.strip()}\n\n{next_commands(entry['cci_drive'], lane, worktree, status)}\n```")
