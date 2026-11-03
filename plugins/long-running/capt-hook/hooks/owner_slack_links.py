from __future__ import annotations

import json
import re
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, T, on

from .compaction_handoff import CompactionState
from .root_replies import ACTIVE, PACIFIC, REPLY_WINDOW, SYSTEM_PREFIXES, owner_text
from .window_lane import Coordinator, coordinator_tests

PERMALINK = re.compile(r"https://[\w-]+\.slack\.com/archives/[A-Z0-9]+/p(?P<seconds>\d{10})(?P<micros>\d{6})(?:\?\S*?thread_ts=(?P<thread>\d{10}\.\d{6}))?")
HANDOFF_TOOLS = frozenset({
    "mcp__plugin_cc-slack_cc-slack__slack_reply",
    "mcp__plugin_cc-slack_cc-slack__slack_send",
    "mcp__slack__slack_send_message",
    "Agent",
    "SendMessage",
    "Write",
    "Edit",
})
HANDOFF_COMMANDS = (("cci", "post"), ("cc-slack", "reply"), ("cc-slack", "send"))
UNROUTED = (
    "A Slack thread the owner linked has no reply and no lane, message, record or status file naming it. "
    "Reply there with `slack_reply`, or hand the link to a comms lane, then stop again.\n```\n{lines}\n```"
)

LINK = "https://poetic-technology.slack.com/archives/C0BQSADC7SS/p1793635190051179?thread_ts=1793632907.983749&cid=C0BQSADC7SS"
ASKED = T.user(f"why are you not following up on the bug reports {LINK}", timestamp="2026-11-02T18:33:00Z")


def stamps(link: re.Match[str]) -> set[str]:
    seconds, micros = link["seconds"], link["micros"]
    found = {f"{seconds}.{micros}", f"p{seconds}{micros}"}
    if thread := link["thread"]:
        found |= {thread, f"p{thread.replace('.', '')}"}
    return found


def names(use: object, wanted: set[str]) -> bool:
    raw = json.dumps(use.call.raw)
    if not any(stamp in raw for stamp in wanted):
        return False
    if use.call.name in HANDOFF_TOOLS:
        return True
    return use.call.name == "Bash" and any((Path(cmd.argv[0]).name, *cmd.argv[1:2]) in HANDOFF_COMMANDS for cmd in use.call.command_line if len(cmd.argv) > 1)


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    only_if=[Coordinator()],
    tests=coordinator_tests({
        Input(transcript=[ASKED, T.assistant("Looking into the bug reports.", timestamp="2026-11-02T18:34:00Z")], state=ACTIVE): Block(pattern=r"10:33 AM https://poetic-technology\.slack\.com"),
        Input(
            transcript=[ASKED, T.assistant(T.tool("mcp__plugin_cc-slack_cc-slack__slack_thread", channel_id="C0BQSADC7SS", thread_ts="1793632907.983749"), timestamp="2026-11-02T18:34:00Z")],
            state=ACTIVE,
        ): Block(pattern=r"10:33 AM"),
        Input(
            transcript=[ASKED, T.assistant(T.tool("mcp__plugin_cc-slack_cc-slack__slack_reply", channel_id="C0BQSADC7SS", thread_ts="1793632907.983749", text="On it."), timestamp="2026-11-02T18:34:00Z")],
            state=ACTIVE,
        ): Allow(),
        Input(
            transcript=[ASKED, T.assistant(T.tool("Agent", name="bugs-comms-1", prompt=f"Follow up in {LINK}"), timestamp="2026-11-02T18:34:00Z")],
            state=ACTIVE,
        ): Allow(),
        Input(
            transcript=[ASKED, T.assistant(T.tool("Bash", command="cci post --lane root --kind go --to bugs-comms-1 --text 'reply in thread 1793632907.983749'"), timestamp="2026-11-02T18:34:00Z")],
            state=ACTIVE,
        ): Allow(),
        Input(transcript=[T.user(f"<teammate-message>{LINK}</teammate-message>", timestamp="2026-11-02T18:33:00Z"), T.assistant("Noted.")], state=ACTIVE): Allow(),
        Input(transcript=[ASKED, T.assistant("Looking into the bug reports.")]): Allow(),
    }),
)
def owner_slack_links_get_routed(evt: BaseHookEvent) -> HookResult | None:
    """The drive root stops only once every Slack thread the owner linked has a reply posted there or a handoff that names the thread: an Agent prompt, a SendMessage, a status file write, a `cci post` or a `cc-slack reply`."""
    if not CompactionState.load(evt).active:
        return None
    calls = evt.ctx.t.tool_calls.items
    lines = []
    for event in evt.ctx.t.events:
        if not (text := owner_text(event)) or text.lstrip().startswith(SYSTEM_PREFIXES):
            continue
        asked = event.meta.timestamp
        for link in PERMALINK.finditer(text):
            wanted = stamps(link)
            if not any(use.ts >= asked and names(use, wanted) for use in calls):
                lines.append(f"{asked.astimezone(PACIFIC):%-I:%M %p} {link[0]}")
    return evt.block(UNROUTED.format(lines="\n".join(dict.fromkeys(lines)))) if lines else None
