from __future__ import annotations

from cc_transcript import AttachmentEvent, DeferredToolsDelta
from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Or, Warn, on

from .compaction_handoff import Bound, Communicator, Coordinator, shared_tests
from .tests.root_fixtures import ACTIVE

DRIVE_SERVERS = ("mcp__plugin_long-running_lr__", "mcp__plugin_cc-inbox_cci__")
REPLY = "mcp__plugin_long-running_lr__reply"
POST = "mcp__plugin_cc-inbox_cci__post"
SLACK = "mcp__plugin_cc-slack_cc-slack__slack_send"


def delta(added: list[str], removed: list[str] | None = None) -> dict:
    return {"type": "attachment", "timestamp": "2026-11-03T09:00:00Z", "attachment": {"type": "deferred_tools_delta", "addedNames": added, "addedLines": added, "removedNames": removed or []}}


def deferred_drive_tools(evt: BaseHookEvent) -> list[str]:
    names: dict[str, None] = {}
    for event in evt.ctx.t.events:
        if isinstance(event, AttachmentEvent) and isinstance(found := event.detail, DeferredToolsDelta):
            names |= dict.fromkeys(name for name in found.added_names if name.startswith(DRIVE_SERVERS))
            for name in found.removed_names:
                names.pop(name, None)
    return list(names)


@on(
    Event.SessionStart,
    only_if=[Bound(), Or(Coordinator(), Communicator())],
    tests=shared_tests({
        Input(source="compact", transcript=[delta([REPLY, POST, SLACK])], state=ACTIVE): Warn(pattern=rf"select:{REPLY},{POST}` and `max_results` 2"),
        Input(source="compact", transcript=[delta([REPLY, POST]), delta([], [POST])], state=ACTIVE): Warn(pattern=rf"select:{REPLY}` and `max_results` 1"),
        Input(source="compact", transcript=[delta([REPLY]), delta([REPLY])], state=ACTIVE): Warn(pattern=rf"select:{REPLY}` and `max_results` 1"),
        Input(source="compact", state=ACTIVE): Allow(),
        Input(source="compact", transcript=[delta([SLACK])], state=ACTIVE): Allow(),
        Input(source="startup", transcript=[delta([REPLY])], state=ACTIVE): Allow(),
        Input(source="resume", transcript=[delta([REPLY])], state=ACTIVE): Allow(),
        Input(source="compact", transcript=[delta([REPLY])]): Allow(),
        Input(source="compact", transcript=[delta([REPLY])], state=ACTIVE, agent_id="a1b2c3"): Allow(),
    }),
)
def load_deferred_drive_tools_after_compacting(evt: BaseHookEvent) -> HookResult | None:
    if evt.source != "compact" or not (names := deferred_drive_tools(evt)):
        return None
    return evt.context(f"This session first listed its drive tools as deferred, so they stay behind tool search for its whole life and compacting just dropped their schemas. Before calling any of them, run `ToolSearch` with query `select:{','.join(names)}` and `max_results` {len(names)}.")
