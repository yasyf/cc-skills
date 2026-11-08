from __future__ import annotations

from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Runs, Tool, on

from .call_dir import run_dir, unknown_dir
from .compaction_handoff import Bound
from .lane_landing import DRIVE_ENV
from .vet_before_push import in_lane

REPLAYS = frozenset({"rebase", "merge", "cherry-pick", "am", "pull"})
CANNOT_CONFLICT = frozenset({"--abort", "--continue", "--skip", "--quit", "--ff-only"})
LANE_ENV = DRIVE_ENV | {"CLAUDE_LONG_RUNNING_LANE": "pr-unblock-19"}
OWN = Path(__file__).parents[1]
INSIDE = OWN / "hooks"
OUTSIDE = OWN.parent
REFUSAL = (
    "A lane never runs `git {verb}` in its own checkout, since a conflict there leaves markers in the `.claude/hooks` its guards load from."
    " Run it in a worktree of its own from `ccx vcs worktree add`, or use `ccx vcs stack rebase`."
)


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash"), Runs("git")],
    tests={
        Input(cwd=str(OWN), command="git rebase origin/dev 2>&1 | tail -6 # ccx:raw", env=LANE_ENV): Block(pattern=r"^A lane never runs `git rebase` in its own checkout"),
        Input(cwd=str(OWN), command=f"cd {INSIDE} && git cherry-pick abc123", env=LANE_ENV): Block(pattern=r"`git cherry-pick` in its own checkout"),
        Input(cwd=str(OWN), command="git pull --rebase origin dev", env=LANE_ENV): Block(pattern=r"`git pull` in its own checkout"),
        Input(cwd=str(OWN), command=f"cd {OUTSIDE} && git rebase origin/dev # ccx:raw", env=LANE_ENV): Allow(),
        Input(cwd=str(OWN), command="git rebase --abort", env=LANE_ENV): Allow(),
        Input(cwd=str(OWN), command="git merge-base origin/dev HEAD", env=LANE_ENV): Allow(),
        Input(cwd=str(OWN), command="git pull --ff-only", env=LANE_ENV): Allow(),
        Input(cwd=str(OWN), command="git rebase origin/dev"): Allow(),
    },
)
def lanes_never_replay_commits_in_their_own_checkout(evt: BaseHookEvent) -> HookResult | None:
    if not in_lane(evt) or evt.cwd is None:
        return None
    own = Path(evt.cwd)
    for call in evt.command.calls():
        verb = call.verb_argv[1] if call.name == "git" and len(call.verb_argv) > 1 else ""
        if verb not in REPLAYS or CANNOT_CONFLICT.intersection(call.args) or unknown_dir(evt, call):
            continue
        if Path(run_dir(evt, call)).is_relative_to(own):
            return evt.block(REFUSAL.format(verb=verb))
    return None
