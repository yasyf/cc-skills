from __future__ import annotations

from captain_hook import Allow, BaseHookEvent, Event, Input, Warn, warn_command

from .compaction_handoff import Bound, CompactionState
from .lane_landing import lane_name
from .lane_rotation import DriveActive

ACTIVE = [CompactionState(active=True, coordinator=True)]
LANE = "alanding-desk-3-0123456789abcdef"
TIMEOUT_SHELL = r"\btimeout\s+(?:-\S+\s+)*\d+[smhd]?\s+(?:\S*/)?(?:zsh|bash|sh|fish)\b"
BACKGROUND = r"\b(?:nohup|setsid|disown)\b|(?<![&|>])&(?!&)(?=\s*(?:\Z|;|\n|\)))"
WATCHER = r"\bdesk-wait\.sh\b|\bcci\s+watch\b"
GUARDED = r"\s*(?:\S*/)?lr\s+wait\b.*\s--\s"
SHELL_WAIT = rf"(?s)\A(?!{GUARDED}).*?(?:{TIMEOUT_SHELL}|{BACKGROUND}|{WATCHER})"


class InLane:
    def check(self, evt: BaseHookEvent) -> bool:
        return bool(lane_name(evt))


warn_command(
    SHELL_WAIT,
    message=(
        "A lane never backgrounds a process, wraps a shell in `timeout`, or runs a watcher: "
        "wait with `Monitor`, with one `lr wait <seconds> <source>... [-- <command>]` call, which follows its sources and exits, "
        "or end the turn on a typed record naming who resumes you."
    ),
    only_if=[Bound(), InLane(), DriveActive()],
    events=Event.PreToolUse,
    tests={
        Input(command="timeout 240 zsh /Users/me/.claude/scratch/d/landing-desk-3/loop3.sh", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`lr wait <seconds> <source>"),
        Input(command="nohup python3 poll.py > poll.log 2>&1 &", agent_id=LANE, state=ACTIVE): Warn(),
        Input(command="tail -f run.log | grep --line-buffered ERROR &", agent_id=LANE, state=ACTIVE): Warn(),
        Input(command="desk-wait.sh 50 mailbox:landing-desk-3=/c", agent_id=LANE, state=ACTIVE): Warn(),
        Input(command="cci watch --drive d --cursor x --to x", env={"CLAUDE_LONG_RUNNING_LANE": "orca-lane-2"}, state=ACTIVE): Warn(),
        Input(command="lr wait 540 mailbox:landing-desk-3=/c -- bk build watch 12", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="lr wait 540 mailbox:drive-watchdog=/c -- cci watch --drive d --cursor drive-watchdog --kind state,ask", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="/h/.claude/long-running/current/bin/lr wait 540 mailbox:drive-watchdog=/c -- cci watch --drive d --cursor drive-watchdog", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="git status && git log --oneline -3 2>&1", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="sleep 5; cci tail --drive d --to x", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="cci post --kind note --text 'R & D; do it for every lane'", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="timeout 30 cci tail --drive d", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="nohup python3 poll.py &", state=ACTIVE): Allow(),
        Input(command="nohup python3 poll.py &", agent_id="a0123456789abcdef", state=ACTIVE): Allow(),
        Input(command="nohup python3 poll.py &", agent_id=LANE): Allow(),
    },
)
