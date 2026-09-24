from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from captain_hook import (
    Allow,
    BaseHookEvent,
    Block,
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

SKILL_NAMES = ("long-running",)
PLAN_ARG = re.compile(r"\S*\.claude/plans/[^\s/]+\.md")
ARCHIVE_SUFFIX = "-pre-compact.md"
FIXTURES = Path(__file__).parent / "tests" / "fixtures"

WINDOW_FLOOR = 100_000
WINDOW_CEILING = 1_000_000
DEFAULT_WINDOW = 200_000
OUTPUT_RESERVE = 20_000
AUTOCOMPACT_BUFFER = 13_000
FIRE_FRACTION = 0.8
MAX_REMINDERS = 2


@workflow_state("long_running_compaction")
class CompactionState(WorkflowState):
    active: bool = False
    model: str | None = None
    plan_path: str | None = None
    phase: Literal["idle", "rewriting", "compacting"] = "idle"
    archive_path: str | None = None
    reminders: int = 0


def model_window(model: str | None) -> int:
    return WINDOW_CEILING if model and model.endswith("[1m]") else DEFAULT_WINDOW


def settings_window(project: Path | None) -> int | None:
    files = [Path.home() / ".claude" / "settings.json"]
    if project:
        files += [project / ".claude" / "settings.json", project / ".claude" / "settings.local.json"]
    window = None
    for file in files:
        if file.is_file() and (value := json.loads(file.read_text()).get("autoCompactWindow")) is not None:
            window = value
    return window


def configured_window(project: Path | None) -> int | None:
    if env := reqenv.getenv("CLAUDE_CODE_AUTO_COMPACT_WINDOW"):
        return min(max(int(env), WINDOW_FLOOR), WINDOW_CEILING)
    return settings_window(project)


def threshold(model: str | None, project: Path | None) -> int:
    cap = model_window(model)
    window = min(configured_window(project) or cap, cap)
    limit = window - OUTPUT_RESERVE - AUTOCOMPACT_BUFFER
    if pct := reqenv.getenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"):
        limit = min(int((window - OUTPUT_RESERVE) * float(pct) / 100), limit)
    return limit


def used_tokens(transcript: Path) -> int:
    for line in reversed(transcript.read_text().splitlines()):
        entry = json.loads(line)
        if entry.get("type") == "assistant" and not entry.get("isSidechain") and (usage := entry["message"].get("usage")):
            return usage["input_tokens"] + usage["cache_creation_input_tokens"] + usage["cache_read_input_tokens"]
    return 0


def compact_instructions(plan: str) -> str:
    return (
        f"Long-running compaction handoff. `{plan}` is the authoritative restart state; "
        "keep only in-flight details from the last turn that it lacks."
    )


def directive(*, used: int, limit: int, archive: Path | None, plan: Path, archives: list[Path], now: str) -> str:
    archived = f"The current plan is archived at `{archive}`. " if archive else ""
    bullets = "\n".join(f"- `{path}`" for path in archives)
    return (
        f"Context is at {used:,} of the {limit:,}-token auto-compaction threshold ({round(100 * used / limit)}%). "
        f"Compaction handoff — do not enter plan mode. {archived}"
        "Before rewriting, record any durable state still living only in this conversation in cc-notes "
        f"(ledger, rulings log, notes). Then rewrite `{plan}` with one Write: dump all context needed on restart, "
        "get rid of everything unnecessary (finished work, superseded state, anything the archives already hold). "
        f"Use exactly these sections: `# <title> (compacted {now}Z)`, `## Restart here (read first)`, "
        "`## Mandate (owner, verbatim)`, `## Standing constraints`, `## End state`, "
        f"`## Owner decisions (never re-ask)`, `## State at {now}Z`, `## Live lanes`, `## Owed follow-ups`, "
        "`## Owner actions pending`, `## Key notes`, `## Done means`, "
        f"`## Archived plans (history only, never needed to restart)` with these bullets verbatim:\n{bullets}\n"
        "Then end your turn; the hook runs /compact."
    )


@on(
    Event.PostToolUse,
    only_if=[Tool("Skill")],
    tests={
        Input(tool="Skill", tool_input={"skill": "long-running:long-running", "args": "~/.claude/plans/x.md"}): Allow(),
        Input(tool="Skill", tool_input={"skill": "codex"}): Allow(),
    },
)
def activate_on_skill(evt: BaseHookEvent) -> HookResult | None:
    if not (call := evt.as_input(SkillCall)) or not skill_name_matches(call.skill, SKILL_NAMES):
        return None
    state = CompactionState.load(evt)
    state.active = True
    if match := PLAN_ARG.search(call.args or ""):
        state.plan_path = str(Path(match[0]).expanduser())
    state.save(evt)
    return None


@on(Event.UserPromptSubmit, tests={Input(prompt="/long-running drive the release"): Allow()})
def activate_on_command(evt: BaseHookEvent) -> HookResult | None:
    if (evt.user_prompt or "").startswith("/long-running"):
        state = CompactionState.load(evt)
        state.active = True
        state.save(evt)
    return None


@on(
    Event.PostToolUse,
    only_if=[Tool("Write", "Edit")],
    tests={
        Input(tool="Write", file="/home/u/.claude/plans/brook.md", content="# plan"): Allow(),
        Input(tool="Write", file="/home/u/.claude/plans/brook.2026-09-24-1630-pre-compact.md", content="# old"): Allow(),
    },
)
def track_plan(evt: BaseHookEvent) -> HookResult | None:
    path = evt.file.path
    if path.match(".claude/plans/*.md") and not path.name.endswith(ARCHIVE_SUFFIX):
        state = CompactionState.load(evt)
        state.plan_path = str(path)
        state.save(evt)
    return None


@on(
    Event.SessionStart,
    tests={
        Input(
            source="compact", state=[CompactionState(active=True, plan_path="/p/brook.md", phase="compacting")]
        ): Warn(pattern=r"^Compacted long-running session\. Read `/p/brook\.md` before anything else"),
        Input(source="compact", state=[CompactionState(plan_path="/p/brook.md")]): Allow(),
        Input(source="startup", state=[CompactionState(active=True, plan_path="/p/brook.md")]): Allow(),
    },
)
def reground_after_compact(evt: BaseHookEvent) -> HookResult | None:
    state = CompactionState.load(evt)
    if model := evt._raw.get("model"):
        state.model = model
    if evt.source == "compact" and state.active:
        state.phase = "idle"
        state.reminders = 0
    state.save(evt)
    if evt.source == "compact" and state.active and state.plan_path:
        return evt.context(
            f"Compacted long-running session. Read `{state.plan_path}` before anything else; it supersedes the summary. "
            "The long-running skill stays active — reload its rules (Skill `long-running`) if they are not in context."
        )
    return None


def begin_handoff(evt: BaseHookEvent, state: CompactionState) -> HookResult | None:
    used = used_tokens(evt.transcript_path)
    limit = threshold(state.model, evt.cwd)
    if used < FIRE_FRACTION * limit:
        return None
    now = datetime.now(UTC)
    if state.plan_path:
        plan = Path(state.plan_path).expanduser()
        archive = plan.with_name(f"{plan.stem}.{now:%Y-%m-%d-%H%M}{ARCHIVE_SUFFIX}")
        shutil.copy2(plan, archive)
    else:
        plan = Path.home() / ".claude" / "plans" / f"long-running-{evt.session_id[:8]}.md"
        archive = None
    archives = sorted(plan.parent.glob(f"{plan.stem}.*{ARCHIVE_SUFFIX}"), reverse=True)
    state.plan_path = str(plan)
    state.archive_path = str(archive) if archive else None
    state.phase = "rewriting"
    state.reminders = 0
    state.save(evt)
    return evt.block(
        directive(used=used, limit=limit, archive=archive, plan=plan, archives=archives, now=f"{now:%Y-%m-%d %H:%M}")
    )


def plan_rewritten(state: CompactionState) -> bool:
    plan = Path(state.plan_path).expanduser()
    if state.archive_path is None:
        return plan.exists()
    return plan.read_bytes() != Path(state.archive_path).read_bytes()


def send_compact(handle: str, plan: str) -> None:
    subprocess.Popen(
        [
            "sh",
            "-c",
            'orca terminal wait --terminal "$1" --for tui-idle --timeout-ms 120000 '
            '&& orca terminal send --terminal "$1" --text "$2" --enter',
            "compact",
            handle,
            f"/compact {compact_instructions(plan)}",
        ],
        env=dict(reqenv.env_map()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def finish_handoff(evt: BaseHookEvent, state: CompactionState) -> HookResult | None:
    if not plan_rewritten(state):
        if state.reminders < MAX_REMINDERS:
            state.reminders += 1
            state.save(evt)
            return evt.block(
                f"Compaction handoff pending: rewrite `{state.plan_path}` with one Write per the directive, "
                "then end your turn."
            )
        state.phase = "idle"
        state.reminders = 0
        state.save(evt)
        return evt.allow(
            system_message=f"Long-running compaction handoff gave up: `{state.plan_path}` was not rewritten "
            f"after {MAX_REMINDERS} reminders. Run /compact yourself, or the hook retries at the next stop."
        )
    state.phase = "compacting"
    state.save(evt)
    if not (handle := reqenv.getenv("ORCA_TERMINAL_HANDLE")):
        return evt.allow(
            system_message=f"Long-running plan `{state.plan_path}` is rewritten for compaction, but "
            f"ORCA_TERMINAL_HANDLE is unset so the hook cannot type it. Run: /compact {compact_instructions(state.plan_path)}"
        )
    send_compact(handle, state.plan_path)
    return None


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    tests={
        Input(transcript=FIXTURES / "usage-460k.jsonl"): Allow(),
        Input(
            transcript=FIXTURES / "usage-460k.jsonl",
            agent_id="a1b2c3",
            state=[CompactionState(active=True)],
        ): Allow(),
        Input(
            transcript=FIXTURES / "usage-170k.jsonl",
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, model="claude-opus-5-5[1m]")],
        ): Allow(),
        Input(
            transcript=FIXTURES / "usage-460k.jsonl",
            state=[CompactionState(active=True, model="claude-opus-5-5[1m]")],
        ): Allow(),
        Input(
            transcript=FIXTURES / "usage-170k.jsonl",
            session_id="0123456789abcdef",
            state=[CompactionState(active=True)],
        ): Block(pattern=r"^Context is at 170,000 of the 167,000-token .*rewrite `\S+/\.claude/plans/long-running-01234567\.md`"),
        Input(
            transcript=FIXTURES / "usage-460k.jsonl",
            cwd=str(FIXTURES / "project-local"),
            session_id="0123456789abcdef",
            state=[CompactionState(active=True, model="claude-opus-5-5[1m]")],
        ): Block(pattern=r"^Context is at 460,000 of the 267,000-token auto-compaction threshold \(172%\)"),
        Input(
            file=FileFixture(home=True, name="brook.md", content="# brook\n"),
            transcript=FIXTURES / "usage-460k.jsonl",
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, model="claude-opus-5-5[1m]", plan_path="~/brook.md")],
        ): Block(
            pattern=r"^Context is at 460,000 of the 567,000-token auto-compaction threshold \(81%\)\. "
            r"Compaction handoff — do not enter plan mode\. The current plan is archived at "
            r"`(\S+/)brook\.\d{4}-\d\d-\d\d-\d{4}-pre-compact\.md`\. .*rewrite `\1brook\.md` with one Write.*"
            r"verbatim:\n- `\1brook\.\d{4}-\d\d-\d\d-\d{4}-pre-compact\.md`\nThen end your turn; the hook runs /compact\.$"
        ),
        Input(
            state=[
                CompactionState(
                    active=True,
                    plan_path=str(FIXTURES / "plans" / "same.md"),
                    archive_path=str(FIXTURES / "plans" / "same.2026-09-24-1630-pre-compact.md"),
                    phase="rewriting",
                )
            ]
        ): Block(pattern=r"^Compaction handoff pending: rewrite `\S+/same\.md`"),
        Input(
            state=[
                CompactionState(
                    active=True,
                    plan_path=str(FIXTURES / "plans" / "same.md"),
                    archive_path=str(FIXTURES / "plans" / "same.2026-09-24-1630-pre-compact.md"),
                    phase="rewriting",
                    reminders=MAX_REMINDERS,
                )
            ]
        ): Allow(system_message=r"^Long-running compaction handoff gave up: `\S+/same\.md` was not rewritten"),
        Input(
            state=[
                CompactionState(
                    active=True,
                    plan_path=str(FIXTURES / "plans" / "changed.md"),
                    archive_path=str(FIXTURES / "plans" / "changed.2026-09-24-1630-pre-compact.md"),
                    phase="rewriting",
                )
            ]
        ): Allow(system_message=r"ORCA_TERMINAL_HANDLE is unset .* Run: /compact Long-running compaction handoff\."),
        Input(
            transcript=FIXTURES / "usage-460k.jsonl",
            state=[CompactionState(active=True, plan_path=str(FIXTURES / "plans" / "same.md"), phase="compacting")],
        ): Allow(),
    },
)
def compaction_handoff(evt: BaseHookEvent) -> HookResult | None:
    state = CompactionState.load(evt)
    if not state.active:
        return None
    match state.phase:
        case "idle":
            return begin_handoff(evt, state)
        case "rewriting":
            return finish_handoff(evt, state)
        case "compacting":
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
