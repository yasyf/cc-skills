from __future__ import annotations

import subprocess
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import Bound, CompactionState

BOUND = [CompactionState(active=True, coordinator=True)]

LR = Path(__file__).parents[2] / "bin" / "lr"
LIST_SECONDS = 20
LIST = f"{LR} handoff live-tasks"
LINE = "- Monitor \"ci log\" (task aaa): `Monitor` with `{}`\n"
RESTARTED = "This session restarted and these background tasks died with it, so start each one again with the call it names unless its work is done:\n{tasks}"
COMPACTED = "These background tasks were live when this session compacted and still run, so their notifications still arrive here:\n{tasks}"
HEADS = {"resume": RESTARTED, "compact": COMPACTED}


@on(
    Event.SessionStart,
    only_if=[Bound()],
    tests={
        Input(source="resume", commands={LIST: LINE}, state=BOUND): Warn(pattern=r"^This session restarted and .*\n- Monitor \"ci log\" \(task aaa\)"),
        Input(source="compact", commands={LIST: LINE}, state=BOUND): Warn(pattern=r"^These background tasks were live when this session compacted"),
        Input(source="resume", commands={LIST: ""}, state=BOUND): Allow(),
        Input(source="resume", commands={LIST: (1, "open transcript: no such file")}, state=BOUND): Warn(pattern=r"^Could not list this session's background tasks"),
        Input(source="startup", commands={LIST: LINE}, state=BOUND): Allow(),
        Input(source="clear", commands={LIST: LINE}, state=BOUND): Allow(),
    },
)
def a_restart_or_compaction_names_the_tasks_it_lost(evt: BaseHookEvent) -> HookResult | None:
    """A background task leaves no trace in a session's context once it restarts or compacts; the transcript is the record of what was running."""
    head = HEADS.get(evt._raw.get("source", ""))
    if not head or evt.agent_id:
        return None
    done = subprocess.run(
        [str(LR), "handoff", "live-tasks", "--transcript", evt._raw.get("transcript_path", "")],
        capture_output=True,
        text=True,
        timeout=LIST_SECONDS,
        env=dict(reqenv.env_map()),
    )
    if done.returncode != 0:
        return evt.context(f"Could not list this session's background tasks: {(done.stderr or done.stdout).strip()}. Check what it had running by hand.")
    return evt.context(head.format(tasks=done.stdout.rstrip())) if done.stdout.strip() else None
