from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from captain_hook import (
    Allow,
    BaseHookEvent,
    Event,
    FileFixture,
    FromSubagent,
    HookResult,
    Input,
    SkillCall,
    Tool,
    Warn,
    WorkflowState,
    on,
    workflow_state,
)
from captain_hook.conditions import skill_name_matches
from captain_hook.util import reqenv

from .compact_job import MAX_LIFETIME_SECONDS
from .nudges import queue_nudge
from .turns import latest_turn, threshold

SKILL_NAMES = ("long-running",)
PLAN_ARG = re.compile(r"[^\s`'\"]*\.claude/plans/[^\s/`'\"]+\.md")
POINTER_PREFIX = "- **Progress (read first after any compaction):**"
SLUG = re.compile(r"progress:([\w.-]+)")
DOC_SECTIONS = "how the drive runs; owner asks and state; lanes and binding rulings; landed; waiting on the owner; root's next actions"
COMPACT_JOB = Path(__file__).with_name("compact_job.py")
FIXTURES = Path(__file__).parent / "tests" / "fixtures"
FIRE_FRACTION = 0.8
COMPACT_RETRY_SECONDS = MAX_LIFETIME_SECONDS + 60


@workflow_state("long_running_compaction")
class CompactionState(WorkflowState):
    active: bool = False
    model: str | None = None
    plan_path: str | None = None
    phase: Literal["idle", "due", "written", "compacting"] = "idle"
    store: Literal["ccn", "folder"] = "ccn"
    slug: str | None = None
    due_since: float | None = None
    compacting_since: float | None = None


def ccn(cwd: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["ccn", "-R", cwd, *args], capture_output=True, text=True)


def has_cc_notes(cwd: str) -> bool:
    return shutil.which("ccn") is not None and ccn(cwd, "doc", "list", "--limit", "1", "--json").returncode == 0


def slug_of(plan: Path) -> str:
    text = plan.read_text() if plan.exists() else ""
    pointer = next((line for line in text.splitlines() if line.startswith(POINTER_PREFIX)), "")
    return match[1] if (match := SLUG.search(pointer)) else plan.stem


def progress_folder(plan: Path) -> Path:
    return plan.with_name(f"{plan.stem}-progress")


def resume_steps(state: CompactionState) -> str:
    plan = Path(state.plan_path or "")
    if state.store == "folder":
        return f"then the newest file in `{progress_folder(plan)}/`"
    return f"then the progress doc: `ccn doc list --label progress:{state.slug}`, then `ccn doc show <id>`"


def compact_instructions(state: CompactionState) -> str:
    return (
        f"Long-running compaction handoff. `{state.plan_path}` and its progress record are the authoritative "
        f"restart state: read the plan, {resume_steps(state)}. Keep only in-flight details from the last turn "
        "that they lack."
    )


def handoff_nudge(*, used: int, limit: int, state: CompactionState) -> str:
    plan = Path(state.plan_path or "")
    now = f"{datetime.now(UTC):%Y-%m-%dT%H%MZ}"
    if state.store == "folder":
        write = f"write the drive's whole execution state to a new file `{progress_folder(plan)}/{now}.md`"
    else:
        write = (
            f'write the drive\'s whole execution state as a new cc-notes doc: `ccn doc add "<drive>: progress {now}" '
            f'--label progress:{state.slug} --when "Resuming or compacting the <drive> drive: read before anything '
            f'else, after the plan" --body -`'
        )
    return (
        f"Context is at {used:,} of the {limit:,}-token auto-compaction threshold ({round(100 * used / limit)}%). "
        f"When convenient, {write}, with sections: {DOC_SECTIONS}. Never rewrite `{plan}`. "
        "The hook supersedes the previous progress record, points the plan's one progress line at the new one, "
        "and runs /compact once the input line is empty."
    )


def point_plan(plan: Path, line: str) -> None:
    lines = plan.read_text().rstrip("\n").split("\n") if plan.exists() else []
    at = next((i for i, text in enumerate(lines) if text.startswith(POINTER_PREFIX)), None)
    if at is None:
        lines += ["", line]
    else:
        lines[at] = line
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("\n".join(lines) + "\n")


