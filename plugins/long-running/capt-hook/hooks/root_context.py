from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from captain_hook import (
    Allow,
    Annotated,
    Arguments,
    BaseHookEvent,
    Block,
    CommandMatches,
    CommandSchema,
    Confirm,
    Event,
    FileFixture,
    FromSubagent,
    GrepCall,
    HookResult,
    Input,
    Operand,
    Option,
    Or,
    Prompt,
    ReadCall,
    Tool,
    Warn,
    WorkflowState,
    hook,
    on,
    workflow_state,
)
from captain_hook.cmd import Call, Target
from captain_hook.command_schema import Scalar
from captain_hook.util import reqenv
from pydantic import BaseModel

from .compaction_handoff import CompactionState, ccn, progress_folder
from .nudges import queue_nudge
from .tests.root_fixtures import ACTIVE, LONG

READ_LINES = 150
MCP_CHARS = 8000
ARTIFACT_DIRS = "audits,briefs,handoffs,tool-results,subagents,transcripts"
ARTIFACT_FILES = frozenset({"matrix.md"})
ARTIFACT_EXTENSIONS = frozenset({"jsonl"})
READ_DEFAULT_LIMIT = 2000
FILE_READERS = frozenset({"cat", "head", "tail", "sed", "awk", "grep", "egrep", "fgrep", "less", "more", "bat", "jq", "yq"})
SEARCHERS = frozenset({"rg", "ag", "ack"})
GREPS = frozenset({"grep", "egrep", "fgrep"})
BOUNDED_BYTES = 4000
BOUNDED_LINES = 40
BOUNDED_MATCHES = 20
SCRIPT_FIRST = SEARCHERS | {"grep", "egrep", "fgrep", "sed", "awk", "jq", "yq"}
SCRIPT_FLAGS = frozenset({"-e", "-f", "--regexp", "--file", "--expression"})
SLACK_READ_TOOLS = (
    "mcp__slack__slack_get_thread",
    "mcp__slack__slack_conversations_history",
    "mcp__slack__slack_get_full_conversation",
    "mcp__slack__slack_catch_me_up",
    "mcp__slack__slack_search_messages",
    "mcp__plugin_cc-slack_cc-slack__slack_thread",
)
SLACK_WRITE_TOOLS = (
    "mcp__plugin_cc-slack_cc-slack__slack_send",
    "mcp__plugin_cc-slack_cc-slack__slack_reply",
    "mcp__plugin_cc-slack_cc-slack__slack_edit",
    "mcp__plugin_cc-slack_cc-slack__slack_react",
    "mcp__plugin_cc-slack_cc-slack__slack_unreact",
    "mcp__slack__slack_send_message",
    "mcp__slack__slack_add_reaction",
    "mcp__slack__slack_remove_reaction",
)
CCX_MCP_READS = tuple(
    f"mcp__plugin_cc-context_cc-context__ccx_{name}" for name in ("code_", "repo_", "web_", "vcs_diff", "exec")
)
DOC_TOOLS = (
    "mcp__claude_ai_Claude_Docs__read",
    "mcp__claude_ai_Claude_Docs__export",
    "mcp__plugin_datadog_mcp__get_datadog_notebook",
    "mcp__linear__get_document",
    "mcp__claude_ai_Capacities__getObjectContent",
    "mcp__claude_ai_Capacities__readObjectBlocks",
)
MCP_EXEMPT = ("mcp__plugin_cc-notes_", "mcp__plugin_cc-present_", "mcp__plugin_codex_")
ANSWER_TOOLS = ("mcp__plugin_cc-notes_cc-notes__answer_add", "mcp__plugin_cc-notes_cc-notes__answer_edit")
STANDING = r"\b(?:from now on|always|never|I told you|the plan is)\b"
SYSTEM_PREFIXES = ("<", "/", "This session is being continued", "[Request interrupted")
RULING_LABELS = ("scope:durable", "owner-ruling")
PACIFIC = ZoneInfo("America/Los_Angeles")
RULING_SYSTEM = (
    "The owner of a multi-agent coding drive just sent the orchestrator a message. When the message states a "
    "standing rule (a preference, policy, or decision meant to hold beyond the current task), return in title the "
    "one question that rule answers, phrased as an agent would ask it before acting, under 120 characters, ending "
    "with a question mark. Example: the message 'from now on, release everything as it merges' gives the title "
    "'When does a merged PR get released?'. When the message states no standing rule, return an empty title."
)
ROOT_READ = Confirm(
    rule=(
        "An investigation read at the drive root: reading repo code, history, lane output, or Slack to work something "
        "out floods the root's context and belongs to a lane. One-shot control-plane reads are outside the rule: a "
        "bounded read of a state, inbox, or mailbox file, and a single Slack thread read during an incident turn "
        "before any lane exists."
    )
)

