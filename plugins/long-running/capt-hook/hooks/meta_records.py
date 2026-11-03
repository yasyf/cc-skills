from __future__ import annotations

import re
from collections.abc import Iterator

from captain_hook import Allow, BaseHookEvent, Block, Event, Input, Tool, hook

from .cci_mcp_posts import POST_TOOL
from .lane_rotation import ROOT, route, routed
from .owner_facing import CCI_POST, record_text
from .session_tree import drive_lane
from .tests.register_fixtures import DRIVE_CONFIG

ITEM = re.compile(r"(?i)\bm\d+\b")
META = "meta-desk-2"
IS_META = route(META, META, "desired", meta=True)
REPORT = f"cci post --drive d --lane {META} --kind report --to root --text 'hook-gate-1 merged #2201, not installed'"
DECIDE = f"cci post --drive d --lane {META} --kind decide --to root --text 'stand down conflicts-2: its brief is superseded'"


def posts(evt: BaseHookEvent) -> Iterator[tuple[str, list[str], str, str, bool]]:
    """Each cci post in `evt` as its writing lane, recipients, kind, text, and whether that text is one inline line."""
    if evt.tool_name == POST_TOOL:
        given = evt._tool_input
        lane = str(given.get("lane") or drive_lane(evt) or "")
        for record in [given, *(given.get("records") or ())]:
            text = str(record.get("text") or "")
            yield str(record.get("lane") or lane), [str(to) for to in record.get("to") or ()], str(record.get("kind") or ""), text, not record.get("path") and "\n" not in text
        return
    for call in evt.command.calls("cci"):
        values = CCI_POST.bind(call).values
        if values.get("words", ())[:1] != ("post",):
            continue
        text = record_text(values)
        inline = not (values.get("path") or values.get("body-file") or "-" in values.get("text", ())) and "\n" not in text
        recipients = [to for value in values.get("to", ()) for to in str(value).split(",")]
        yield str((values.get("lane") or (drive_lane(evt) or "",))[0]), recipients, ",".join(values.get("kind", ())), text, inline


def one_decide_line(kind: str, text: str, inline: bool) -> bool:
    return kind == "decide" and inline and not ITEM.search(text)


class MetaRecordToRoot:
    def check(self, evt: BaseHookEvent) -> bool:
        return any(
            ROOT in (to.split("@", 1)[0] for to in recipients) and not one_decide_line(kind, text, inline) and (found := routed(evt, lane)) is not None and found.get("meta")
            for lane, recipients, kind, text, inline in posts(evt)
        )


def mcp(lane: str, kind: str, text: str, to: list[str]) -> Input:
    return Input(tool=POST_TOOL, tool_input={"lane": lane, "kind": kind, "to": to, "text": text}, env=DRIVE_CONFIG, commands=IS_META)


hook(  # capt-hook: no-escape the owner ruled that meta records reach root only as the one decide line the message names
    Event.PreToolUse,
    only_if=[Tool("Bash", POST_TOOL), MetaRecordToRoot()],
    block=True,
    message=(
        "A meta lane's record never reaches root, which reads drive work alone."
        " Send it to `owner-relay` or your meta desk, or, when root must act on a drive lane, post that act alone as one `--kind decide` line to root naming the lane, with no item number or history."
    ),
    tests={
        Input(command=REPORT, env=DRIVE_CONFIG, commands=IS_META): Block(pattern=r"^A meta lane's record never reaches root[\s\S]*`owner-relay`[\s\S]*one `--kind decide` line"),
        Input(command=REPORT.replace("--to root", "--to owner-relay,root@brook-drive"), env=DRIVE_CONFIG, commands=IS_META): Block(),
        Input(command=REPORT.replace(f"--lane {META} ", ""), env=DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": META}, commands=IS_META): Block(),
        Input(command=DECIDE.replace("stand down", "M35: stand down"), env=DRIVE_CONFIG, commands=IS_META): Block(),
        Input(command=f"cci post --drive d --lane {META} --kind decide --to root --text - <<'EOF'\nstand down conflicts-2\nwhy: M35\nEOF", env=DRIVE_CONFIG, commands=IS_META): Block(),
        mcp(META, "report", "hook-gate-1 merged #2201", ["root"]): Block(),
        Input(tool=POST_TOOL, tool_input={"lane": META, "records": [{"kind": "report", "to": ["owner-relay"], "text": "x"}, {"kind": "report", "to": ["root"], "text": "y"}]}, env=DRIVE_CONFIG, commands=IS_META): Block(),
        Input(command=DECIDE, env=DRIVE_CONFIG, commands=IS_META): Allow(),
        mcp(META, "decide", "relaunch conflicts-2 from its handoff", ["root"]): Allow(),
        Input(command=REPORT.replace("--to root", "--to owner-relay"), env=DRIVE_CONFIG, commands=IS_META): Allow(),
        Input(command=REPORT.replace(META, "conflicts-2"), env=DRIVE_CONFIG, commands=route("conflicts-2", "conflicts-2", "desired")): Allow(),
        Input(command=REPORT): Allow(),
    },
)
