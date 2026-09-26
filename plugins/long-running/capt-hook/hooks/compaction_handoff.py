from __future__ import annotations

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
ARCHIVE_SUFFIX = "-pre-compact.md"
ARCHIVE_HEADING = "## Archived plans (history only, never needed to restart)"
COMPACT_JOB = Path(__file__).with_name("compact_job.py")
FIXTURES = Path(__file__).parent / "tests" / "fixtures"
FIRE_FRACTION = 0.8
COMPACT_RETRY_SECONDS = MAX_LIFETIME_SECONDS + 60


@workflow_state("long_running_compaction")
class CompactionState(WorkflowState):
    active: bool = False
    model: str | None = None
    plan_path: str | None = None
    phase: Literal["idle", "archived", "rewritten", "compacting"] = "idle"
    archive_path: str | None = None
    compacting_since: float | None = None


def compact_instructions(plan: str) -> str:
    return (
        f"Long-running compaction handoff. `{plan}` is the authoritative restart state; "
        "keep only in-flight details from the last turn that it lacks."
    )


def rewrite_nudge(*, used: int, limit: int, plan: Path, archive: Path | None) -> str:
    archived = f"; the previous version is archived at `{archive}`" if archive else ""
    return (
        f"Context is at {used:,} of the {limit:,}-token auto-compaction threshold ({round(100 * used / limit)}%). "
        f"When convenient, rewrite `{plan}` as the current restart state{archived}. "
        "The hook links the archives into it and runs /compact once the input line is empty."
    )


def archives_of(plan: Path) -> list[Path]:
    return sorted(plan.parent.glob(f"{plan.stem}.[0-9][0-9][0-9][0-9]-*{ARCHIVE_SUFFIX}"), reverse=True)


def link_archives(plan: Path) -> None:
    text = plan.read_text()
    if not (missing := [archive for archive in archives_of(plan) if str(archive) not in text]):
        return
    bullets = [f"- `{archive}`" for archive in missing]
    lines = text.rstrip("\n").split("\n")
    if ARCHIVE_HEADING in lines:
        at = lines.index(ARCHIVE_HEADING) + 1
        lines[at:at] = bullets
    else:
        lines += ["", ARCHIVE_HEADING, *bullets]
    plan.write_text("\n".join(lines) + "\n")


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
        Input(tool="Write", file="/home/u/.claude/plans/brook.2026-09-24-163000-pre-compact.md", content="# old"): Allow(),
    },
)
def track_plan(evt: BaseHookEvent) -> HookResult | None:
    path = evt.file.path
    if not path.match(".claude/plans/*.md") or path.name.endswith(ARCHIVE_SUFFIX):
        return None
    with CompactionState.mutate(evt) as state:
        if (
            state.phase == "archived"
            and state.plan_path
            and path == Path(state.plan_path).expanduser()
            and not (state.archive_path and path.read_bytes() == Path(state.archive_path).read_bytes())
        ):
            link_archives(path)
            state.phase = "rewritten"
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
def archive_at_threshold(evt: BaseHookEvent) -> HookResult | None:
    with CompactionState.mutate(evt) as state:
        if not state.active or state.phase != "idle" or not (root := latest_turn(evt.transcript_path)):
            return None
        limit = threshold(root.model, state.model, evt.cwd)
        if root.tokens < FIRE_FRACTION * limit:
            return None
        if state.plan_path and (plan := Path(state.plan_path).expanduser()).exists():
            archive = plan.with_name(f"{plan.stem}.{datetime.now(UTC):%Y-%m-%d-%H%M%S}{ARCHIVE_SUFFIX}")
            with plan.open("rb") as source, archive.open("xb") as target:
                shutil.copyfileobj(source, target)
        else:
            plan = (
                Path(state.plan_path).expanduser()
                if state.plan_path
                else Path.home() / ".claude" / "plans" / f"long-running-{evt.session_id[:8]}.md"
            )
            archive = None
        state.plan_path = str(plan)
        state.archive_path = str(archive) if archive else None
        state.phase = "archived"
        queue_nudge(evt, rewrite_nudge(used=root.tokens, limit=limit, plan=plan, archive=archive))
    return None


