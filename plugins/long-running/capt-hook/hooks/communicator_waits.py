from __future__ import annotations

from captain_hook import Allow, Event, FromSubagent, Input, Runs, Warn, hook

from .compaction_handoff import Communicator, communicator_tests, CompactionState

ACTIVE = [CompactionState(active=True, communicator=True)]
UNTIL = "until grep -q done out.txt; do sleep 5; done"


hook(
    Event.PreToolUse,
    message="Warning, nothing was blocked: A sleep loop holds his next message until it ends. Arm Monitor on the output, or end the turn: a background task's notification wakes you.",
    only_if=[Communicator(), Runs("sleep")],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(command=UNTIL, state=ACTIVE): Warn(pattern=r"Arm Monitor"),
        Input(command="sleep 20; cat out.txt", state=ACTIVE): Warn(),
        Input(tool="Bash", tool_input={"command": UNTIL, "run_in_background": True}, state=ACTIVE): Warn(),
        Input(command="git status", state=ACTIVE): Allow(),
        Input(command=UNTIL, agent_id="a1b2c3", state=ACTIVE): Allow(),
        Input(command=UNTIL): Allow(),
    }),
)
