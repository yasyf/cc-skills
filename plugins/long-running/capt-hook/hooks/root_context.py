from __future__ import annotations

import json
import os
import re
from collections.abc import Container, Iterator
from pathlib import Path

from captain_hook import (
    Allow,
    BaseHookEvent,
    Block,
    Event,
    FileFixture,
    FromSubagent,
    GrepCall,
    HookResult,
    Input,
    ReadCall,
    Warn,
    WorkflowState,
    on,
    workflow_state,
)
from captain_hook.cmd import Call, Target
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState, progress_folder
from .lane_rotation import TEAMMATE_MESSAGE
from .nudges import queue_nudge
from .task_list import SYSTEM_PREFIXES, excerpt

READ_LINES = 150
MCP_CHARS = 8000
ARTIFACT_DIRS = "audits,briefs,handoffs,tool-results,subagents,transcripts"
ARTIFACT_FILES = frozenset({"matrix.md"})
READ_DEFAULT_LIMIT = 2000
RAW_MARKER = re.compile(r"#\s*root:raw\s*$")
RAW_ESCAPE = "Bypass: append `# root:raw` to a Bash command."
READ_ESCAPE = "Read a window with offset/limit instead, or bypass with a Bash read ending `# root:raw`."
NO_ESCAPE = "No bypass for this tool."

EXPLORE = "Explore (model: sonnet)"
READER = "long-running:lane (model: sonnet)"
PR_TRIAGE = "cc-context:pr-review-triage"
CI_TRIAGE = "cc-context:ci-triage"
WEB_FETCH = "cc-context:web-fetch"
SLACK_TRIAGE = "cc-slack:slack-triage"

FILE_READERS = frozenset({"cat", "head", "tail", "sed", "awk", "grep", "egrep", "fgrep", "less", "more", "bat", "jq", "yq"})
SEARCHERS = frozenset({"rg", "ag", "ack"})
SCRIPT_FIRST = SEARCHERS | {"grep", "egrep", "fgrep", "sed", "awk", "jq", "yq"}
SCRIPT_FLAGS = frozenset({"-e", "-f", "--regexp", "--file", "--expression"})
GIT_READS = frozenset({"log", "show", "diff", "blame", "grep", "reflog"})
GH_READS = {
    ("pr", "view"): PR_TRIAGE,
    ("pr", "diff"): PR_TRIAGE,
    ("run", "view"): CI_TRIAGE,
    ("issue", "view"): READER,
}
CCX_READS = {"code": EXPLORE, "repo": EXPLORE, "web": WEB_FETCH}
CCX_VCS_READS = {"diff": EXPLORE, "show": EXPLORE, "history": EXPLORE, "log": EXPLORE, "reviews": PR_TRIAGE}
CC_SLACK_READS = frozenset({"thread", "history"})
SLACK_TOOLS = frozenset(
    {
        "mcp__slack__slack_get_thread",
        "mcp__slack__slack_conversations_history",
        "mcp__slack__slack_get_full_conversation",
        "mcp__slack__slack_catch_me_up",
        "mcp__slack__slack_search_messages",
        "mcp__plugin_cc-slack_cc-slack__slack_thread",
    }
)
CCX_MCP = "mcp__plugin_cc-context_cc-context__ccx_"
CCX_MCP_READS = ("code_", "repo_", "web_", "vcs_diff", "exec")
DOC_TOOLS = frozenset(
    {
        "mcp__claude_ai_Claude_Docs__read",
        "mcp__claude_ai_Claude_Docs__export",
        "mcp__plugin_datadog_mcp__get_datadog_notebook",
        "mcp__linear__get_document",
        "mcp__claude_ai_Capacities__getObjectContent",
        "mcp__claude_ai_Capacities__readObjectBlocks",
    }
)
MCP_EXEMPT = ("mcp__plugin_cc-notes_", "mcp__plugin_cc-present_", "mcp__plugin_codex_")
ANSWER_TOOLS = frozenset({"mcp__plugin_cc-notes_cc-notes__answer_add", "mcp__plugin_cc-notes_cc-notes__answer_edit"})
CCN = frozenset({"ccn", "cc-notes"})
ANSWER_VERBS = frozenset({("answer", "add"), ("answer", "edit")})
STANDING = re.compile(r"\b(?:from now on|always|never|I told you|the plan is)\b", re.IGNORECASE)
UNRECORDED = "owner standing rule not recorded: answer_add it (scope:durable) + a plan Decisions line"
COMMITMENT = re.compile(r"\b(?:from now on|we will|we now|going forward)\b", re.IGNORECASE)
SLACK_WRITES = frozenset(
    {
        "mcp__plugin_cc-slack_cc-slack__slack_send",
        "mcp__plugin_cc-slack_cc-slack__slack_reply",
        "mcp__slack__slack_send_message",
    }
)
CC_SLACK_WRITES = frozenset({"send", "reply"})
UNRECORDED_COMMITMENT = "standing commitment posted to Slack and not recorded: answer_add it (scope:durable) with the permalink"
LONG = "line\n" * 400
ACTIVE = [CompactionState(active=True)]

