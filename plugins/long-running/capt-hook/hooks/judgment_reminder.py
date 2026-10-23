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

from .compaction_handoff import CompactionState, drive_entry

DEFAULT_MINUTES = 30
REMINDER = """Judgment first. The owner, verbatim (4ac9243): "use hour judgement snd uour brain ... no literal mode ... no using tooling as excises for not doign whay is say" and "alwaus use the best tool for the job dont just be mechanical".
- Act on what his sentence means, read as a colleague would, not on its literal words.
- Take the smallest action that fully does it: one direct edit beats a sweep, one worker beats a fan-out.
- A decision that turns on a line of code is made by reading that line, not by spawning an audit.
- A finding in hand is fixed, not re-audited.
- A repeat from the owner is a defect report about your reading: find every place it survives and fix them all.
- Ask the owner only for a genuine ambiguity; decide everything else.
- A tool that refuses or breaks is fixed forward in a side lane, never an excuse and never a question to him."""


@workflow_state("long_running_judgment_reminder")
class ReminderClock(WorkflowState):
    said_at: float = 0.0


def interval_seconds(evt: BaseHookEvent) -> float:
    entry = drive_entry(evt) or {}
    return 60 * float(entry.get("judgment_reminder_minutes", DEFAULT_MINUTES))


@on(
    Event.UserPromptSubmit,
    skip_if=[FromSubagent()],
    tests={
        Input(prompt="keep going", state=[CompactionState(active=True)]): Warn(pattern=r"^Judgment first\."),
        Input(prompt="keep going", state=[CompactionState(active=True), ReminderClock(said_at=time.time())]): Allow(),
        Input(prompt="keep going", state=[CompactionState()]): Allow(),
    },
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
