from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from typing import NamedTuple

from captain_hook import Allow, And, BaseHookEvent, Event, HookResult, Input, Or, Runs, Tool, Warn, on
from captain_hook.util import reqenv

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import LR, Bound, bus_name, drive_entry
from .owner_facing import post_values, record_text
from .root_replies import board_rows
from .session_tree import post_lane

LEADS = re.compile(r"^\s*hold #(\d+)\b", re.IGNORECASE)
PR_NUMBER = re.compile(r"(\d+)$")
ROWS = f"{LR} board rows"
ENV = {"CLAUDE_LONG_RUNNING_DRIVE": "d", "CLAUDE_LONG_RUNNING_LANE": "lane-a"}
READY = {ROWS: json.dumps([{"number": 49730, "title": "chats: a long chat opens at its newest turn", "ready_for_tap": True, "tapped": False}])}
HELD = {ROWS: json.dumps([{"number": 49730, "title": "chats: a long chat opens at its newest turn", "ready_for_tap": False, "tapped": False}])}
LEFT = "This hold takes each tap-ready PR below off the owner's board until it lifts or he answers the decide it sends him:\n```\n{lines}\n```"
BREAKS = "breaks=the cutover deletes what it reads"
OWNER = "owner"


class Hold(NamedTuple):
    lane: str
    prs: list[str]
    attrs: dict[str, str]


def held(lane: str, kind: str, text: str, prs: list[str], resolves: bool, attrs: dict[str, str]) -> Iterator[Hold]:
    if kind != "hold" or resolves:
        return
    lead = LEADS.match(text)
    yield Hold(lane, [lead[1]] if lead else [found[1] for value in prs for part in str(value).split(",") if (found := PR_NUMBER.search(part))], attrs)


def hold_posts(evt: BaseHookEvent) -> Iterator[Hold]:
    if evt.tool_name == POST_TOOL:
        given = evt._tool_input
        given_prs = given.get("pr") or ()
        yield from held(str(given.get("lane") or post_lane(evt) or ""), str(given.get("kind") or ""), str(given.get("text") or ""), [given_prs] if isinstance(given_prs, str) else list(given_prs), bool(given.get("resolves")), {str(key): str(value) for key, value in (given.get("attrs") or {}).items()})
        return
    for call in evt.command.calls("cci"):
        values = post_values(evt, call)
        if values.get("words", ())[:1] != ("post",):
            continue
        pairs = (pair.partition("=") for value in values.get("attr", ()) for pair in str(value).split(","))
        yield from held(str((values.get("lane") or (post_lane(evt) or "",))[0]), ",".join(values.get("kind", ())), record_text(values), list(values.get("pr", ())), bool(values.get("resolves")), {key: value for key, _, value in pairs})


def gates(hold: Hold) -> bool:
    return hold.lane == OWNER or bool(hold.attrs.get("breaks")) or bool(hold.attrs.get("ruling"))


def holds(evt: BaseHookEvent) -> Iterator[str]:
    for hold in hold_posts(evt):
        if gates(hold) and not (hold.attrs.get("until") == "tap" and len(hold.prs) == 1):
            yield from hold.prs


@on(
    Event.PreToolUse,
    only_if=[Bound(), Or(Tool(POST_TOOL), And(Tool("Bash"), Runs("cci", "post")))],
    tests={
        Input(command=f"cci post --lane a --kind hold --pr 49730 --attr '{BREAKS}' --text 'Hold until the cutover runs.'", env=ENV, commands=READY): Warn(pattern=r"^This hold takes each tap-ready PR[\s\S]*#49730 chats: a long chat opens at its newest turn"),
        Input(command=f"cci post --lane a --kind hold --to root --attr '{BREAKS}' --text 'hold #49730 until the cutover runs'", env=ENV, commands=READY): Warn(pattern=r"#49730 chats"),
        Input(tool=POST_TOOL, tool_input={"kind": "hold", "pr": ["49730"], "attrs": {"breaks": "the cutover deletes what it reads"}, "text": "Hold until the cutover runs."}, env=ENV, commands=READY): Warn(pattern=r"#49730 chats"),
        Input(command="cci post --lane owner --kind hold --pr 49730 --text 'hold #49730: he said wait for the cutover'", env=ENV, commands=READY): Warn(pattern=r"#49730 chats"),
        Input(command="cci post --lane a --kind hold --pr 49730 --text 'Hold until the cutover runs.'", env=ENV, commands=READY): Allow(),
        Input(command="cci post --lane owner --kind hold --pr 49730 --attr until=tap --text 'hold #49730 until his tap'", env=ENV, commands=READY): Allow(),
        Input(command=f"cci post --lane a --kind hold --pr 49730 --attr '{BREAKS}' --text 'Hold until the cutover runs.'", env=ENV, commands=HELD): Allow(),
        Input(command=f"cci post --lane a --kind hold --pr 49730 --attr '{BREAKS}' --resolves 4120 --text 'hold #49730 lift: the cutover ran'", env=ENV, commands=READY): Allow(),
        Input(command="cci post --lane a --kind report --pr 49730 --text 'hold #49730 is in place'", env=ENV, commands=READY): Allow(),
        Input(command=f"cci post --lane a --kind hold --pr 49730 --attr '{BREAKS}' --text 'Hold until the cutover runs.'", env=ENV, commands={ROWS: (1, "store unreadable")}): Allow(),
    },
)
def holds_say_what_leaves_his_board(evt: BaseHookEvent) -> HookResult | None:
    if not (prs := list(dict.fromkeys(holds(evt)))):
        return None
    drive = bus_name(entry) if (entry := drive_entry(evt)) else reqenv.getenv("CLAUDE_LONG_RUNNING_DRIVE")
    try:
        rows = board_rows(drive)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    left = [f"#{pr} {rows[pr]['title']}" for pr in prs if pr in rows and rows[pr]["ready_for_tap"] and not rows[pr]["tapped"]]
    return evt.context(LEFT.format(lines="\n".join(left))) if left else None