type Verdict = tuple[str, str, str]


@workflow_state("long_running_root_context")
class RootContextState(WorkflowState):
    oversized: dict[str, int] = {}
    standing: str | None = None
    committed: bool = False
    recorded: bool = False


def setting(name: str, default: int) -> int:
    return int(reqenv.getenv(f"LONG_RUNNING_ROOT_{name}", default))


def artifact_dirs() -> frozenset[str]:
    return frozenset(reqenv.getenv("LONG_RUNNING_ROOT_ARTIFACT_DIRS", ARTIFACT_DIRS).split(","))


def pinned(path: Path, plan: Path | None) -> bool:
    return plan is not None and (path == plan or path.is_relative_to(progress_folder(plan)))


def exempt(path: Path, plan: Path | None) -> bool:
    return pinned(path, plan) or "inbox" in path.parts


def artifact(path: Path) -> bool:
    return bool(artifact_dirs() & set(path.parts)) or path.name in ARTIFACT_FILES or path.suffix == ".jsonl"


def line_count(path: Path) -> int:
    with path.open("rb") as file:
        return sum(chunk.count(b"\n") for chunk in iter(lambda: file.read(1 << 20), b""))


def operands(call: Call) -> tuple[str, ...]:
    return tuple(target.value or "" for target in call.targets)


def candidates(target: Target) -> Iterator[Path]:
    if not target.has_glob:
        yield from filter(None, [target.path])
        return
    yield from (target.cwd / match if target.cwd else Path(match) for match in target.expand())


def paths(call: Call) -> list[Path]:
    targets = list(call.targets)
    if call.name in SCRIPT_FIRST and not any(flag.split("=")[0] in SCRIPT_FLAGS for flag in call.flags):
        targets = targets[1:]
    return [path for target in targets for path in candidates(target) if path.exists()]


def pair_in(words: tuple[str, ...], pairs: Container[tuple[str, str]]) -> tuple[str, str] | None:
    return next((pair for pair in zip(words, words[1:]) if pair in pairs), None)


def read_verdict(call: ReadCall, plan: Path | None) -> Verdict | None:
    path = Path(call.file_path).expanduser()
    if not path.is_file() or not os.access(path, os.R_OK) or pinned(path, plan):
        return None
    if artifact(path) and "inbox" not in path.parts:
        return EXPLORE, f"reading lane artifact `{path.name}`", READ_ESCAPE
    window = min(call.limit or READ_DEFAULT_LIMIT, line_count(path) - max((call.offset or 1) - 1, 0))
    if window > (limit := setting("READ_LINES", READ_LINES)):
        return EXPLORE, f"a {window}-line read of `{path.name}` (root limit {limit})", READ_ESCAPE
    return None


def bash_verdict(call: Call, plan: Path | None) -> Verdict | None:
    words = operands(call)
    if call.name in SEARCHERS:
        found = paths(call)
        if not found or not all(exempt(path, plan) for path in found):
            return EXPLORE, f"a `{call.name}` search", RAW_ESCAPE
    elif call.name in FILE_READERS and not (call.name == "sed" and "-i" in call.flags):
        if not all(exempt(path, plan) for path in paths(call)):
            return EXPLORE, f"a `{call.name}` read of repo or lane files", RAW_ESCAPE
    elif call.name == "git" and words[:1] and words[0] in GIT_READS:
        return EXPLORE, f"`git {words[0]}`", RAW_ESCAPE
    elif call.name == "gh" and (pair := pair_in(words, GH_READS)):
        return GH_READS[pair], f"`gh {' '.join(pair)}`", RAW_ESCAPE
    elif call.name == "ccx" and words[:1] and (agent := CCX_READS.get(words[0])):
        return agent, f"`ccx {words[0]}`", RAW_ESCAPE
    elif call.name == "ccx" and words[:1] == ("vcs",) and (agent := CCX_VCS_READS.get(words[1] if len(words) > 1 else "")):
        return agent, f"`ccx vcs {words[1]}`", RAW_ESCAPE
    elif call.name == "cc-slack" and words[:1] and words[0] in CC_SLACK_READS:
        return SLACK_TRIAGE, f"`cc-slack {words[0]}`", RAW_ESCAPE
    return None


