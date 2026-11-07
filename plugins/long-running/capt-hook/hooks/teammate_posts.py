from __future__ import annotations

import shlex

from captain_hook import Allow, Annotated, Input, Occurrence, PreToolUseEvent, Rewrite, T, rewrite_command_occurrences

from .compaction_handoff import Bound
from .owner_facing import CCI_POST
from .session_tree import agent_lane
from .tests.register_fixtures import DRIVE_CONFIG

TEAMMATE = DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": "meta-desk-3"}
BRIEFED = [T.user('<teammate-message teammate_id="team-lead" summary="Parse live tasks">\nccx: lane=live-tasks-parse\nYour brief is /b/live-tasks-parse.md\n</teammate-message>', isSidechain=True)]
DONE = "cci post --drive d --kind done --to meta-desk-3 --text 'merged #3470'"


def with_own_lane(evt: PreToolUseEvent, occ: Occurrence) -> str | None:
    call = next((call for call in evt.command.calls("cci") if call.occurrence.index == occ.index), None)
    if call is None or (values := CCI_POST.bind(call).values).get("words", ())[:1] != ("post",) or values.get("lane") or not (lane := agent_lane(evt)):
        return None
    start, end = occ.command.span
    verb = next(word for word in call.command.words[1:] if word.value == "post").span[1]
    source = occ.line.raw.encode()
    return (source[start:verb] + f" --lane {shlex.quote(lane)}".encode() + source[verb:end]).decode()


rewrite_command_occurrences(
    to=with_own_lane,
    only_if=[Bound()],
    skip_if=[Annotated("raw")],
    note="Added `--lane` from your `ccx: lane=` line to this `cci post`: a teammate shares its desk's environment, so a post naming no lane would read as the desk's.",
    tests={
        Input(command=DONE, env=TEAMMATE, agent_id="a1b2c3", transcript=BRIEFED): Rewrite(pattern="cci post --lane live-tasks-parse --drive d --kind done --to meta-desk-3"),
        Input(command="git log -1 && cci post --kind landed --pr 12 'merged #12' | tail -1", env=TEAMMATE, agent_id="a1b2c3", transcript=BRIEFED): Rewrite(pattern="git log -1 && cci post --lane live-tasks-parse --kind landed --pr 12 'merged #12' | tail -1"),
        Input(command=DONE.replace("--drive d", "--drive d --lane live-tasks-parse"), env=TEAMMATE, agent_id="a1b2c3", transcript=BRIEFED): Allow(),
        Input(command="cci post --lane=live-tasks-parse --kind note x", env=TEAMMATE, agent_id="a1b2c3", transcript=BRIEFED): Allow(),
        Input(command="cci tail --drive d --reader live-tasks-parse", env=TEAMMATE, agent_id="a1b2c3", transcript=BRIEFED): Allow(),
        Input(command=DONE, env=TEAMMATE, transcript=BRIEFED): Allow(),
        Input(command=DONE, env=TEAMMATE, agent_id="a1b2c3", transcript=[T.user("read the build log")]): Allow(),
        Input(command=f"{DONE}  # ccx:raw", env=TEAMMATE, agent_id="a1b2c3", transcript=BRIEFED): Allow(),
    },
)