WORDS = (Operand("words", count="*"),)
REPO_OPTION = Option("repo", ("-R", "--repo"))
GIT = CommandSchema(
    "git",
    operands=WORDS,
    options=(
        Option("dir", ("-C",)),
        Option("config", ("-c",)),
        Option("git_dir", ("--git-dir",)),
        Option("work_tree", ("--work-tree",)),
        Option("namespace", ("--namespace",)),
        Option(
            "global_flag",
            (
                "--no-pager",
                "-P",
                "-p",
                "--paginate",
                "--bare",
                "--no-replace-objects",
                "--literal-pathspecs",
                "--glob-pathspecs",
                "--noglob-pathspecs",
                "--icase-pathspecs",
                "--no-optional-locks",
                "--no-advice",
            ),
            bool,
        ),
    ),
)
GH = CommandSchema("gh", operands=WORDS, options=(REPO_OPTION,))
CCX = CommandSchema("ccx", operands=WORDS)
CC_SLACK = CommandSchema("cc-slack", operands=WORDS)
CCN = CommandSchema("ccn", operands=WORDS, options=(REPO_OPTION,))
CC_NOTES = CommandSchema("cc-notes", operands=WORDS, options=(REPO_OPTION,))
ANSWER_WRITES = (("answer", "add"), ("answer", "edit"))
HEAD = CommandSchema(
    "head", operands=WORDS, options=(Option("bytes", ("-c", "--bytes")), Option("lines", ("-n", "--lines")))
)
TAIL = CommandSchema("tail", operands=WORDS, options=(Option("lines", ("-n", "--lines")),))
WC = CommandSchema(
    "wc",
    operands=WORDS,
    options=tuple(Option(name, flags, bool) for name, flags in (("l", ("-l",)), ("w", ("-w",)), ("c", ("-c",)), ("m", ("-m",)))),
)
CAT = CommandSchema("cat", operands=WORDS)
GREP = CommandSchema(
    "grep",
    operands=WORDS,
    options=(
        Option("count", ("-c", "--count"), bool),
        Option("max", ("-m", "--max-count")),
        Option("pattern", ("-e", "--regexp")),
        *(
            Option(flag, (flag,), bool)
            for flag in ("-n", "-i", "-E", "-F", "-G", "-P", "-w", "-x", "-v", "-l", "-H", "-h", "-s", "-a")
        ),
    ),
)
LIST_OPERATORS = frozenset({"|", ";", "&&", "||"})
REDIRECT_HEADS = frozenset("<>&0123456789")
READ_SCHEMAS = {"head": HEAD, "tail": TAIL, "wc": WC, "cat": CAT} | dict.fromkeys(GREPS, GREP)


@workflow_state("long_running_root_context")
class RootContextState(WorkflowState):
    oversized: dict[str, int] = {}
    rule_pending: bool = False
    rule_recorded: bool = False


def verb(*prefixes: tuple[str, ...]) -> Callable[[Arguments], bool]:
    def matches(arguments: Arguments) -> bool:
        words = arguments.values.get("words", ())
        return any(words[: len(prefix)] == prefix for prefix in prefixes)

    return matches


def runs_verb(schema: CommandSchema, *prefixes: tuple[str, ...]) -> CommandMatches:
    return CommandMatches(schema, only_if=(verb(*prefixes),))


class DriveActive:
    def check(self, evt: BaseHookEvent) -> bool:
        return CompactionState.load(evt).active


class RulingTitle(BaseModel):
    title: str = ""


class OwnerPrompt:
    def check(self, evt: BaseHookEvent) -> bool:
        text = (evt.user_prompt or "").strip()
        return not text.startswith(SYSTEM_PREFIXES) and "<teammate-message" not in text


class ToolStartsWith:
    def __init__(self, *prefixes: str) -> None:
        self.prefixes = prefixes

    def check(self, evt: BaseHookEvent) -> bool:
        return (evt.tool_name or "").startswith(self.prefixes)


class OversizedTool:
    def check(self, evt: BaseHookEvent) -> bool:
        return (evt.tool_name or "") in RootContextState.load(evt).oversized


def setting(name: str, default: int) -> int:
    return int(reqenv.getenv(f"LONG_RUNNING_ROOT_{name}", default))