def tool_verdict(evt: BaseHookEvent, plan: Path | None) -> Verdict | None:
    name = evt.tool_name or ""
    if read := evt.as_input(ReadCall):
        return read_verdict(read, plan)
    if name == "Bash":
        if RAW_MARKER.search(evt.command.raw.rstrip()):
            return None
        return next((verdict for call in evt.command.calls() if (verdict := bash_verdict(call, plan))), None)
    if grep := evt.as_input(GrepCall):
        root = Path(grep.path).expanduser() if grep.path else None
        return None if root and exempt(root, plan) else (EXPLORE, "a Grep search", NO_ESCAPE)
    if name in SLACK_TOOLS:
        return SLACK_TRIAGE, "a Slack thread or history read", NO_ESCAPE
    if name.startswith(CCX_MCP) and name.removeprefix(CCX_MCP).startswith(CCX_MCP_READS):
        return EXPLORE, f"`{name.removeprefix(CCX_MCP)}`", NO_ESCAPE
    if name in DOC_TOOLS or name in RootContextState.load(evt).oversized:
        return READER, f"the `{name}` fetch", NO_ESCAPE
    return None


def delegate(verdict: Verdict) -> str:
    agent, what, escape = verdict
    return (
        f"delegate to a lane: {agent} — {what} belongs to a lane, not the drive root "
        f"(long-running R2; the lane returns the conclusion and pointers). {escape}"
    )


@on(
    Event.PreToolUse,
    skip_if=[FromSubagent()],
    tests={
        Input(command="rg -n LAUNCH plugins", state=ACTIVE): Block(pattern=r"^delegate to a lane: Explore"),
        Input(command="git log --oneline -20", state=ACTIVE): Block(pattern=r"`git log`"),
        Input(command="gh pr view 28797 --json body", state=ACTIVE): Block(pattern=r"cc-context:pr-review-triage"),
        Input(command="gh run view 123 --log-failed", state=ACTIVE): Block(pattern=r"cc-context:ci-triage"),
        Input(command="ccx code read hooks/x.py --section 1-90", state=ACTIVE): Block(pattern=r"`ccx code`"),
        Input(command="ccx vcs diff", state=ACTIVE): Block(pattern=r"`ccx vcs diff`"),
        Input(command="cc-slack thread C0B/p1790815593712039", state=ACTIVE): Block(pattern=r"cc-slack:slack-triage"),
        Input(command="rg -n LAUNCH plugins # root:raw", state=ACTIVE): Allow(),
        Input(command="rg -n '# root:raw' plugins", state=ACTIVE): Block(),
        Input(command="gh -R yasyf/cc-skills pr view 148", state=ACTIVE): Block(pattern=r"`gh pr view`"),
        Input(command="ccx vcs status | jq .", state=ACTIVE): Allow(),
        Input(command="ccx vcs status", state=ACTIVE): Allow(),
        Input(command="ccx vcs pr status 28797 28756", state=ACTIVE): Allow(),
        Input(command="date -u +%H:%MZ && ls ~/scratch", state=ACTIVE): Allow(),
        Input(command="ccn doc show 4ffc9a5", state=ACTIVE): Allow(),
        Input(command="cat >> inbox.md <<'EOF'\nR576 orca-desk: launch l17 NOW\nEOF", state=ACTIVE): Allow(),
        Input(command="ccx vcs status | grep blocked", state=ACTIVE): Allow(),
        Input(command="cc-present push board.json --session s", state=ACTIVE): Allow(),
        Input(command="rg -n LAUNCH plugins"): Allow(),
        Input(command="git log -5", agent_id="a1b2c3", state=ACTIVE): Allow(),
        Input(tool="Read", file=FileFixture(name="matrix-notes.md", content=LONG), state=ACTIVE): Block(
            pattern=r"400-line read of `matrix-notes\.md` \(root limit 150\)"
        ),
        Input(tool="Read", file=FileFixture(name="notes.md", content=LONG), limit=40, state=ACTIVE): Allow(),
        Input(tool="Read", file=FileFixture(name="notes.md", content=LONG), offset=300, state=ACTIVE): Allow(),
        Input(tool="Read", file=FileFixture(name="small.md", content="a\nb\n"), state=ACTIVE): Allow(),
        Input(tool="Read", file=FileFixture(name="matrix.md", content="a\n"), state=ACTIVE): Block(
            pattern=r"lane artifact `matrix\.md`"
        ),
        Input(tool="Read", file=FileFixture(name="notes.md", content=LONG)): Allow(),
        Input(tool="Grep", tool_input={"pattern": "LAUNCH", "path": "plugins"}, state=ACTIVE): Block(
            pattern=r"a Grep search"
        ),
        Input(tool="mcp__slack__slack_get_thread", tool_input={"channel": "C1", "ts": "1.2"}, state=ACTIVE): Block(
            pattern=r"cc-slack:slack-triage"
        ),
        Input(tool="mcp__plugin_cc-context_cc-context__ccx_code_read", tool_input={"path": "x.py"}, state=ACTIVE): Block(
            pattern=r"`code_read`"
        ),
        Input(tool="mcp__plugin_datadog_mcp__get_datadog_notebook", tool_input={"id": 1}, state=ACTIVE): Block(
            pattern=r"long-running:lane"
        ),
        Input(
            tool="mcp__linear__get_issue",
            tool_input={"id": "ENG-1"},
            state=[*ACTIVE, RootContextState(oversized={"mcp__linear__get_issue": 40000})],
        ): Block(pattern=r"`mcp__linear__get_issue` fetch"),
        Input(tool="mcp__plugin_cc-notes_cc-notes__doc_show", tool_input={"id": "4ffc9a5"}, state=ACTIVE): Allow(),
        Input(tool="SendMessage", tool_input={"to": "orca-desk", "message": "go"}, state=ACTIVE): Allow(),
    },
)
def keep_lane_reads_out_of_root(evt: BaseHookEvent) -> HookResult | None:
    drive = CompactionState.load(evt)
    if not drive.active:
        return None
    plan = Path(drive.plan_path).expanduser() if drive.plan_path else None
    return evt.block(delegate(verdict)) if (verdict := tool_verdict(evt, plan)) else None


