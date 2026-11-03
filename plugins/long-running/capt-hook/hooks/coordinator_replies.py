from __future__ import annotations

from captain_hook import Allow, Annotated, BaseHookEvent, Event, FromSubagent, HookResult, Input, T, Tool, Warn, on, warn_command

from .compaction_handoff import Coordinator, coordinator_tests, TURN_WINDOW
from .lane_rotation import DriveActive
from .tests.root_fixtures import ACTIVE

WAIT = "lr coordinator next"
POST = "cci post"
NEXT_CALL = "lr coordinator next --drive d --cursor /drive/root/next.cursor"
REPLY = "cci post --lane root --drive d --kind decide --to lane-a --text 'take the second branch'"
PR_GO = r"--kind[ =]go\b(?![\s\S]*orca-desk: launch)"
AD_HOC_SCRIPT = r"\bpython3?\s+(?:-c\b|-\s|/tmp/)|\bcat\s*>\s*/tmp/\S*\.py\b"


def batch(*calls) -> list:
    return [T.user("drive"), T.assistant(*(T.tool("Bash", command=command) for command in calls))]


@on(
    Event.PreToolUse,
    only_if=[Coordinator(), DriveActive(), Tool("Bash")],
    skip_if=[FromSubagent(), Annotated("raw")],
    transcript_events=TURN_WINDOW,
    tests=coordinator_tests({
        Input(command=REPLY, transcript=batch(NEXT_CALL, REPLY), state=ACTIVE): Warn(pattern=r"one call"),
        Input(command="cat > /tmp/coord.zsh <<'EOF'\ncci post --kind go\nEOF\nzsh /tmp/coord.zsh", transcript=batch(NEXT_CALL, "cat > /tmp/c.zsh <<'EOF'\ncci post --kind decide\nEOF"), state=ACTIVE): Warn(),
        Input(command=REPLY, transcript=batch(NEXT_CALL), state=ACTIVE): Allow(),
        Input(command=REPLY, transcript=batch(REPLY, NEXT_CALL), state=ACTIVE): Allow(),
        Input(command="cci who --drive d", transcript=batch(NEXT_CALL, REPLY), state=ACTIVE): Allow(),
        Input(command=REPLY, transcript=batch(NEXT_CALL, REPLY)): Allow(),
    }),
)
def one_reply_call_per_batch(evt: BaseHookEvent) -> HookResult | None:
    if POST not in evt.command.raw:
        return None
    since_wait: list[str] = []
    for use in evt.ctx.turn.tool_calls.named("Bash").items:
        command = use.call.raw.get("command") or ""
        since_wait = [] if WAIT in command else [*since_wait, command]
    if not any(POST in command for command in since_wait):
        return None
    return evt.context("Warning, nothing was blocked: Each call re-reads the coordinator's whole context, so a batch gets one reply call. Put every post in one script, beside the next `lr coordinator next` in the same message.")


warn_command(
    PR_GO,
    message="Warning, nothing was blocked: A go on a PR reads its grades first, so it belongs to the drive's `landing.go_lanes` lane. Post the lane an `ask` naming the PR and keep launches, holds and overrides here.",
    only_if=[Coordinator(), DriveActive()],
    skip_if=[FromSubagent(), Annotated("raw")],
    events=Event.PreToolUse,
    tests=coordinator_tests({
        Input(command="cci post --lane root --drive d --kind go --pr 48577 --text 'go #48577 at 939f525 on green [exempt: regression]'", state=ACTIVE): Warn(pattern=r"landing\.go_lanes"),
        Input(command="cat > /tmp/coord.zsh <<'EOF'\nP --kind go --to desk-runner --text 'go #1 at abc'\nEOF\nzsh /tmp/coord.zsh", state=ACTIVE): Warn(),
        Input(command="cci post --lane root --drive d --kind go --to orca-desk --text 'R9 (9:00 PM) orca-desk: launch lane-a NOW opus xhigh brief=/b'", state=ACTIVE): Allow(),
        Input(command="cci post --lane root --drive d --kind hold --pr 1 --text 'hold #1'", state=ACTIVE): Allow(),
        Input(command="cci post --lane go-sweep-8 --drive d --kind go --pr 1 --text 'go #1'", agent_id="a1b2c3", state=ACTIVE): Allow(),
        Input(command="cci post --lane root --drive d --kind go --pr 1 --text 'go #1'"): Allow(),
    }),
)

warn_command(
    AD_HOC_SCRIPT,
    message="Warning, nothing was blocked: An ad hoc script stays in the coordinator's context for good. Post the chore to lane `chores` with `--kind ask`, and its answer arrives in a later batch.",
    only_if=[Coordinator(), DriveActive()],
    skip_if=[FromSubagent(), Annotated("raw")],
    events=Event.PreToolUse,
    tests=coordinator_tests({
        Input(command="cat > /tmp/coord-0914.py <<'EOF'\nimport json\nEOF\npython3 /tmp/coord-0914.py", state=ACTIVE): Warn(pattern=r"lane `chores`"),
        Input(command="python3 - <<'PY'\nimport json\nPY", state=ACTIVE): Warn(),
        Input(command="python3 -c 'print(1)'", state=ACTIVE): Warn(),
        Input(command="python3 /plugin/bin/rulings.py classify 629293ae", state=ACTIVE): Allow(),
        Input(command="cci who --drive d", state=ACTIVE): Allow(),
        Input(command="python3 -c 'print(1)'", agent_id="a1b2c3", state=ACTIVE): Allow(),
        Input(command="python3 -c 'print(1)'"): Allow(),
    }),
)
