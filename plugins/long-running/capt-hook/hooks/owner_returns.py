from __future__ import annotations

import re
import time
from pathlib import Path

from captain_hook import Allow, AssistantMessage, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, T, Warn, WorkflowState, on, workflow_state

from .compaction_handoff import Communicator, communicator_tests, CompactionState, drive_entry
from .root_context import OwnerPrompt
from .tests.register_fixtures import DRIVE_CONFIG

RETURN_GAP_SECONDS = 60 * 60
STEPS = Path("progress-desk") / "steps.json"
LINK = re.compile(r"https?://\S+")
OWNER_TABLE = re.compile(r"^#+ .+\n(?:.*\n)*?\| Row \| What it delivers \|", re.MULTILINE)
RETURNED = "The owner is back after {minutes} minutes away. Open this reply with the full update: the dashboard link on its first line, then the owner table grouped by feature from `{steps}`."
MISSING = "A reply to the owner's return opens with the full update, and this one lacks {missing}. Rewrite it to open with the dashboard link, then the owner table under a heading per feature."
NO_LINK = "the dashboard link on its first line"
NO_TABLE = "the owner table under a feature heading"
UPDATE = "http://dash.brook.ts.net:8840/\n\n## Durable agents, step Rebuild\n| Row | What it delivers | State | Who is on it right now | What blocks it | What we are doing to unblock it, and by when | What went wrong or slowed it |\n|---|---|---|---|---|---|---|\n| Step 2 | Agents resume | open | fix-3 | nothing recorded | fix-3 lands it | None recorded |"


@workflow_state("long_running_owner_returns")
class OwnerReturns(WorkflowState):
    prompted_at: float | None = None
    returned: bool = False


def away(minutes: float) -> list:
    return [CompactionState(active=True), OwnerReturns(prompted_at=time.time() - 60 * minutes)]


def back(reply: str, returned: bool = True) -> Input:
    return Input(transcript=[T.user("where are we"), T.assistant(reply)], state=[CompactionState(active=True), OwnerReturns(returned=returned)])


@on(
    Event.UserPromptSubmit,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(prompt="where are we", state=away(61), env=DRIVE_CONFIG): Warn(pattern=r"^The owner is back after 61 minutes away\..*`/s/brook/progress-desk/steps\.json`"),
        Input(prompt="where are we", state=away(59), env=DRIVE_CONFIG): Allow(),
        Input(prompt="where are we", state=[CompactionState(active=True)], env=DRIVE_CONFIG): Allow(),
        Input(prompt="<task-notification>update-desk finished</task-notification>", state=away(61), env=DRIVE_CONFIG): Allow(),
        Input(prompt="where are we", state=away(61), env=DRIVE_CONFIG, agent_id="a1b2c3"): Allow(),
    }),
)
def owner_return_opens_with_the_update(evt: BaseHookEvent) -> HookResult | None:
    now, owner = time.time(), OwnerPrompt().check(evt)
    with OwnerReturns.mutate(evt) as state:
        gap = now - (state.prompted_at or now)
        state.returned = owner and gap >= RETURN_GAP_SECONDS
        state.prompted_at = now if owner else state.prompted_at
    if not state.returned:
        return None
    return evt.context(RETURNED.format(minutes=int(gap // 60), steps=Path(drive_entry(evt)["state_dir"]) / STEPS))


@on(
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        back("Since you were away, three PRs merged.\n\n" + UPDATE): Block(pattern=r"lacks the dashboard link on its first line\."),
        back("http://dash.brook.ts.net:8840/\n\nThree PRs merged while you were away."): Block(pattern=r"lacks the owner table under a feature heading\."),
        back(UPDATE): Allow(),
        back("Since you were away, three PRs merged.", returned=False): Allow(),
    }),
)
def owner_return_reply_leads_with_the_update(evt: BaseHookEvent) -> HookResult | None:
    if not OwnerReturns.load(evt).returned:
        return None
    reply = AssistantMessage().content(evt) or ""
    missing = [gap for gap, found in ((NO_LINK, LINK.search(reply.split("\n", 1)[0])), (NO_TABLE, OWNER_TABLE.search(reply))) if not found]
    return evt.block(MISSING.format(missing=" and ".join(missing))) if missing else None
