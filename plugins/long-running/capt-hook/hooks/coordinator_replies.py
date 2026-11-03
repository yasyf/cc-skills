from __future__ import annotations

from captain_hook import Allow, Annotated, Event, FromSubagent, Input, Warn, warn_command

from .compaction_handoff import Coordinator, coordinator_tests
from .lane_rotation import DriveActive
from .tests.root_fixtures import ACTIVE

PR_GO = r"--kind[ =]go\b(?![\s\S]*orca-desk: launch)"
AD_HOC_SCRIPT = r"\bpython3?\s+(?:-c\b|-\s|/tmp/)|\bcat\s*>\s*/tmp/\S*\.py\b"


warn_command(
    PR_GO,
    message="Warning, nothing was blocked: A go on a PR reads its grades first, so it belongs to the drive's `landing.go_lanes` lane. Post the lane an `ask` naming the PR and keep launches, holds and overrides here.",
    only_if=[Coordinator(), DriveActive()],
    skip_if=[FromSubagent(), Annotated("raw")],
    events=Event.PreToolUse,
    tests=coordinator_tests({
        Input(command="cci post --lane root --drive d --kind go --pr 48577 --text 'go #48577 at 939f525 on green [exempt: regression]'", state=ACTIVE): Warn(pattern=r"landing\.go_lanes"),
        Input(command="cat > /tmp/coord.zsh <<'EOF'\nP --kind go --to desk-runner --text 'go #1 at abc'\nEOF\nzsh /tmp/coord.zsh", state=ACTIVE): Warn(),
        Input(command="cci post --lane root --drive d --kind go --to orca-desk --text 'orca-desk: launch lane-a NOW opus xhigh brief=/b'", state=ACTIVE): Allow(),
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
