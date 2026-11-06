from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Tool, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .lane_rotation import ROOT
from .meta_records import posts
from .owner_facing import fenced

COUNTING = re.compile(rf"^(?:{ROOT}|supervisor|grader-appeals(?:-\d+)?)$")
QUOTED = re.compile(r"(?s)```.*?(?:```|\Z)|(?m:^[ \t]*>[^\n]*)|`[^`\n]*`|\"[^\"\n]*\"|“[^”]*”")
LOOKS = re.compile(r"(?:^|(?<=[.!?])\s+)(?:\d{1,2}:\d{2} [AP]M\s+)?(?:(?:root|main):\s+)?((?:finder|fidelity)-override\b|(?:(?:finder|fidelity)\s+)?override\s+#\d+)[^.;\n]*", re.IGNORECASE | re.MULTILINE)
COUNTED = re.compile(r"^(finder|fidelity)-override\s+#?\d+\s+(?:at\s+)?[0-9a-f]{7,40}\b(?:\s*(?::|\s(?:finder|fidelity)\s)\s*#?(\d+(?:@[0-9a-f]+)?)\b)?", re.IGNORECASE)
REFUSAL = (
    "The supervisor counts an override only as `finder-override #<pr> at <sha>: <seq>` or `fidelity-override #<pr> at <sha>: <seq>` opening a clause, and skips any other wording unread."
    " Rewrite each line below in that form and post the decide again."
)
APPEALS = "grader-appeals-3"


def post(text: str, lane: str = APPEALS, kind: str = "decide") -> Input:
    return Input(command=f"cci post --drive d --lane {lane} --kind {kind} --to landing-desk --text '{text}'", env={"CLAUDE_LONG_RUNNING_LANE": lane})


def counted(span: str) -> bool:
    form = COUNTED.match(span)
    return form is not None and (form[1].lower() == "fidelity" or form[2] is not None)


def unparsed(text: str) -> list[str]:
    bare = QUOTED.sub("", text)
    spans = (bare[found.start(1) : found.end()].strip() for found in LOOKS.finditer(bare))
    return [span for span in spans if not counted(span)]


@on(  # capt-hook: no-escape an override worded any other way is skipped by the supervisor with no refusal, and the named form always carries what it means
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", POST_TOOL)],
    tests={
        post("override #51849 finder 536588 at d035cbe (carrying 367205@a090d980 from b92fb9e), its one open row (second fix at directory.rs:384)"): Block(pattern=r"^The supervisor counts an override only as[\s\S]*override #51849 finder 536588 at d035cbe"),
        post("override #51836 finder 535897 at 0a49519, its one open row (the delete-asked stub at routes.go:75-78 answers asked=true)"): Block(pattern=r"override #51836 finder 535897 at 0a49519"),
        post("override #51826 finder 533581 at 7e8f026, its one open row (second fix in this area at codes_table.go:262)"): Block(pattern=r"override #51826 finder 533581 at 7e8f026"),
        post("override #51759 fidelity 522437 at 5328959, row 1 (plan:171: the plan names no ProxyGateway.signal field)"): Block(pattern=r"override #51759 fidelity 522437 at 5328959"),
        post("Looked again. finder-override #9 at e718a74: a memory limit", lane=ROOT): Block(pattern=r"finder-override #9 at e718a74: a memory limit"),
        Input(tool=POST_TOOL, tool_input={"lane": APPEALS, "kind": "decide", "to": ["landing-desk"], "text": "override #51849 finder 536588 at d035cbe"}, env={"CLAUDE_LONG_RUNNING_LANE": APPEALS}): Block(),
        post("finder-override #51849 at d035cbe: 536588. Reposts 367889@a090d980 in the counted form; same grade, head and rows"): Allow(),
        post("fidelity-override #51759 at 5328959: 522437, row 1 (plan:171)"): Allow(),
        post("fidelity-override #9 at e718a74: plan 44 says so", lane=ROOT): Allow(),
        post("finder-override #9 at e718a74 finder #79447@637eacf4: a memory limit", lane=ROOT): Allow(),
        post("the lane asked for `override #51849 finder 536588 at d035cbe` and I refuse", lane=ROOT): Allow(),
        post("no override #51849 finder is owed at this head", lane=ROOT): Allow(),
        post("override #51849 finder 536588 at d035cbe", lane="landing-desk-3"): Allow(),
        post("override #51849 finder 536588 at d035cbe", kind="note"): Allow(),
    },
)
def override_decides_parse(evt: BaseHookEvent) -> HookResult | None:
    found = [span for lane, _, kind, text, _, _ in posts(evt) if COUNTING.match(lane) and "decide" in kind.lower().split(",") for span in unparsed(text)]
    return evt.block(fenced(REFUSAL, found)) if found else None
