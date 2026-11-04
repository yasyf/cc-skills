from __future__ import annotations

import re
from collections.abc import Iterator

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound, CompactionState
from .lane_rotation import DriveActive
from .owner_facing import CCI_POST, posted_text

ACTIVE = [CompactionState(active=True, coordinator=True)]
CLOCK = r"\d{1,2}(?::\d{2})?\s*[AP]\.?M\.?|\d{1,2}:\d{2}"
DEADLINE = re.compile(rf"(?i)\b(?:by|before|until|due(?:\s+(?:by|at))?)\s+(?:{CLOCK})\b")
LEADING_CLOCK = re.compile(rf"(?i)^(?:{CLOCK})\b")
NAMES_LANE = re.compile(r"\blane=[\w.-]+|\b[a-z][a-z0-9]*(?:-[a-z0-9]+)*-\d{1,2}\b")
NAMES_RECORD = re.compile(r"(?<![.\d])\b\d{6,7}\b")
STEP = "step:"
UNCHECKED_KINDS = frozenset({"escalation"})
POST = "cci post --drive d --lane memoryschema-1 --kind report --text 'schema migration lands by 3:10 AM'"
PLAN = "--attr owner=memoryschema-1 --attr 'step:1=2:30 AM open the PR' --attr 'step:2=3:00 AM merge on green'"


def names_deadline(text: str, attrs: dict[str, str]) -> bool:
    return bool(attrs.get("deadline") or DEADLINE.search(text))


def planned(attrs: dict[str, str]) -> bool:
    steps = [step for key, step in attrs.items() if key.startswith(STEP)]
    return bool(attrs.get("owner")) and bool(steps) and all(LEADING_CLOCK.match(step) for step in steps)


def unowned_deadline(text: str) -> bool:
    return bool(DEADLINE.search(text)) and not NAMES_LANE.search(text)


def posts(evt: BaseHookEvent) -> Iterator[tuple[str, set[str], dict[str, str], bool]]:
    if evt.tool_name == POST_TOOL:
        given = evt._tool_input
        yield str(given.get("text") or ""), {str(given.get("kind") or "").lower()}, {str(key): str(value) for key, value in (given.get("attrs") or {}).items()}, bool(given.get("re"))
        return
    for call in evt.command.calls("cci"):
        values = CCI_POST.bind(call).values
        if values.get("words", ())[:1] == ("post",):
            pairs = (pair.partition("=") for value in values.get("attr", ()) for pair in str(value).split(","))
            yield posted_text(values), set(values.get("kind", ())), {key: value for key, _, value in pairs}, bool(values.get("re"))


class PostsUnplannedDeadline:
    def check(self, evt: BaseHookEvent) -> bool:
        return any(not kinds & UNCHECKED_KINDS and not answers and not NAMES_RECORD.search(text) and names_deadline(text, attrs) and not planned(attrs) for text, kinds, attrs, answers in posts(evt))


def mcp(text: str, **attrs: str) -> Input:
    return Input(tool=POST_TOOL, tool_input={"kind": "report", "text": text, "attrs": attrs}, state=ACTIVE)


DEADLINE_PLAN = (
    "Warning, nothing was blocked: A record naming a deadline, a clock time after by, before, until or due, carries its plan."
    " Add `--attr owner=<lane>` and one `--attr 'step:<n>=<clock> <what>'` per step, each opening with its Pacific time, no commas; the cci post tool takes the same keys in `attrs`."
)


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", POST_TOOL), DriveActive(), PostsUnplannedDeadline()],
    tests={
        Input(command=POST, state=ACTIVE): Warn(pattern=r"^Warning, nothing was blocked: A record naming a deadline"),
        Input(command=f"{POST} --attr owner=memoryschema-1", state=ACTIVE): Warn(),
        Input(command=f"{POST} --attr owner=memoryschema-1 --attr 'step:1=2:30 AM open the PR' --attr 'step:2=merge on green'", state=ACTIVE): Warn(),
        Input(command="cci post --drive d --lane lane-a --kind note --attr 'deadline=5:00 AM' --text 'image published'", state=ACTIVE): Warn(),
        mcp("schema migration lands before 4 PM"): Warn(),
        Input(command="cci post --drive d --lane lane-a --kind state --text '#50107 lands by 3:10 AM'", state=ACTIVE): Warn(),
        Input(command=f"{POST} --re 423936", state=ACTIVE): Allow(),
        Input(command="cci post --drive d --lane lane-a --kind state --text 'm630 due 12:15 PM routed 423936'", state=ACTIVE): Allow(),
        Input(tool=POST_TOOL, tool_input={"kind": "report", "text": "schema migration lands before 4 PM", "re": 423936}, state=ACTIVE): Allow(),
        Input(command=f"{POST} {PLAN}", state=ACTIVE): Allow(),
        mcp("schema migration lands before 4 PM", owner="memoryschema-1", **{"step:1": "2:30 AM open the PR"}): Allow(),
        Input(command="cci post --drive d --lane lane-a --kind state --text 'relaunched at 3:10 AM, quiet since 9:45 PM'", state=ACTIVE): Allow(),
        Input(command="cci post --drive d --lane lane-a --kind report --text - <<'EOF'\nlands by 3:10 AM\nEOF", state=ACTIVE): Allow(),
        Input(command="cci post --drive d --lane watch --kind escalation --text 'meta: M35 was due by 11:00 PM'", state=ACTIVE): Allow(),
        Input(command="cci tail --drive d --since 'by 3:10 AM'", state=ACTIVE): Allow(),
        Input(command=POST): Allow(),
    },
)
def deadline_records_carry_their_plan(evt: BaseHookEvent) -> HookResult:
    return evt.context(DEADLINE_PLAN)
