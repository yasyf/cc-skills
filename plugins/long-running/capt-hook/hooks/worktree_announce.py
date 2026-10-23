from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Tool, WorkflowState, on
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState, script
from .lane_landing import lane_name
from .lane_reports import is_lane
from .lane_rotation import DriveActive

WORKTREES = Path(".claude") / "worktrees"
WORKTREE_DEPTH = 2
ANNOUNCE_TIMEOUT_SECONDS = 20
OUTSIDE_REPO = 3
ACTIVE = [CompactionState(active=True)]
AGENT = "ainc-liam-load-opus-0123456789abcdef"
WORKTREE = str(Path(__file__).parent / "tests" / "fixtures" / "lane-home" / ".claude" / "worktrees" / "monorepo" / "inc-liam-fork-parent-grant")
ENV = {"CLAUDE_LONG_RUNNING_DRIVE": "900424b6"}
ANNOUNCE = f"{sys.executable} {script('drive.py')} worktree"


class AnnouncedWorktrees(WorkflowState):
    keys: list[str] = []


def worktree_root(path: Path) -> Path | None:
    parts = path.parts
    found = next((index for index in reversed(range(len(parts) - 1)) if parts[index : index + 2] == WORKTREES.parts), None)
    end = None if found is None else found + len(WORKTREES.parts) + WORKTREE_DEPTH
    return Path(*parts[:end]) if end is not None and len(parts) >= end else None


def touched_dirs(evt: BaseHookEvent) -> list[Path]:
    dirs: list[Path] = []
    for call in evt.cmd.calls():
        if call.cwd:
            dirs.append(Path(call.cwd))
        options = call.leading_options if call.name == "git" else ()
        dirs += [Path(value) for flag, value in zip(options, options[1:], strict=False) if flag == "-C"]
    return dirs


@on(
    Event.PostToolUse,
    only_if=[Tool("Bash"), FromSubagent(), DriveActive()],
    tests={
        Input(command=f"cd {WORKTREE}/rust && git status", agent_id=AGENT, state=ACTIVE, env=ENV, commands={ANNOUNCE: ""}): Allow(),
        Input(command=f"git -C {WORKTREE} diff --stat", agent_id=AGENT, state=ACTIVE, env=ENV, commands={ANNOUNCE: ""}): Allow(),
        Input(command=f"git -C {WORKTREE} diff --stat", agent_id=AGENT, state=[*ACTIVE, AnnouncedWorktrees(keys=[f"inc-liam-load-opus:{WORKTREE}"])], env=ENV): Allow(),
        Input(command="git -C /repo status", agent_id=AGENT, state=ACTIVE, env=ENV): Allow(),
        Input(command=f"cd {WORKTREE} && git status", agent_id=AGENT, env=ENV): Allow(),
    },
)
def announce_lane_worktree(evt: BaseHookEvent) -> HookResult | None:
    lane = lane_name(evt)
    if not is_lane(evt) or not lane:
        return None
    fresh = [path for path in dict.fromkeys(filter(None, map(worktree_root, touched_dirs(evt)))) if f"{lane}:{path}" not in AnnouncedWorktrees.load(evt).keys]
    settled: list[Path] = []
    for path in fresh:
        argv = [sys.executable, str(script("drive.py")), "worktree", "--lane", lane, "--path", str(path), "--session", evt.session_id]
        argv += ["--drive", drive] if (drive := reqenv.getenv("CLAUDE_LONG_RUNNING_DRIVE")) else []
        if subprocess.run(argv, capture_output=True, text=True, timeout=ANNOUNCE_TIMEOUT_SECONDS).returncode in (0, OUTSIDE_REPO):
            settled.append(path)
    if settled:
        with AnnouncedWorktrees.mutate(evt) as state:
            state.keys = [*state.keys, *(f"{lane}:{path}" for path in settled)]
    return None