def record_doc(state: CompactionState, cwd: str) -> bool:
    listed = ccn(cwd, "doc", "list", "--label", f"progress:{state.slug}", "--json")
    docs = sorted(json.loads(listed.stdout or "[]"), key=lambda doc: doc["updated_at"], reverse=True)
    if not docs or datetime.fromisoformat(docs[0]["updated_at"]).timestamp() < int(state.due_since or 0):
        return False
    newest, *older = docs
    for doc in older:
        ccn(cwd, "doc", "supersede", doc["id"], "--by", newest["id"])
    point_plan(
        Path(state.plan_path or ""),
        f"{POINTER_PREFIX} the latest execution state is the active cc-notes doc labelled `progress:{state.slug}` "
        f"(`ccn doc list --label progress:{state.slug}`, now `{newest['id'][:8]}`; `ccn doc show <id>`). Each "
        "handoff adds a new doc and supersedes the previous one, so history is the supersede chain; only this "
        "line's id changes.",
    )
    return True


def record_file(state: CompactionState) -> bool:
    plan = Path(state.plan_path or "")
    files = sorted(progress_folder(plan).glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files or files[0].stat().st_mtime < int(state.due_since or 0):
        return False
    point_plan(
        plan,
        f"{POINTER_PREFIX} the latest execution state is the newest file in `{progress_folder(plan)}/`, "
        f"now `{files[0].name}`; only this line's name changes.",
    )
    return True


@on(
    Event.PostToolUse,
    only_if=[Tool("Skill")],
    skip_if=[FromSubagent()],
    tests={
        Input(tool="Skill", tool_input={"skill": "long-running:long-running", "args": "~/.claude/plans/x.md"}): Allow(),
        Input(tool="Skill", tool_input={"skill": "codex"}): Allow(),
    },
)
def activate_on_skill(evt: BaseHookEvent) -> HookResult | None:
    if (call := evt.as_input(SkillCall)) and skill_name_matches(call.skill, SKILL_NAMES):
        activate(evt, call.args or "")
    return None


@on(
    Event.UserPromptSubmit,
    tests={
        Input(prompt="/long-running drive the release"): Allow(),
        Input(prompt="/long-running:long-running Continue the plan at ~/.claude/plans/x.md"): Allow(),
        Input(prompt="drive /long-running later"): Allow(),
    },
)
def activate_on_command(evt: BaseHookEvent) -> HookResult | None:
    if (prompt := evt.user_prompt or "").startswith("/long-running"):
        activate(evt, prompt)
    return None


def activate(evt: BaseHookEvent, args: str) -> None:
    with CompactionState.mutate(evt) as state:
        state.active = True
        if match := PLAN_ARG.search(args):
            state.plan_path = str(Path(match[0]).expanduser())


@on(
    Event.PostToolUse,
    only_if=[Tool("Write", "Edit")],
    skip_if=[FromSubagent()],
    tests={
        Input(tool="Write", file="/home/u/.claude/plans/brook.md", content="# plan"): Allow(),
    },
)
def track_plan(evt: BaseHookEvent) -> HookResult | None:
    path = evt.file.path
    if not path.match(".claude/plans/*.md"):
        return None
    with CompactionState.mutate(evt) as state:
        state.plan_path = str(path)
    return None


@on(
    Event.PostToolUse,
    skip_if=[FromSubagent()],
    tests={
        Input(tool="Bash", tool_input={"command": "ls"}, transcript=FIXTURES / "usage-460k.jsonl"): Allow(),
        Input(
            tool="Bash",
            tool_input={"command": "ls"},
            transcript=FIXTURES / "usage-262k-fable-5-1.jsonl",
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, model="claude-fable-5-1")],
        ): Allow(),
        Input(
            tool="Bash",
            tool_input={"command": "ls"},
            file=FileFixture(home=True, name="brook.md", content="# brook\n"),
            transcript=FIXTURES / "usage-460k.jsonl",
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, plan_path="~/brook.md")],
        ): Allow(),
        Input(
            tool="Bash",
            tool_input={"command": "ls"},
            transcript=FIXTURES / "usage-800k.jsonl",
            agent_id="a1b2c3",
            state=[CompactionState(active=True)],
        ): Allow(),
    },
)
def nudge_at_threshold(evt: BaseHookEvent) -> HookResult | None:
    with CompactionState.mutate(evt) as state:
        if not state.active or state.phase != "idle" or not (root := latest_turn(evt.transcript_path)):
            return None
        limit = threshold(root.model, state.model, evt.cwd)
        if root.tokens < FIRE_FRACTION * limit:
            return None
        plan = (
            Path(state.plan_path).expanduser()
            if state.plan_path
            else Path.home() / ".claude" / "plans" / f"long-running-{evt.session_id[:8]}.md"
        )
        state.plan_path = str(plan)
        state.slug = slug_of(plan)
        state.store = "ccn" if has_cc_notes(evt.cwd) else "folder"
        state.due_since = time.time()
        state.phase = "due"
        queue_nudge(evt, handoff_nudge(used=root.tokens, limit=limit, state=state))
    return None


