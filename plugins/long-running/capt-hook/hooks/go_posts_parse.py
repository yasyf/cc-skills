from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Tool, on

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .lane_rotation import ROOT
from .meta_records import Post, post_records
from .owner_facing import fenced

COUNTING = re.compile(rf"^(?:{ROOT}|supervisor)(?:-\d+)?$")
PAIR = r"#\d+\s+at\s+[0-9a-f]{7,40}\b"
CLAUSE = re.compile(rf"(?i)(?:^\s*(?:\d{{1,2}}:\d{{2}}\s*[AP]M\s+)?|[.:;!?]\s+)(?:go(?:\s+(?:port|test-only|ci-only))?|merge)\s+({PAIR}(?:(?:\s*,\s*|\s*,?\s+and\s+){PAIR})*)")
HEAD = re.compile(r"(?i)#(\d+)\s+at\s+([0-9a-f]{7,40})\b")
WITHDRAWN = re.compile(r"(?i)^\s*withdrawn\b")
ORDER = re.compile(r"^\s*(?:\d{1,2}:\d{2}\s*[AP]M\s+)?orca-desk:")
QUOTED = re.compile(r"(?s)```.*?(?:```|\Z)|(?m:^[ \t]*>[^\n]*)|`[^`\n]*`|\"[^\"\n]*\"|“[^”]*”")
NAMED = re.compile(r"(?i)(?P<lead>[^.;!?\n]{0,40}?)(?<![\w./-])go(?![\w./-])(?P<mid>[^.;:\n#]{0,40}?)(?<![\w/-])#(?P<pr>\d+)(?:\s+(?:at\s+)?(?P<sha>[0-9a-f]{7,40})\b)?")
EXEMPT = re.compile(r"(?i)\[exempt:\s*[^\],;\s]+")
CLASS = re.compile(r"(?i)(?<![\w-])(?:test-only|ci-only|ci-tooling|old-tree-port|port|flake-fix|regression|twin|owner-pick|exempt|2c70b68|06288326|eea86c3)(?![\w-])")
CLASS_ATTR = "exempt-class"
REFUSAL = (
    "The landing pass reads a go only from a clause opening `go #<pr> at <sha>`, and an exempt class only from the `exempt-class` attr, so this go moves nothing."
    " Post the line below instead: `lr landing go` gates the class at the head and sets the attr, and a tapped go opens its text with the pair."
)
WRONG = "ci-only go (2c70b68) #51409 at 1046a9740fc78b2179196ab68ea0ec31c41b86fe: root read every file: Buildkite scripts, infra/ci sand.ts and its test. No product source."


def post(text: str, lane: str = ROOT, kind: str = "go", flags: str = "--pr 51409") -> Input:
    return Input(command=f"cci post --drive d --lane {lane} --kind {kind} --to landing-desk {flags} --text '{text}'", env={"CLAUDE_LONG_RUNNING_LANE": lane})


def counted(text: str) -> dict[int, str]:
    if WITHDRAWN.match(text) or not (clause := CLAUSE.search(text)):
        return {}
    return {int(pr): sha.lower() for pr, sha in HEAD.findall(clause[1])}


def fix(record: Post) -> str | None:
    if not COUNTING.match(record.lane) or "go" not in record.kind.lower().split(",") or WITHDRAWN.match(record.said) or ORDER.match(record.said):
        return None
    bare = QUOTED.sub("", record.said)
    named = [found for found in NAMED.finditer(bare) if not record.prs or int(found["pr"]) in record.prs]
    read = counted(record.said)
    prs = list(dict.fromkeys([*read, *(int(found["pr"]) for found in named)]))
    hand = not record.attrs.get(CLASS_ATTR) and (EXEMPT.search(bare) is not None or any(CLASS.search(found["lead"] + found["mid"]) for found in named))
    unread = [found for found in named if int(found["pr"]) not in read]
    if not prs or not (hand or unread):
        return None
    if hand or record.attrs.get(CLASS_ATTR):
        return f"lr landing go {' '.join(map(str, prs))} --lane {record.lane}"
    heads = read | {int(found["pr"]): (found["sha"] or "<head sha>").lower() for found in unread}
    return "go " + ", ".join(f"#{pr} at {heads[pr]}" for pr in prs)


@on(  # capt-hook: no-escape a go worded any other way moves nothing on the landing pass, and the named form always carries what it means
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", POST_TOOL)],
    tests={
        post(WRONG): Block(pattern=r"^The landing pass reads a go only from a clause opening[\s\S]*```\nlr landing go 51409 --lane root\n```"),
        Input(tool=POST_TOOL, tool_input={"lane": ROOT, "kind": "go", "to": ["landing-desk"], "pr": ["51409"], "text": WRONG}, env={"CLAUDE_LONG_RUNNING_LANE": ROOT}): Block(pattern=r"lr landing go 51409 --lane root"),
        post("incident go #51665 at 86cfe00b246910ce272fe1260a91c90e2107d3fd: the sentry fix, old tree only.", flags="--pr 51665"): Block(pattern=r"\ngo #51665 at 86cfe00b246910ce272fe1260a91c90e2107d3fd\n"),
        post("2:09 PM go #45992 on green (release-gate forward fix).", flags="--pr 45992"): Block(pattern=r"\ngo #45992 at <head sha>\n"),
        post("go #47986 on green [exempt: ci-only]", flags="--pr 47986"): Block(pattern=r"lr landing go 47986 --lane root"),
        post("6:44 AM go #49020 at 31a9af6 on green [exempt: ci-tooling]. The relay join fix.", flags="--pr 49020"): Block(pattern=r"lr landing go 49020 --lane root"),
        post("go #42706 at 00dfa16 (restack onto dev). go #42732 at 0fb1128 (on #42706).", flags="--pr 42706,42732"): Block(pattern=r"\ngo #42706 at 00dfa16, #42732 at 0fb1128\n"),
        Input(command="lr landing go 51409 --lane root", env={"CLAUDE_LONG_RUNNING_LANE": ROOT}): Allow(),
        post("go #51409 at 1046a97 on green [exempt: ci-only]: fidelity ok #545352, finder ok #545404", flags="--pr 51409 --attr exempt-class=ci-only"): Allow(),
        post("go #51409 at 1046a97: his 9:16 AM tap on card-tap-51409-1046a97"): Allow(),
        post("go #42743 at 74f214c (carry: patch-identical replay onto #42742 8f7c249, go #139774 carries)", flags="--pr 42743"): Allow(),
        post("orca-desk: relay to dd-repair-1: hold lifted, go posted for #50026 at f1b746b.", flags=""): Allow(),
        post("withdrawn: go #131499 for #42767 at 8986348 (finder majors there).", flags="--pr 42767"): Allow(),
        post("go narrow. Start the executor-only release now at 9d80c2cb7a77 or later.", flags="--pr 50866"): Allow(),
        post("8:53 AM go yasyf/cc-skills#1584 at ef1e133 on green [exempt: ci-tooling]", flags=""): Allow(),
        post("the lane read `ci-only go #51409 at 1046a97` as nothing"): Allow(),
        post(WRONG, lane="old-chat-convert-5"): Allow(),
        post(WRONG, kind="note"): Allow(),
    },
)
def go_posts_parse(evt: BaseHookEvent) -> HookResult | None:
    found = [line for record in post_records(evt) if (line := fix(record))]
    return evt.block(fenced(REFUSAL, found)) if found else None
