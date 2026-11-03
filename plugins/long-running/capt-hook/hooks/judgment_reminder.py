from __future__ import annotations

import time

from captain_hook import (
    Allow,
    BaseHookEvent,
    Event,
    FromSubagent,
    HookResult,
    Input,
    Warn,
    WorkflowState,
    on,
    workflow_state,
)

from .compaction_handoff import Communicator, communicator_tests, CompactionState, drive_entry

DEFAULT_MINUTES = 30
REMINDER = (
    "Judgment first: read the owner's message as a colleague would, not word by word, "
    "and take the smallest action that fully does it. "
    "Fix a finding in hand instead of re-auditing it, and ask only about a genuine ambiguity."
)


@workflow_state("long_running_judgment_reminder")
class ReminderClock(WorkflowState):
    said_at: float = 0.0


def interval_seconds(evt: BaseHookEvent) -> float:
    entry = drive_entry(evt) or {}
    return 60 * float(entry.get("judgment_reminder_minutes", DEFAULT_MINUTES))


@on(
    Event.UserPromptSubmit,
    skip_if=[FromSubagent()],
    only_if=[Communicator()],
    tests=communicator_tests({
        Input(prompt="keep going", state=[CompactionState(active=True, coordinator=True)]): Warn(pattern=r"^Judgment first: read the owner's message as a colleague would"),
        Input(prompt="keep going", state=[CompactionState(active=True, coordinator=True), ReminderClock(said_at=time.time())]): Allow(),
        Input(prompt="keep going", state=[CompactionState()]): Allow(),
    }),
)
def remind_judgment_first(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    now = time.time()
    with ReminderClock.mutate(evt) as clock:
        if now - clock.said_at < interval_seconds(evt):
            return None
        clock.said_at = now
    return evt.context(REMINDER)