@on(
    Event.PostToolUse,
    skip_if=[FromSubagent()],
    tests={
        Input(tool="mcp__linear__get_issue", output="x" * 9000, state=ACTIVE): Warn(pattern=r"9000 chars.*next call blocks"),
        Input(tool="mcp__linear__get_issue", output="small", state=ACTIVE): Allow(),
        Input(tool="mcp__plugin_cc-notes_cc-notes__doc_show", output="x" * 9000, state=ACTIVE): Allow(),
        Input(tool="mcp__linear__get_issue", output="x" * 9000): Allow(),
    },
)
def learn_oversized_mcp(evt: BaseHookEvent) -> HookResult | None:
    name = evt.tool_name or ""
    if not name.startswith("mcp__") or name.startswith(MCP_EXEMPT) or not CompactionState.load(evt).active:
        return None
    response = evt.tool_response
    size = len(response if isinstance(response, str) else json.dumps(response))
    if size <= setting("MCP_CHARS", MCP_CHARS):
        return None
    with RootContextState.mutate(evt) as state:
        state.oversized[name] = size
    return evt.context(
        f"`{name}` put {size} chars into the drive root; its next call blocks here. "
        f"Delegate it to a lane: {READER} returns the conclusion."
    )


def records_answer(evt: BaseHookEvent) -> bool:
    if evt.tool_name in ANSWER_TOOLS:
        return True
    return any(call.name in CCN and pair_in(operands(call), ANSWER_VERBS) for call in evt.command.calls())


def posts_commitment(evt: BaseHookEvent) -> bool:
    if evt.tool_name in SLACK_WRITES:
        return COMMITMENT.search(json.dumps(evt.input.raw)) is not None
    return any(
        call.name == "cc-slack" and set(operands(call)[:1]) & CC_SLACK_WRITES and COMMITMENT.search(" ".join(call.args))
        for call in evt.command.calls()
    )


def standing_rule(prompt: str) -> bool:
    text = prompt.strip()
    return not text.startswith(SYSTEM_PREFIXES) and not TEAMMATE_MESSAGE.search(text) and STANDING.search(text) is not None


@on(
    Event.UserPromptSubmit | Event.PostToolUse | Event.Stop,
    skip_if=[FromSubagent()],
    tests={
        Input(prompt="from now on, release everything as it merges", state=ACTIVE): Allow(),
        Input(command="ccn answer add 'Release as merged?' --body yes", state=ACTIVE): Allow(),
        Input(prompt="from now on, release everything as it merges"): Allow(),
        Input(
            tool="mcp__plugin_cc-slack_cc-slack__slack_reply",
            tool_input={"thread": "C0B/p1790815593712039", "text": "Going forward we release as merged."},
            state=ACTIVE,
        ): Allow(),
    },
)
def nudge_unrecorded_standing_rule(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    if evt.event == Event.UserPromptSubmit:
        if standing_rule(prompt := evt.user_prompt or ""):
            with RootContextState.mutate(evt) as state:
                state.standing, state.recorded = excerpt(prompt), False
    elif evt.event == Event.PostToolUse:
        if records_answer(evt):
            with RootContextState.mutate(evt) as state:
                state.recorded = True
        elif posts_commitment(evt):
            with RootContextState.mutate(evt) as state:
                state.committed = True
    else:
        with RootContextState.mutate(evt) as state:
            if state.standing and not state.recorded:
                queue_nudge(evt, f"{UNRECORDED} — {state.standing}")
            if state.committed and not state.recorded:
                queue_nudge(evt, UNRECORDED_COMMITMENT)
            state.standing, state.committed, state.recorded = None, False, False
    return None