def artifact_dirs() -> frozenset[str]:
    return frozenset(reqenv.getenv("LONG_RUNNING_ROOT_ARTIFACT_DIRS", ARTIFACT_DIRS).split(","))


def drive_plan(evt: BaseHookEvent) -> Path | None:
    plan = CompactionState.load(evt).plan_path
    return Path(plan).expanduser() if plan else None


def pinned(path: Path, plan: Path | None) -> bool:
    return plan is not None and (path == plan or path.is_relative_to(progress_folder(plan)))


def exempt(path: Path, plan: Path | None) -> bool:
    return pinned(path, plan) or "inbox" in path.parts


def is_artifact(path: Path) -> bool:
    return bool(artifact_dirs() & set(path.parts)) or path.name in ARTIFACT_FILES or path.suffix.removeprefix(".") in ARTIFACT_EXTENSIONS


def line_count(path: Path) -> int:
    with path.open("rb") as file:
        return sum(chunk.count(b"\n") for chunk in iter(lambda: file.read(1 << 20), b""))


def candidates(target: Target) -> Iterator[Path]:
    if not target.has_glob:
        yield from filter(None, [target.path])
        return
    yield from (target.cwd / match if target.cwd else Path(match) for match in target.expand())


def operand_targets(call: Call) -> list[Target]:
    targets = list(call.targets)
    if call.name in SCRIPT_FIRST and not any(flag.split("=")[0] in SCRIPT_FLAGS for flag in call.flags):
        return targets[1:]
    return targets


def paths(call: Call) -> list[Path]:
    return [path for target in operand_targets(call) for path in candidates(target) if path.exists()]


def read_target(evt: BaseHookEvent) -> Path | None:
    read = evt.as_input(ReadCall)
    if read is None:
        return None
    path = Path(read.file_path).expanduser()
    if not path.is_file() or not os.access(path, os.R_OK) or pinned(path, drive_plan(evt)):
        return None
    return path


class ReadsLaneArtifact:
    def check(self, evt: BaseHookEvent) -> bool:
        path = read_target(evt)
        return path is not None and is_artifact(path) and "inbox" not in path.parts


class ReadsOversizeWindow:
    def check(self, evt: BaseHookEvent) -> bool:
        path = read_target(evt)
        if path is None or (is_artifact(path) and "inbox" not in path.parts):
            return False
        read = evt.as_input(ReadCall)
        window = min(read.limit or READ_DEFAULT_LIMIT, line_count(path) - max((read.offset or 1) - 1, 0))
        return window > setting("READ_LINES", READ_LINES)


class SearchesRepo:
    def check(self, evt: BaseHookEvent) -> bool:
        plan = drive_plan(evt)
        if grep := evt.as_input(GrepCall):
            root = Path(grep.path).expanduser() if grep.path else None
            return not (root and exempt(root, plan))
        for call in evt.command.calls():
            if call.name in SEARCHERS:
                found = paths(call)
                if not found or not all(exempt(path, plan) for path in found):
                    return True
        return False


def within(values: tuple[Scalar | None, ...], limit: int) -> bool:
    return all(isinstance(value, str) and value.isdigit() and int(value) <= limit for value in values)


def caps_output(name: str, values: dict[str, tuple[Scalar | None, ...]]) -> bool:
    match name:
        case "head":
            return within(values.get("bytes", ()), BOUNDED_BYTES) and within(values.get("lines", ()), BOUNDED_LINES)
        case "tail":
            return within(values.get("lines", ()), BOUNDED_LINES)
        case "wc":
            return True
        case "grep" | "egrep" | "fgrep":
            return bool(values.get("count")) or (bool(values.get("max")) and within(values["max"], BOUNDED_MATCHES))
    return False


def bound(call: Call) -> Arguments | None:
    schema = READ_SCHEMAS.get(call.name)
    arguments = schema.bind(call) if schema else None
    return arguments if arguments and arguments.complete else None


def operands(call: Call, arguments: Arguments) -> tuple[Target, ...]:
    targets = arguments.paths("words").targets
    return targets[1:] if call.name in GREPS and "pattern" not in arguments.values else targets


def simple_list(raw: str, calls: Sequence[Call]) -> bool:
    glue = list(raw)
    for call in calls:
        if call.source.span is None or call.nested:
            return False
        start, end = call.source.span
        glue[start:end] = " " * (end - start)
    words = "".join(glue).split()
    if "#" in (heads := [word[0] for word in words]):
        words = words[: heads.index("#")]
    return all(word in LIST_OPERATORS or word[0] in REDIRECT_HEADS for word in words)


