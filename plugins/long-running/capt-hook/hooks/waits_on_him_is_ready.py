from __future__ import annotations

import json
import re
import subprocess

from captain_hook import Allow, And, Annotated, BaseHookEvent, Block, Event, HookResult, Input, Or, Runs, Tool, Warn, on
from captain_hook.util import reqenv

from .board_text_gate import blocks, own_text, published
from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import LR, Bound, bus_name, drive_entry
from .meta_records import posts
from .owner_facing import CCI_POST, Reads
from .root_replies import tap_claims, unready

PR_NUMBER = re.compile(r"(\d+)$")
ROWS = f"{LR} board rows"
ENV = {"CLAUDE_LONG_RUNNING_DRIVE": "d", "CLAUDE_LONG_RUNNING_LANE": "lane-a"}
HELD = {ROWS: json.dumps([{"number": 49730, "ready_for_tap": False, "waits_on": "held by root: cutover pick"}])}
READY = {ROWS: json.dumps([{"number": 49730, "ready_for_tap": True, "waits_on": ""}])}
REFUSED = "A line naming a PR as waiting on the owner stands only when its row says `ready_for_tap`. Say what each one waits on instead, from `lr board rows --json`:\n```\n{lines}\n```"
UNREAD = "Warning, nothing was refused: `lr board rows` did not answer, so this line naming a PR as waiting on the owner went unchecked. Read the row before it reaches him."


def flagged(evt: BaseHookEvent) -> list[str]:
    if evt.tool_name == POST_TOOL:
        given = evt._tool_input.get("pr") or ()
        values = [given] if isinstance(given, str) else given
    else:
        values = [value for call in evt.command.calls("cci") for value in CCI_POST.bind(call).values.get("pr", ())]
    return [found[1] for value in values for part in str(value).split(",") if (found := PR_NUMBER.search(part))]


def record_claims(evt: BaseHookEvent) -> list[str]:
    claimed = []
    for _, _, _, text, _, _ in posts(evt):
        named, bare = tap_claims(text)
        claimed += named + (flagged(evt) if bare else [])
    return claimed


def card_claims(evt: BaseHookEvent) -> list[str]:
    if evt.tool_name == POST_TOOL:
        return []
    return [pr for item in published(evt, Reads(evt, []), []) for block, _ in blocks(item.doc) for text in own_text(block) for pr in tap_claims(text)[0]]


@on(
    Event.PreToolUse,
    only_if=[Bound(), Or(Tool(POST_TOOL), And(Tool("Bash"), Or(Runs("cci", "post"), Runs("cc-present"))))],
    skip_if=[Annotated("raw")],
    tests={
        Input(command="cci post --lane a --kind blocked --text '#49730 waits on his tap'", env=ENV, commands=HELD): Block(pattern=r"#49730 is not ready for his tap: held by root: cutover pick"),
        Input(command="cci post --lane a --kind blocked --pr 49730 --text 'this waits on his tap'", env=ENV, commands=HELD): Block(pattern=r"#49730 is not ready"),
        Input(command="cci post --lane a --kind blocked --text '#49731 waits on his tap'", env=ENV, commands=READY): Block(pattern=r"#49731 is not ready for his tap: it has no row"),
        Input(command="cc-present reply --block b --body '#49730 waits on your tap'", env=ENV, commands=HELD): Block(pattern=r"#49730 is not ready"),
        Input(command="cci post --lane a --kind blocked --text '#49730 waits on his tap'", env=ENV, commands=READY): Allow(),
        Input(command="cc-present reply --block b --body '#49730 waits on your tap'", env=ENV, commands=READY): Allow(),
        Input(command="cci post --lane a --kind report --text '#49730 does not wait on his tap'", env=ENV, commands=HELD): Allow(),
        Input(command="cci post --lane a --kind report --text '#49730 is waiting on CI'", env=ENV, commands=HELD): Allow(),
        Input(command="cci post --lane a --kind blocked --text '#49730 waits on his tap'  # ccx:raw", env=ENV, commands=HELD): Allow(),
        Input(command="cci post --lane a --kind blocked --text '#49730 waits on his tap'", env=ENV, commands={ROWS: (1, "store unreadable")}): Warn(pattern=r"^Warning, nothing was refused: `lr board rows`"),
    },
)
def waits_on_him_is_ready(evt: BaseHookEvent) -> HookResult | None:
    if not (prs := list(dict.fromkeys(record_claims(evt) + card_claims(evt)))):
        return None
    drive = bus_name(entry) if (entry := drive_entry(evt)) else reqenv.getenv("CLAUDE_LONG_RUNNING_DRIVE")
    try:
        lines = unready(drive, prs)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return evt.context(UNREAD)
    return evt.block(REFUSED.format(lines="\n".join(lines))) if lines else None