@on(
    Event.SessionStart,
    tests={
        Input(
            source="compact", state=[CompactionState(active=True, plan_path="/p/brook.md", phase="compacting")]
        ): Warn(pattern=r"^Compacted long-running session\. Read `/p/brook\.md` before anything else.*context\.$"),
        Input(
            source="compact",
            state=[
                CompactionState(
                    active=True,
                    plan_path="/p/brook.md",
                    archive_path="/p/brook.2026-09-24-163000-pre-compact.md",
                    phase="archived",
                )
            ],
        ): Warn(
            pattern=r"^Compacted long-running session\. Read `/p/brook\.md` .* It has not been rewritten since "
            r"`/p/brook\.2026-09-24-163000-pre-compact\.md` archived it; rewrite it as the current restart state "
            r"when convenient\.$"
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
            f" It has not been rewritten since `{state.archive_path}` archived it; rewrite it as the current "
            "restart state when convenient."
            if state.phase == "archived" and state.archive_path
            else ""
        )
        state.phase = "idle"
        state.compacting_since = None
        if not (state.active and state.plan_path):
            return None
    return evt.context(
        f"Compacted long-running session. Read `{state.plan_path}` before anything else; it supersedes the summary. "
        "The long-running skill stays active — reload its rules (Skill `long-running`) if they are not in context."
        + pending
    )


def send_compact(handle: str, plan: str, transcript: Path) -> None:
    subprocess.Popen(
        [sys.executable, str(COMPACT_JOB), handle, f"/compact {compact_instructions(plan)}", str(transcript)],
        env=dict(reqenv.env_map()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def compact_due(state: CompactionState) -> bool:
    if state.phase == "rewritten":
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
        Input(state=[CompactionState(active=True, plan_path="/p/brook.md", phase="archived")]): Allow(),
        Input(state=[CompactionState(active=True, plan_path="/p/brook.md", phase="rewritten")]): Allow(
            system_message=r"^Long-running plan `/p/brook\.md` is rewritten for compaction, but "
            r"ORCA_TERMINAL_HANDLE is unset so the hook cannot type it\. Run: /compact Long-running compaction "
            r"handoff\. `/p/brook\.md` is the authoritative restart state; .*$"
        ),
        Input(
            state=[CompactionState(active=True, plan_path="/p/brook.md", phase="compacting", compacting_since=None)]
        ): Allow(),
        Input(
            agent_id="a1b2c3", state=[CompactionState(active=True, plan_path="/p/brook.md", phase="rewritten")]
        ): Allow(),
    },
)
def compact_when_idle(evt: BaseHookEvent) -> HookResult | None:
    with CompactionState.mutate(evt) as state:
        if not state.active or not state.plan_path or not compact_due(state):
            return None
        state.phase = "compacting"
        if not (handle := reqenv.getenv("ORCA_TERMINAL_HANDLE")):
            state.compacting_since = None
            return evt.allow(
                system_message=f"Long-running plan `{state.plan_path}` is rewritten for compaction, but "
                "ORCA_TERMINAL_HANDLE is unset so the hook cannot type it. "
                f"Run: /compact {compact_instructions(state.plan_path)}"
            )
        state.compacting_since = time.time()
        send_compact(handle, state.plan_path, evt.transcript_path)
    return None


@on(
    Event.PreCompact,
    tests={
        Input(state=[CompactionState(active=True, plan_path="/p/brook.md")]): Warn(
            pattern=r"^Long-running compaction handoff\. `/p/brook\.md` is the authoritative restart state; "
            r"keep only in-flight details from the last turn that it lacks\.$"
        ),
        Input(state=[CompactionState(plan_path="/p/brook.md")]): Allow(),
    },
)
def compaction_instructions(evt: BaseHookEvent) -> HookResult | None:
    state = CompactionState.load(evt)
    if state.active and state.plan_path:
        return evt.context(compact_instructions(state.plan_path))
    return None