def keeps_stdout(call: Call) -> bool:
    return all(redirect.fd == 2 or redirect.op.startswith("<") for redirect in call.redirects)


def capped_by_pipe(call: Call, calls: Sequence[Call]) -> bool:
    end = next((each for each in calls if each.occurrence.index == call.occurrence.index + 1), None)
    if end is None or call.occurrence.next_op != "|" or end.occurrence.next_op == "|" or not keeps_stdout(end):
        return False
    arguments = bound(end)
    return arguments is not None and not operands(end, arguments) and caps_output(end.name, arguments.values)


def bounded_read(call: Call, calls: Sequence[Call], raw: str) -> bool:
    if not simple_list(raw, calls) or not keeps_stdout(call) or (arguments := bound(call)) is None:
        return False
    match operands(call, arguments):
        case [target] if not target.has_glob and target.path is not None and target.path.is_file():
            return caps_output(call.name, arguments.values) or capped_by_pipe(call, calls)
    return False


class ReadsRepoFiles:
    def check(self, evt: BaseHookEvent) -> bool:
        plan = drive_plan(evt)
        calls = evt.command.calls()
        reads = [
            call
            for call in calls
            if call.name in FILE_READERS
            and not (call.name == "sed" and "-i" in call.flags)
            and not all(exempt(path, plan) for path in paths(call))
        ]
        return bool(reads) and not (len(reads) == 1 and bounded_read(reads[0], calls, evt.command.raw))


def inbox_file(path: Path) -> bool:
    return "inbox" in path.parts and path.suffix == ".md"


def counts(call: Call) -> bool:
    arguments = bound(call)
    return call.name in GREPS and arguments is not None and bool(arguments.values.get("count"))


class ReadsInbox:
    def check(self, evt: BaseHookEvent) -> bool:
        if read := evt.as_input(ReadCall):
            return inbox_file(Path(read.file_path))
        return any(
            call.name in FILE_READERS
            and not counts(call)
            and any(target.path is not None and inbox_file(target.path) for target in operand_targets(call))
            for call in evt.command.calls()
        )


def root_block(
    *, message: str, only_if: Sequence[object], tests: dict, bypass: bool = True, confirm: Confirm | None = None
) -> None:
    hook(
        Event.PreToolUse,
        message=message,
        only_if=[DriveActive(), *only_if],
        skip_if=[FromSubagent(), *([Annotated("raw")] if bypass else [])],
        block=True,
        confirm=confirm,
        tests=tests,
    )


root_block(
    message="Drive roots read inboxes through `inbox-digest.py --state <drive>/inbox/.inbox-digest.json <files>`.",
    only_if=[ReadsInbox()],
    tests={
        Input(command="tail -n 40 ~/.claude/scratch/drive/inbox/deploy-go.md", state=ACTIVE): Block(pattern=r"inbox-digest"),
        Input(command="grep -n G12 /drive/inbox/deploy-go.md", state=ACTIVE): Block(),
        Input(command="sed -n '1,400p' /drive/inbox/runner.md", state=ACTIVE): Block(),
        Input(command="cat /drive/inbox/*.md", state=ACTIVE): Block(),
        Input(tool="Read", tool_input={"file_path": "/drive/inbox/runner.md"}, state=ACTIVE): Block(),
        Input(command="grep -c G12 /drive/inbox/deploy-go.md", state=ACTIVE): Allow(),
        Input(command="wc -l /drive/inbox/deploy-go.md", state=ACTIVE): Allow(),
        Input(command="cat >> /drive/inbox/orca-desk.md <<'EOF'\nR9 orca-desk: relay to a: go\nEOF", state=ACTIVE): Allow(),
        Input(command="inbox-digest.py --state /drive/inbox/.inbox-digest.json /drive/inbox/deploy-go.md", state=ACTIVE): Allow(),
        Input(command="tail -n 40 /drive/inbox/deploy-go.md", state=ACTIVE): Block(),
        Input(command="tail -n 40 /drive/inbox/deploy-go.md"): Allow(),
        Input(command="tail -n 40 /drive/inbox/deploy-go.md", agent_id="a1b2c3", state=ACTIVE): Allow(),
        Input(command="tail -n 40 /drive/inbox/deploy-go.md # ccx:raw", state=ACTIVE): Allow(),
    },
)

