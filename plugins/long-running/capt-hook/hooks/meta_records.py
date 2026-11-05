from __future__ import annotations

import re
from collections.abc import Iterator
from typing import NamedTuple

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .lane_rotation import ROOT, route, routed
from .owner_facing import post_values, record_text
from .session_tree import drive_lane
from .tests.register_fixtures import DRIVE_CONFIG

ITEM = re.compile(r"(?i)\bm\d+\b")
META = "meta-desk-2"
IS_META = route(META, META, "desired", meta=True)
REPORT = f"cci post --drive d --lane {META} --kind report --to root --text 'hook-gate-1 merged #2201, not installed'"
DECIDE = f"cci post --drive d --lane {META} --kind decide --to root --text 'stand down conflicts-2: its brief is superseded'"
DONE = f"cci post --drive d --lane {META} --kind done --to owner-relay --text 'hook-gate-1 is finished'"
INSTALLED = "--attr installed=0.7.1198 --attr proof=saw-it-refuse"


class Post(NamedTuple):
    lane: str
    recipients: list[str]
    kind: str
    text: str
    inline: bool
    attrs: dict[str, str]
    topic: str
    heads: tuple[str, ...] = ()


def post_records(evt: BaseHookEvent) -> Iterator[Post]:
    if evt.tool_name == POST_TOOL:
        given = evt._tool_input
        lane = str(given.get("lane") or drive_lane(evt) or "")
        for record in [given, *(given.get("records") or ())]:
            text = str(record.get("text") or "")
            yield Post(str(record.get("lane") or lane), [str(to) for to in record.get("to") or ()], str(record.get("kind") or ""), text, not record.get("path") and "\n" not in text, {str(key): str(value) for key, value in (record.get("attrs") or given.get("attrs") or {}).items()}, str(record.get("topic") or ""))
        return
    for call in evt.command.calls("cci"):
        values = post_values(evt, call)
        if values.get("words", ())[:1] != ("post",):
            continue
        text = record_text(values)
        inline = not (values.get("path") or values.get("body-file") or "-" in values.get("text", ())) and "\n" not in text
        recipients = [to for value in values.get("to", ()) for to in str(value).split(",")]
        pairs = (pair.partition("=") for value in values.get("attr", ()) for pair in str(value).split(","))
        yield Post(str((values.get("lane") or (drive_lane(evt) or "",))[0]), recipients, ",".join(values.get("kind", ())), text, inline, {key: value for key, _, value in pairs}, str((values.get("topic") or ("",))[0]), tuple(str(head) for head in values.get("head", ())))


def posts(evt: BaseHookEvent) -> Iterator[tuple[str, list[str], str, str, bool, dict[str, str]]]:
    """Each cci post in `evt` as its writing lane, recipients, kind, text, whether that text is one inline line, and its attributes."""
    for post in post_records(evt):
        yield tuple(post[:6])


def one_decide_line(kind: str, text: str, inline: bool) -> bool:
    return kind == "decide" and inline and not ITEM.search(text)


class MetaRecordToRoot:
    def check(self, evt: BaseHookEvent) -> bool:
        return any(
            ROOT in (to.split("@", 1)[0] for to in recipients) and not one_decide_line(kind, text, inline) and (found := routed(evt, lane)) is not None and found.get("meta")
            for lane, recipients, kind, text, inline, _ in posts(evt)
        )


class MetaDoneOwesItsInstall:
    def check(self, evt: BaseHookEvent) -> bool:
        return any(
            kind.lower() == "done" and not (attrs.get("installed") and attrs.get("proof")) and (found := routed(evt, lane)) is not None and found.get("meta")
            for lane, _, kind, _, _, attrs in posts(evt)
        )


def mcp(lane: str, kind: str, text: str, to: list[str]) -> Input:
    return Input(tool=POST_TOOL, tool_input={"lane": lane, "kind": kind, "to": to, "text": text}, env=DRIVE_CONFIG, commands=IS_META)