@on(
    Event.SessionStart,
    tests={
        Input(
            source="compact",
            state=[CompactionState(active=True, plan_path="/p/brook.md", slug="brook", phase="compacting")],
        ): Warn(
            pattern=r"^Compacted long-running session\. Read `/p/brook\.md` before anything else, then the progress "
            r"doc: `ccn doc list --label progress:brook`, then `ccn doc show <id>`; .*context\.$"
        ),
        Input(
            source="compact",
            state=[CompactionState(active=True, plan_path="/p/brook.md", slug="brook", phase="due")],
        ): Warn(
            pattern=r"^Compacted long-running session\. .* The progress record was not written before compaction; "
            r"write it when convenient\.$"
        ),
        Input(source="compact", state=[CompactionState(plan_path="/p/brook.md")]): Allow(),
        Input(source="startup", state=[CompactionState(active=True, plan_path="/p/brook.md")]): Allow(),
    },
)
def reground_after_compact(evt: BaseHookEvent) -> HookResult | None:
    with CompactionState.mutate(evt) as state:
        if model := evt._raw.get("model"):
            state.model = model
        if evt.source != "compact":
            return None
        pending = (
            " The progress record was not written before compaction; write it when convenient."
            if state.phase == "due"
            else ""
        )
        state.phase = "idle"
        state.compacting_since = None
        if not (state.active and state.plan_path):
            return None
    return evt.context(
        f"Compacted long-running session. Read `{state.plan_path}` before anything else, {resume_steps(state)}; "
        "they supersede the summary. "
        "The long-running skill stays active — reload its rules (Skill `long-running`) if they are not in context."
        + pending
    )


def send_compact(handle: str, instructions: str, transcript: Path) -> None:
    subprocess.Popen(
        [sys.executable, str(COMPACT_JOB), handle, f"/compact {instructions}", str(transcript)],
        env=dict(reqenv.env_map()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def compact_due(state: CompactionState, cwd: str) -> bool:
    if state.phase == "due" and (record_file(state) if state.store == "folder" else record_doc(state, cwd)):
        state.phase = "written"
    if state.phase == "written":
        return True
    return (
        state.phase == "compacting"
        and state.compacting_since is not None
        and time.time() - state.compacting_since >= COMPACT_RETRY_SECONDS
    )


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    tests={
        Input(transcript=FIXTURES / "usage-800k.jsonl", state=[CompactionState(active=True)]): Allow(),
        Input(state=[CompactionState(active=True, plan_path="/p/brook.md", slug="brook", phase="written")]): Allow(
            system_message=r"^Long-running progress for `/p/brook\.md` is recorded for compaction, but "
            r"ORCA_TERMINAL_HANDLE is unset so the hook cannot type it\. Run: /compact Long-running compaction "
            r"handoff\. `/p/brook\.md` and its progress record are the authoritative restart state: .*$"
        ),
        Input(
            state=[CompactionState(active=True, plan_path="/p/brook.md", phase="compacting", compacting_since=None)]
        ): Allow(),
        Input(
            agent_id="a1b2c3", state=[CompactionState(active=True, plan_path="/p/brook.md", phase="written")]
        ): Allow(),
    },
)
def compact_when_idle(evt: BaseHookEvent) -> HookResult | None:
    with CompactionState.mutate(evt) as state:
        if not state.active or not state.plan_path or not compact_due(state, evt.cwd):
            return None
        state.phase = "compacting"
        if not (handle := reqenv.getenv("ORCA_TERMINAL_HANDLE")):
            state.compacting_since = None
            return evt.allow(
                system_message=f"Long-running progress for `{state.plan_path}` is recorded for compaction, but "
                "ORCA_TERMINAL_HANDLE is unset so the hook cannot type it. "
                f"Run: /compact {compact_instructions(state)}"
            )
        state.compacting_since = time.time()
        send_compact(handle, compact_instructions(state), evt.transcript_path)
    return None


@on(
    Event.PreCompact,
    tests={
        Input(state=[CompactionState(active=True, plan_path="/p/brook.md", slug="brook")]): Warn(
            pattern=r"^Long-running compaction handoff\. `/p/brook\.md` and its progress record are the "
            r"authoritative restart state: read the plan, then the progress doc: `ccn doc list --label progress:brook`, then "
            r"`ccn doc show <id>`\. Keep only in-flight details from the last turn that they lack\.$"
        ),
        Input(state=[CompactionState(plan_path="/p/brook.md")]): Allow(),
    },
)
def compaction_instructions(evt: BaseHookEvent) -> HookResult | None:
    state = CompactionState.load(evt)
    if state.active and state.plan_path:
        return evt.context(compact_instructions(state))
    return None