root_block(
    message="Lane artifacts are read by a lane, not the drive root. Delegate with `Agent` using `Explore` and `model: sonnet`.",
    only_if=[ReadsLaneArtifact()],
    tests={
        Input(tool="Read", file=FileFixture(name="matrix.md", content="a\n"), state=ACTIVE): Block(
            pattern=r"Lane artifacts"
        ),
        Input(tool="Read", file=FileFixture(name="notes.md", content="a\n"), state=ACTIVE): Allow(),
        Input(tool="Read", file=FileFixture(name="matrix.md", content="a\n")): Allow(),
    },
)

root_block(
    message="A read this large belongs to a lane, not the drive root. Read a window with `offset` and `limit`.",
    only_if=[ReadsOversizeWindow()],
    tests={
        Input(tool="Read", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Block(
            pattern=r"read this large"
        ),
        Input(tool="Read", file=FileFixture(name="notes.md", content=LONG), limit=40, state=ACTIVE): Allow(),
        Input(tool="Read", file=FileFixture(name="notes.md", content=LONG), offset=300, state=ACTIVE): Allow(),
        Input(tool="Read", file=FileFixture(name="small.md", content="a\nb\n"), state=ACTIVE): Allow(),
        Input(tool="Read", file=FileFixture(name="notes.md", content=LONG)): Allow(),
    },
)

root_block(
    message="File reads belong to a lane, not the drive root. Delegate with `Agent` using `Explore` and `model: sonnet`.",
    only_if=[ReadsRepoFiles()],
    confirm=ROOT_READ,
    tests={
        Input(command="cat {file}", file=FileFixture(name="notes.md", content="a\n"), state=ACTIVE): Block(
            pattern=r"File reads"
        ),
        Input(
            command="cat {file}", file=FileFixture(name="notes.md", content="a\n"), state=ACTIVE, llm={"block": False}
        ): Warn(pattern=r"allowed, the model found the call outside the rule"),
        Input(command="cat {file} # ccx:raw", file=FileFixture(name="notes.md", content="a\n"), state=ACTIVE): Allow(),
        Input(command="head -c 1200 {file}", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Allow(),
        Input(command="head -c 4001 {file}", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Block(),
        Input(command="head -n 40 {file}", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Allow(),
        Input(command="head -n 41 {file}", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Block(),
        Input(command="tail -n 40 {file}", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Allow(),
        Input(command="tail -n +40 {file}", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Block(),
        Input(command="tail -f {file}", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Block(),
        Input(command="grep -c line {file}", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Allow(),
        Input(command="grep -n -m 20 line {file}", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Allow(),
        Input(command="grep -m 21 line {file}", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Block(),
        Input(command="grep -m 5 -A 9 line {file}", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Block(),
        Input(command="grep -n line {file}", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Block(),
        Input(command="grep -n line {file} | head -n 12", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Allow(),
        Input(command="grep -m 1 -o . {file}", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Block(),
        Input(command="cat {file} >&2 | head -n 1", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Block(),
        Input(command="cat {file} | tee /dev/stderr | head -n 1", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Block(),
        Input(command="cat {file} | (head -n 1; cat)", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Block(),
        Input(command="for i in 1 2; do head -n 40 {file}; done", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Block(),
        Input(command="grep -c line {file} 2>/dev/null # count", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Allow(),
        Input(command="wc -l {file}", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE): Allow(),
        Input(
            command="head -n 5 {file} && head -n 5 {file}", file=FileFixture(name="notes.md", content=LONG), state=ACTIVE
        ): Block(),
        Input(command="sed -i '' s/a/b/ {file}", file=FileFixture(name="notes.md", content="a\n"), state=ACTIVE): Allow(),
        Input(command="cat {file}", file=FileFixture(name="notes.md", content="a\n")): Allow(),
        Input(command="cat >> inbox.md <<'EOF'\nlaunch l17 NOW\nEOF", state=ACTIVE): Allow(),
        Input(command="ccx vcs status | grep blocked", state=ACTIVE): Allow(),
        Input(command="ccx vcs status | jq .", state=ACTIVE): Allow(),
    },
)

root_block(
    message="Searches belong to a lane, not the drive root. Delegate with `Agent` using `Explore` and `model: sonnet`.",
    only_if=[SearchesRepo()],
    confirm=ROOT_READ,
    tests={
        Input(command="rg -n LAUNCH plugins", state=ACTIVE): Block(pattern=r"^Searches belong"),
        Input(command="rg -n LAUNCH plugins", state=ACTIVE, llm={"confident": False}): Warn(
            pattern=r"allowed, the model could not confirm"
        ),
        Input(tool="Grep", tool_input={"pattern": "LAUNCH", "path": "plugins"}, state=ACTIVE): Block(),
        Input(command="rg -n '# ccx:raw' plugins", state=ACTIVE): Block(),
        Input(command="rg -n LAUNCH plugins # ccx:raw", state=ACTIVE): Allow(),
        Input(command="rg -n LAUNCH plugins", env={"CAPT_HOOK_CCX_RAW": "1"}, state=ACTIVE): Allow(),
        Input(command="rg -n LAUNCH plugins"): Allow(),
        Input(command="rg -n LAUNCH plugins", agent_id="a1b2c3", state=ACTIVE): Allow(),
        Input(command="date -u +%H:%MZ && ls ~/scratch", state=ACTIVE): Allow(),
    },
)

root_block(
    message="Git history reads belong to a lane, not the drive root. Delegate with `Agent` using `Explore` and `model: sonnet`.",
    only_if=[runs_verb(GIT, ("log",), ("show",), ("diff",), ("blame",), ("grep",), ("reflog",))],
    confirm=ROOT_READ,
    tests={
        Input(command="git log --oneline -20", state=ACTIVE): Block(pattern=r"Git history"),
        Input(command="git log --oneline -20", state=ACTIVE, llm={"block": False}): Warn(pattern=r"allowed"),
        Input(command="git -C /tmp/repo diff --stat", state=ACTIVE): Block(),
        Input(command="git --no-optional-locks log -1", state=ACTIVE): Block(),
        Input(command="git --literal-pathspecs show HEAD", state=ACTIVE): Block(),
        Input(command="git status", state=ACTIVE): Allow(),
        Input(command="git log -5", agent_id="a1b2c3", state=ACTIVE): Allow(),
    },
)

root_block(
    message="Code and repo reads belong to a lane, not the drive root. Delegate with `Agent` using `Explore` and `model: sonnet`.",
    only_if=[
        Or(
            runs_verb(CCX, ("code",), ("repo",), ("vcs", "diff"), ("vcs", "show"), ("vcs", "history"), ("vcs", "log")),
            ToolStartsWith(*CCX_MCP_READS),
        )
    ],
    tests={
        Input(command="ccx code read hooks/x.py --section 1-90", state=ACTIVE): Block(pattern=r"Code and repo reads"),
        Input(command="ccx vcs diff", state=ACTIVE): Block(),
        Input(
            tool="mcp__plugin_cc-context_cc-context__ccx_code_read", tool_input={"path": "x.py"}, state=ACTIVE
        ): Block(),
        Input(command="ccx vcs status", state=ACTIVE): Allow(),
        Input(command="ccx vcs pr status 28797 28756", state=ACTIVE): Allow(),
    },
)

root_block(
    message="Web page reads belong to a lane, not the drive root. Delegate with `Agent` using `cc-context:web-fetch`.",
    only_if=[runs_verb(CCX, ("web",))],
    tests={
        Input(command="ccx web read https://example.com/docs", state=ACTIVE): Block(pattern=r"Web page reads"),
        Input(command="ccx vcs status", state=ACTIVE): Allow(),
    },
)

root_block(
    message="PR and review reads belong to a lane, not the drive root. Delegate with `Agent` using `cc-context:pr-review-triage`.",
    only_if=[Or(runs_verb(GH, ("pr", "view"), ("pr", "diff")), runs_verb(CCX, ("vcs", "reviews")))],
    tests={
        Input(command="gh pr view 28797 --json body", state=ACTIVE): Block(pattern=r"PR and review reads"),
        Input(command="gh -R yasyf/cc-skills pr view 148", state=ACTIVE): Block(),
        Input(command="ccx vcs reviews --stack", state=ACTIVE): Block(),
        Input(command="gh pr list", state=ACTIVE): Allow(),
        Input(command="gh pr view 28797", agent_id="a1b2c3", state=ACTIVE): Allow(),
    },
)

root_block(
    message="CI log reads belong to a lane, not the drive root. Delegate with `Agent` using `cc-context:ci-triage`.",
    only_if=[runs_verb(GH, ("run", "view"))],
    tests={
        Input(command="gh run view 123 --log-failed", state=ACTIVE): Block(pattern=r"CI log reads"),
        Input(command="gh run list", state=ACTIVE): Allow(),
    },
)

root_block(
    message="Issue reads belong to a lane, not the drive root. Delegate with `Agent` using `long-running:lane` and `model: sonnet`.",
    only_if=[runs_verb(GH, ("issue", "view"))],
    tests={
        Input(command="gh issue view 148", state=ACTIVE): Block(pattern=r"Issue reads"),
        Input(command="gh issue list", state=ACTIVE): Allow(),
    },
)

root_block(
    message="Slack reads belong to a lane, not the drive root. Delegate with `Agent` using `cc-slack:slack-triage`.",
    only_if=[Or(runs_verb(CC_SLACK, ("thread",), ("history",)), Tool(*SLACK_READ_TOOLS))],
    confirm=ROOT_READ,
    tests={
        Input(command="cc-slack thread C0B/p1790815593712039", state=ACTIVE): Block(pattern=r"Slack reads"),
        Input(tool="mcp__slack__slack_get_thread", tool_input={"channel": "C1", "ts": "1.2"}, state=ACTIVE): Block(),
        Input(
            tool="mcp__slack__slack_get_thread", tool_input={"channel": "C1", "ts": "1.2"}, state=ACTIVE, llm={"block": False}
        ): Warn(pattern=r"allowed"),
        Input(command="cc-slack dm-status --text 'parity wave 3 landed'", state=ACTIVE): Allow(),
        Input(command="cc-slack thread C0B/p1 # ccx:raw", state=ACTIVE): Allow(),
    },
)

root_block(
    message=(
        "The drive root never writes to Slack. "
        "Delegate with `Agent` using `long-running:lane-ship` and `model: sonnet`, briefed from `reference/slack-lane-brief.md`."
    ),
    only_if=[Or(runs_verb(CC_SLACK, ("send",), ("reply",), ("edit",), ("react",), ("unreact",)), Tool(*SLACK_WRITE_TOOLS))],
    bypass=False,
    tests={
        Input(
            tool="mcp__slack__slack_send_message", tool_input={"channel_id": "C0B", "text": "On it"}, state=ACTIVE
        ): Block(pattern=r"never writes to Slack"),
        Input(
            tool="mcp__slack__slack_add_reaction", tool_input={"channel_id": "C0B", "reaction": "eyes"}, state=ACTIVE
        ): Block(),
        Input(command="cc-slack react --url C0B/p1 --name eyes # ccx:raw", state=ACTIVE): Block(),
        Input(command="cc-slack react --url C0B/p1 --name eyes", env={"CAPT_HOOK_CCX_RAW": "1"}, state=ACTIVE): Block(),
        Input(command="~/.claude/plugins/cache/forge/cc-slack/0.2.11/bin/cc-slack react --url C0B/p1", state=ACTIVE): Block(),
        Input(tool="mcp__slack__slack_send_message", tool_input={"channel_id": "C0B", "text": "On it"}): Allow(),
        Input(command="cc-slack reply --url C0B/p1 --text 'On it'", agent_id="a1b2c3", state=ACTIVE): Allow(),
        Input(command="cc-slack dm-status --text 'parity wave 3 landed'", state=ACTIVE): Allow(),
        Input(command="cc-slack whoami", state=ACTIVE): Allow(),
    },
)

root_block(
    message="This fetch is too large for the drive root. Delegate with `Agent` using `long-running:lane` and `model: sonnet`.",
    only_if=[Or(Tool(*DOC_TOOLS), OversizedTool())],
    tests={
        Input(tool="mcp__plugin_datadog_mcp__get_datadog_notebook", tool_input={"id": 1}, state=ACTIVE): Block(
            pattern=r"too large"
        ),
        Input(
            tool="mcp__linear__get_issue",
            tool_input={"id": "ENG-1"},
            state=[*ACTIVE, RootContextState(oversized={"mcp__linear__get_issue": 40000})],
        ): Block(),
        Input(tool="mcp__linear__get_issue", tool_input={"id": "ENG-1"}, state=ACTIVE): Allow(),
        Input(tool="mcp__plugin_cc-notes_cc-notes__doc_show", tool_input={"id": "4ffc9a5"}, state=ACTIVE): Allow(),
        Input(tool="SendMessage", tool_input={"to": "orca-desk", "message": "go"}, state=ACTIVE): Allow(),
    },
)


@on(
    Event.PostToolUse,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    tests={
        Input(tool="mcp__linear__get_issue", output="x" * 9000, state=ACTIVE): Warn(pattern=r"overflowed the drive root"),
        Input(tool="mcp__linear__get_issue", output="small", state=ACTIVE): Allow(),
        Input(tool="mcp__plugin_cc-notes_cc-notes__doc_show", output="x" * 9000, state=ACTIVE): Allow(),
        Input(tool="mcp__linear__get_issue", output="x" * 9000): Allow(),
    },
)
def learn_oversized_mcp(evt: BaseHookEvent) -> HookResult | None:
    name = evt.tool_name or ""
    if not name.startswith("mcp__") or name.startswith(MCP_EXEMPT):
        return None
    response = evt.tool_response
    size = len(response if isinstance(response, str) else json.dumps(response))
    if size <= setting("MCP_CHARS", MCP_CHARS):
        return None
    with RootContextState.mutate(evt) as state:
        state.oversized[name] = size
    return evt.context(
        "That MCP response overflowed the drive root. "
        "Delegate the next call with `Agent` using `long-running:lane` and `model: sonnet`."
    )


def answers(evt: BaseHookEvent, verb: str, *args: str) -> Any:
    done = ccn(str(evt.cwd), "answer", verb, "--json", *args)
    done.check_returncode()
    return json.loads(done.stdout)


def message_label(evt: BaseHookEvent) -> str:
    digest = hashlib.sha256(f"{evt.session_id}\0{evt.user_prompt}".encode()).hexdigest()[:16]
    return f"message:{digest}"


def ruling_title(evt: BaseHookEvent) -> str:
    prompt = (
        Prompt()
        .system(RULING_SYSTEM)
        .context("message", evt.user_prompt or "")
        .ask("What question does the standing rule in this message answer?")
    )
    return evt.ctx.call_llm(prompt, response_model=RulingTitle, model="small", agent=False, transcript=False).title.strip()


def ruling_body(evt: BaseHookEvent) -> str:
    stamp = datetime.now(PACIFIC).strftime("%Y-%m-%d %-I:%M %p PT")
    return f"{evt.user_prompt}\n\nOwner, {stamp}, session {evt.session_id}"


def record_ruling(evt: BaseHookEvent) -> str:
    label = message_label(evt)
    if existing := answers(evt, "list", "--label", label, "--limit", "1"):
        return existing[0]["id"]
    if not (title := ruling_title(evt)):
        return ""
    flags = [arg for name in (*RULING_LABELS, label) for arg in ("--label", name)]
    return answers(evt, "add", *flags, f"--body={ruling_body(evt)}", "--", title)["id"]


@on(
    Event.UserPromptSubmit,
    only_if=[DriveActive(), OwnerPrompt()],
    skip_if=[FromSubagent()],
    tests={
        Input(
            prompt="from now on, release everything as it merges",
            state=ACTIVE,
            commands={"ccn": '[{"id": "4ffc9a5e0d"}]'},
        ): Warn(pattern=r"^Recorded this standing rule as cc-notes answer `4ffc9a5`"),
        Input(prompt="from now on, release everything as it merges", state=ACTIVE, commands={"ccn": "[]"}): Allow(),
        Input(prompt="what is the status of l17?", state=ACTIVE): Allow(),
    },
)
def capture_standing_rule(evt: BaseHookEvent) -> HookResult | None:
    if not evt.ctx.nlp(evt.user_prompt, STANDING):
        return None
    with RootContextState.mutate(evt) as state:
        state.rule_pending = True
        state.rule_recorded = False
    if not (answer_id := record_ruling(evt)):
        return None
    with RootContextState.mutate(evt) as state:
        state.rule_recorded = True
    return evt.context(f"Recorded this standing rule as cc-notes answer `{answer_id[:7]}`; never record it again by hand.")


@on(
    Event.PostToolUse,
    only_if=[
        DriveActive(),
        Or(Tool(*ANSWER_TOOLS), runs_verb(CCN, *ANSWER_WRITES), runs_verb(CC_NOTES, *ANSWER_WRITES)),
    ],
    skip_if=[FromSubagent()],
    tests={
        Input(command="ccn answer add 'Release as merged?' --body yes", state=ACTIVE): Allow(),
        Input(command="ccn doc show 4ffc9a5", state=ACTIVE): Allow(),
    },
)
def record_answer(evt: BaseHookEvent) -> None:
    with RootContextState.mutate(evt) as state:
        state.rule_recorded = True


@on(
    Event.Stop,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    tests={
        Input(state=[*ACTIVE, RootContextState(rule_pending=True)]): Allow(),
        Input(state=ACTIVE): Allow(),
    },
)
def nudge_unrecorded_standing_rule(evt: BaseHookEvent) -> None:
    with RootContextState.mutate(evt) as state:
        if state.rule_pending and not state.rule_recorded:
            queue_nudge(
                evt,
                "A standing rule stated by the owner must be recorded. "
                "Run `answer_add` with `scope:durable`.",
            )
        state.rule_pending = state.rule_recorded = False