META_TO_ROOT = (
    "Warning, nothing was blocked: A meta lane's record never reaches root, which reads drive work alone."
    " Send it to `owner-relay` or your meta desk, or, when root must act on a drive lane, post that act alone as one `--kind decide` line to root naming the lane, with no item number or history."
)


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", POST_TOOL), MetaRecordToRoot()],
    tests={
        Input(command=REPORT, env=DRIVE_CONFIG, commands=IS_META): Warn(pattern=r"^Warning, nothing was blocked: A meta lane's record never reaches root[\s\S]*`owner-relay`[\s\S]*one `--kind decide` line"),
        Input(command=REPORT.replace("--to root", "--to owner-relay,root@brook-drive"), env=DRIVE_CONFIG, commands=IS_META): Warn(),
        Input(command=REPORT.replace(f"--lane {META} ", ""), env=DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": META}, commands=IS_META): Warn(),
        Input(command=DECIDE.replace("stand down", "M35: stand down"), env=DRIVE_CONFIG, commands=IS_META): Warn(),
        Input(command=f"cci post --drive d --lane {META} --kind decide --to root --text - <<'EOF'\nstand down conflicts-2\nwhy: M35\nEOF", env=DRIVE_CONFIG, commands=IS_META): Warn(),
        mcp(META, "report", "hook-gate-1 merged #2201", ["root"]): Warn(),
        Input(tool=POST_TOOL, tool_input={"lane": META, "records": [{"kind": "report", "to": ["owner-relay"], "text": "x"}, {"kind": "report", "to": ["root"], "text": "y"}]}, env=DRIVE_CONFIG, commands=IS_META): Warn(),
        Input(command=DECIDE, env=DRIVE_CONFIG, commands=IS_META): Allow(),
        mcp(META, "decide", "relaunch conflicts-2 from its handoff", ["root"]): Allow(),
        Input(command=REPORT.replace("--to root", "--to owner-relay"), env=DRIVE_CONFIG, commands=IS_META): Allow(),
        Input(command=REPORT.replace(META, "conflicts-2"), env=DRIVE_CONFIG, commands=route("conflicts-2", "conflicts-2", "desired")): Allow(),
        Input(command=REPORT): Allow(),
    },
)
def meta_records_skip_root(evt: BaseHookEvent) -> HookResult:
    return evt.context(META_TO_ROOT)


META_DONE = (
    "Warning, nothing was blocked: A meta lane's `--kind done` names what is running: add `--attr installed=<version> --attr proof=<what you saw working>`."
    " A lane that merged with no install posts `--kind landed --pr <n>` instead, then `--kind blocked --lane-ref <its meta desk>`."
)


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", POST_TOOL), MetaDoneOwesItsInstall()],
    tests={
        Input(command=DONE, env=DRIVE_CONFIG, commands=IS_META): Warn(pattern=r"^Warning, nothing was blocked: A meta lane's `--kind done`[\s\S]*`--attr installed=[\s\S]*`--kind landed --pr <n>`"),
        Input(command=f"{DONE} --attr installed=0.7.1198", env=DRIVE_CONFIG, commands=IS_META): Warn(),
        Input(command=f"{DONE} --attr proof=saw-it", env=DRIVE_CONFIG, commands=IS_META): Warn(),
        Input(command=DONE.replace(f"--lane {META} ", ""), env=DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": META}, commands=IS_META): Warn(),
        mcp(META, "done", "hook-gate-1 is finished", ["owner-relay"]): Warn(),
        Input(command=f"{DONE} {INSTALLED}", env=DRIVE_CONFIG, commands=IS_META): Allow(),
        Input(tool=POST_TOOL, tool_input={"lane": META, "kind": "done", "to": ["owner-relay"], "text": "x", "attrs": {"installed": "0.7.1198", "proof": "saw it"}}, env=DRIVE_CONFIG, commands=IS_META): Allow(),
        Input(command=f"cci post --drive d --lane {META} --kind landed --pr yasyf/cc-skills#2201 --to owner-relay --text 'merged'", env=DRIVE_CONFIG, commands=IS_META): Allow(),
        Input(command=DONE.replace(META, "conflicts-2"), env=DRIVE_CONFIG, commands=route("conflicts-2", "conflicts-2", "desired")): Allow(),
        Input(command=DONE): Allow(),
    },
)
def meta_done_names_its_install(evt: BaseHookEvent) -> HookResult:
    return evt.context(META_DONE)
