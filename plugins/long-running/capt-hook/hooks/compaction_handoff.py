from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
PLAN_ARG = re.compile(r"[^\s`'\"]*\.claude/plans/[^\s/`'\"]+\.md")
LEADING_FLOAT = re.compile(r"\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
LEGACY_WINDOW = re.compile(r"claude-(?:3-|haiku-4-5|sonnet-4-|opus-4-(?:[0-6]\b|\d{8}))")
ONE_M_SUFFIX = "[1m]"
ARCHIVE_SUFFIX = "-pre-compact.md"
SYNTHETIC_MODEL = "<synthetic>"
FIXTURES = Path(__file__).parent / "tests" / "fixtures"
LANES = FIXTURES / "lanes" / "projects" / "p"
REVIEWER = {"id": "a0d0d0d0d0d0d0d0d", "type": "subagent", "status": "running", "description": "lane"}
DESK = {"id": "t0b0b0b0b", "type": "teammate", "status": "running", "description": "landing desk"}
WATCHER = {"id": "t0c0c0c0c", "type": "teammate", "status": "running", "description": "pr watch"}
ARCHIVIST = {"id": "t0j0j0j0j", "type": "teammate", "status": "running", "description": "archivist"}
SLEEPER = {"id": "t0k0k0k0k", "type": "teammate", "status": "running", "description": "sleeper"}
DESK_ID = "alanding-desk-0b0b0b0b0b0b0b0b"
PENDING_NUDGE = "Lane rotation (advisory): `landing-desk` (430,000) passed the 400,000-token rotation line."

WINDOW_FLOOR = 100_000
WINDOW_CEILING = 1_000_000
DEFAULT_WINDOW = 200_000
OUTPUT_RESERVE = 20_000
AUTOCOMPACT_BUFFER = 13_000
FIRE_FRACTION = 0.8
LANE_ROTATE_TOKENS = 400_000
NUDGE_INTERVAL_SECONDS = 15 * 60
ROTATE_GRACE_SECONDS = 30 * 60
NUDGE_LANES = 3
DORMANT_AFTER = timedelta(hours=1)
TAIL_BLOCK = 1 << 16


@workflow_state("long_running_compaction")
class CompactionState(WorkflowState):
    active: bool = False
    model: str | None = None
    plan_path: str | None = None
    phase: Literal["idle", "rewriting", "compacting", "declined"] = "idle"
    archive_path: str | None = None
    rotated: dict[str, float] = {}
    overdue: list[str] = []
    nudged_at: float = 0.0
    nudge: str | None = None


@dataclass(frozen=True)
class Turn:
    model: str
    tokens: int
    at: datetime


@dataclass(frozen=True)
class Lane:
    name: str
    agent_id: str
    turn: Turn


def model_window(model: str, hint: str | None) -> int:
    if hint and hint.endswith(ONE_M_SUFFIX) and model.startswith(hint.removesuffix(ONE_M_SUFFIX)):
        return WINDOW_CEILING
    if model.startswith("claude-") and not LEGACY_WINDOW.match(model):
        return WINDOW_CEILING
    return DEFAULT_WINDOW


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


def pct_override() -> float | None:
    raw = reqenv.getenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE") or ""
    if (match := LEADING_FLOAT.match(raw)) and 0 < (pct := float(match[0])) <= 100:
        return pct
    return None


def threshold(model: str, hint: str | None, project: Path | None) -> int:
    cap = model_window(model, hint)
    window = min(configured_window(project) or cap, cap)
    limit = window - OUTPUT_RESERVE - AUTOCOMPACT_BUFFER
    if pct := pct_override():
        limit = min(int((window - OUTPUT_RESERVE) * pct / 100), limit)
    return limit


def rotate_tokens() -> int:
    return int(reqenv.getenv("LONG_RUNNING_LANE_ROTATE_TOKENS") or LANE_ROTATE_TOKENS)


def reversed_lines(path: Path) -> Iterator[bytes]:
    with path.open("rb") as file:
        end = file.seek(0, os.SEEK_END)
        head = b""
        while end > 0:
            start = max(0, end - TAIL_BLOCK)
            file.seek(start)
            head, *lines = (file.read(end - start) + head).split(b"\n")
            yield from reversed(lines)
            end = start
        yield head


def latest_turn(transcript: Path, *, sidechain: bool = False) -> Turn | None:
    for line in reversed_lines(transcript):
        if not line.strip():
            continue
        entry = json.loads(line)
        if (
            entry.get("type") == "assistant"
            and entry.get("isSidechain", False) == sidechain
            and (model := entry["message"].get("model")) != SYNTHETIC_MODEL
            and (usage := entry["message"].get("usage"))
        ):
            tokens = usage["input_tokens"] + usage["cache_creation_input_tokens"] + usage["cache_read_input_tokens"]
            return Turn(model, tokens, datetime.fromisoformat(entry["timestamp"]))
    return None


def spawned_at(transcript: Path) -> str | None:
    if not transcript.is_file():
        return None
    with transcript.open() as lines:
        first = next(lines, None)
    return json.loads(first)["timestamp"] if first else None


def live_lanes(evt: BaseHookEvent) -> list[Lane]:
    live_subagents = {task.id for task in evt.background_tasks if task.type == "subagent"}
    teammate_tasks = Counter(task.description for task in evt.background_tasks if task.type == "teammate")
    newest: dict[str, tuple[str, str, Path, str | None]] = {}
    for meta_path in sorted((evt.transcript_path.with_suffix("") / "subagents").glob("agent-*.meta.json")):
        meta = json.loads(meta_path.read_text())
        if not (name := meta.get("name")):
            continue
        agent_id = meta_path.name.removeprefix("agent-").removesuffix(".meta.json")
        teammate = meta["description"] if meta.get("teamName") else None
        if teammate is None and agent_id not in live_subagents:
            continue
        lane_transcript = meta_path.with_name(f"agent-{agent_id}.jsonl")
        if not (started := spawned_at(lane_transcript)):
            continue
        if name not in newest or started > newest[name][0]:
            newest[name] = (started, agent_id, lane_transcript, teammate)
    lanes = []
    for name, (_, agent_id, path, teammate) in sorted(newest.items(), key=lambda item: item[1][0], reverse=True):
        if teammate is not None:
            if not teammate_tasks[teammate]:
                continue
            teammate_tasks[teammate] -= 1
        if turn := latest_turn(path, sidechain=True):
            lanes.append(Lane(name, agent_id, turn))
    return lanes


def due_lanes(evt: BaseHookEvent, state: CompactionState, root: Turn, now: float) -> list[Lane]:
    lanes = live_lanes(evt)
    live = {lane.agent_id for lane in lanes}
    state.rotated = {agent_id: sent for agent_id, sent in state.rotated.items() if agent_id in live}
    state.overdue = [agent_id for agent_id in state.overdue if agent_id in live]
    line = rotate_tokens()
    due = [
        lane
        for lane in lanes
        if lane.turn.tokens >= line
        and root.at - lane.turn.at <= DORMANT_AFTER
        and lane.agent_id not in state.overdue
        and now - state.rotated.get(lane.agent_id, 0.0) >= ROTATE_GRACE_SECONDS
    ]
    return sorted(due, key=lambda lane: lane.turn.tokens, reverse=True)[:NUDGE_LANES]


def listed(lanes: list[Lane]) -> str:
    return ", ".join(f"`{lane.name}` ({lane.turn.tokens:,})" for lane in lanes)


def rotation_nudge(lanes: list[Lane]) -> str:
    return (
        f"Lane rotation (advisory): {listed(lanes)} passed the {rotate_tokens():,}-token rotation line. "
        "When each reaches a natural pause, rotate it per the long-running skill's Lane rotation protocol, "
        "starting with its ROTATE message; a lane that has not replied `flushed` keeps running. "
        f"The hook names at most {NUDGE_LANES} lanes per nudge and repeats a lane at most once, "
        f"{ROTATE_GRACE_SECONDS // 60} minutes later."
    )


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
        "(ledger, rulings log, notes). "
        f"Then rewrite `{plan}` with one Write: dump all context needed on restart, "
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


@on(
    Event.UserPromptSubmit | Event.PostToolUse,
    skip_if=[FromSubagent()],
    tests={
        Input(prompt="continue", state=[CompactionState(active=True, nudge=PENDING_NUDGE)]): Warn(
            pattern=r"^Lane rotation \(advisory\): `landing-desk` \(430,000\) passed the 400,000-token rotation line\.$"
        ),
        Input(tool="Bash", tool_input={"command": "ls"}, state=[CompactionState(active=True, nudge=PENDING_NUDGE)]): Warn(
            pattern=r"^Lane rotation \(advisory\): `landing-desk`"
        ),
        Input(prompt="continue", state=[CompactionState(active=True)]): Allow(),
        Input(
            prompt="continue", state=[CompactionState(active=True, nudge=PENDING_NUDGE, phase="rewriting")]
        ): Allow(),
        Input(
            tool="Bash",
            tool_input={"command": "ls"},
            agent_id="a1b2c3",
            state=[CompactionState(active=True, nudge=PENDING_NUDGE)],
        ): Allow(),
    },
)
def deliver_nudge(evt: BaseHookEvent) -> HookResult | None:
    with CompactionState.mutate(evt) as state:
        if not state.nudge or state.phase != "idle":
            return None
        nudge, state.nudge = state.nudge, None
    return evt.context(nudge)


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
        Input(tool="Write", file="/home/u/.claude/plans/brook.2026-09-24-1630-pre-compact.md", content="# old"): Allow(),
    },
)
def track_plan(evt: BaseHookEvent) -> HookResult | None:
    path = evt.file.path
    if path.match(".claude/plans/*.md") and not path.name.endswith(ARCHIVE_SUFFIX):
        with CompactionState.mutate(evt) as state:
            state.plan_path = str(path)
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
                    phase="rewriting",
                )
            ],
        ): Warn(
            pattern=r"^Compacted long-running session\. Read `/p/brook\.md` .*The compaction handoff directive is "
            r"still pending: rewrite `/p/brook\.md` with one Write \(the previous plan is archived at "
            r"`/p/brook\.2026-09-24-163000-pre-compact\.md`\), then end your turn; the hook runs /compact\.$"
        ),
        Input(
            source="compact", state=[CompactionState(active=True, plan_path="/p/brook.md", phase="declined")]
        ): Warn(pattern=r"^Compacted long-running session\. Read `/p/brook\.md` before anything else.*context\.$"),
        Input(
            source="compact",
            state=[CompactionState(active=True, plan_path="/p/long-running-01234567.md", phase="rewriting")],
        ): Warn(pattern=r"still pending: rewrite `/p/long-running-01234567\.md` with one Write, then end your turn"),
        Input(source="compact", state=[CompactionState(plan_path="/p/brook.md")]): Allow(),
        Input(source="startup", state=[CompactionState(active=True, plan_path="/p/brook.md")]): Allow(),
    },
)
def reground_after_compact(evt: BaseHookEvent) -> HookResult | None:
    state = CompactionState.load(evt)
    if model := evt._raw.get("model"):
        state.model = model
    if evt.source == "compact" and state.phase in ("compacting", "declined"):
        state.phase = "idle"
    state.save(evt)
    if evt.source == "compact" and state.active and state.plan_path:
        return evt.context(
            f"Compacted long-running session. Read `{state.plan_path}` before anything else; it supersedes the summary. "
            "The long-running skill stays active — reload its rules (Skill `long-running`) if they are not in context."
            + (pending_rewrite(state) if state.phase == "rewriting" else "")
        )
    return None


def pending_rewrite(state: CompactionState) -> str:
    archived = f" (the previous plan is archived at `{state.archive_path}`)" if state.archive_path else ""
    return (
        f" The compaction handoff directive is still pending: rewrite `{state.plan_path}` with one Write{archived}, "
        "then end your turn; the hook runs /compact."
    )


def begin_handoff(evt: BaseHookEvent, state: CompactionState, root: Turn) -> HookResult | None:
    limit = threshold(root.model, state.model, evt.cwd)
    if root.tokens < FIRE_FRACTION * limit:
        return None
    now = datetime.now(UTC)
    if state.plan_path:
        plan = Path(state.plan_path).expanduser()
        archive = plan.with_name(f"{plan.stem}.{now:%Y-%m-%d-%H%M%S}{ARCHIVE_SUFFIX}")
        with plan.open("rb") as source, archive.open("xb") as target:
            shutil.copyfileobj(source, target)
    else:
        plan = Path.home() / ".claude" / "plans" / f"long-running-{evt.session_id[:8]}.md"
        archive = None
    archives = sorted(plan.parent.glob(f"{plan.stem}.*{ARCHIVE_SUFFIX}"), reverse=True)
    state.plan_path = str(plan)
    state.archive_path = str(archive) if archive else None
    state.phase = "rewriting"
    state.save(evt)
    return evt.block(
        directive(
            used=root.tokens,
            limit=limit,
            archive=archive,
            plan=plan,
            archives=archives,
            now=f"{now:%Y-%m-%d %H:%M}",
        )
    )


def nudge_rotation(evt: BaseHookEvent, state: CompactionState, root: Turn) -> None:
    now = time.time()
    if now - state.nudged_at < NUDGE_INTERVAL_SECONDS:
        return
    if lanes := due_lanes(evt, state, root, now):
        for lane in lanes:
            if lane.agent_id in state.rotated:
                state.overdue.append(lane.agent_id)
            state.rotated[lane.agent_id] = now
        state.nudged_at = now
        state.nudge = rotation_nudge(lanes)
    state.save(evt)


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
        state.phase = "declined"
        state.save(evt)
        return evt.allow(
            system_message=f"Long-running compaction handoff skipped: `{state.plan_path}` was not rewritten after "
            "the directive. Run /compact yourself; the hook stays quiet until the next compaction."
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
            state=[CompactionState(active=True)],
        ): Allow(),
        Input(
            transcript=FIXTURES / "usage-460k.jsonl",
            state=[CompactionState(active=True, model="claude-opus-5-5[1m]")],
        ): Allow(),
        Input(
            transcript=FIXTURES / "usage-262k-fable-5-1.jsonl",
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, model="claude-fable-5-1")],
        ): Allow(),
        Input(
            transcript=FIXTURES / "usage-460k.jsonl",
            session_id="0123456789abcdef",
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, model="claude-fable-5-1")],
        ): Block(pattern=r"^Context is at 460,000 of the 567,000-token auto-compaction threshold \(81%\)"),
        Input(
            transcript=FIXTURES / "usage-170k-sonnet-4-6.jsonl",
            session_id="0123456789abcdef",
            state=[CompactionState(active=True)],
        ): Block(pattern=r"^Context is at 170,000 of the 167,000-token .*rewrite `\S+/\.claude/plans/long-running-01234567\.md`"),
        Input(
            transcript=FIXTURES / "usage-170k-sonnet-4-6.jsonl",
            session_id="0123456789abcdef",
            state=[CompactionState(active=True, model="claude-opus-5-5[1m]")],
        ): Block(pattern=r"^Context is at 170,000 of the 167,000-token "),
        Input(
            transcript=FIXTURES / "usage-170k-sonnet-4-6.jsonl",
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, model="claude-sonnet-4-6[1m]")],
        ): Allow(),
        Input(
            transcript=FIXTURES / "usage-170k-synthetic-tail.jsonl",
            session_id="0123456789abcdef",
            state=[CompactionState(active=True)],
        ): Block(pattern=r"^Context is at 170,000 of the 167,000-token "),
        Input(
            transcript=FIXTURES / "usage-460k.jsonl",
            cwd=str(FIXTURES / "project-local"),
            session_id="0123456789abcdef",
            state=[CompactionState(active=True, model="claude-opus-5-5[1m]")],
        ): Block(pattern=r"^Context is at 460,000 of the 267,000-token auto-compaction threshold \(172%\)"),
        Input(
            transcript=FIXTURES / "usage-100k.jsonl",
            session_id="0123456789abcdef",
            env={"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "50000"},
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, model="claude-opus-5-5[1m]")],
        ): Block(pattern=r"^Context is at 100,000 of the 67,000-token "),
        Input(
            transcript=FIXTURES / "usage-800k.jsonl",
            session_id="0123456789abcdef",
            env={"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "2000000"},
            state=[CompactionState(active=True)],
        ): Block(pattern=r"^Context is at 800,000 of the 967,000-token "),
        Input(
            transcript=FIXTURES / "usage-170k-sonnet-4-6.jsonl",
            session_id="0123456789abcdef",
            env={"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "2000000"},
            state=[CompactionState(active=True)],
        ): Block(pattern=r"^Context is at 170,000 of the 167,000-token "),
        Input(
            transcript=FIXTURES / "usage-460k.jsonl",
            session_id="0123456789abcdef",
            env={"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "50"},
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, model="claude-opus-5-5[1m]")],
        ): Block(pattern=r"^Context is at 460,000 of the 290,000-token "),
        Input(
            transcript=FIXTURES / "usage-460k.jsonl",
            session_id="0123456789abcdef",
            env={"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "99"},
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, model="claude-opus-5-5[1m]")],
        ): Block(pattern=r"^Context is at 460,000 of the 567,000-token "),
        Input(
            transcript=FIXTURES / "usage-460k.jsonl",
            session_id="0123456789abcdef",
            env={"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "0"},
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, model="claude-opus-5-5[1m]")],
        ): Block(pattern=r"^Context is at 460,000 of the 567,000-token "),
        Input(
            transcript=FIXTURES / "usage-460k.jsonl",
            session_id="0123456789abcdef",
            env={"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "150"},
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, model="claude-opus-5-5[1m]")],
        ): Block(pattern=r"^Context is at 460,000 of the 567,000-token "),
        Input(
            transcript=FIXTURES / "usage-460k.jsonl",
            session_id="0123456789abcdef",
            env={"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "nan"},
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, model="claude-opus-5-5[1m]")],
        ): Block(pattern=r"^Context is at 460,000 of the 567,000-token "),
        Input(
            file=FileFixture(home=True, name="brook.md", content="# brook\n"),
            transcript=FIXTURES / "usage-460k.jsonl",
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, model="claude-opus-5-5[1m]", plan_path="~/brook.md")],
        ): Block(
            pattern=r"^Context is at 460,000 of the 567,000-token auto-compaction threshold \(81%\)\. "
            r"Compaction handoff — do not enter plan mode\. The current plan is archived at "
            r"`(\S+/)brook\.\d{4}-\d\d-\d\d-\d{6}-pre-compact\.md`\. .*\(ledger, rulings log, notes\)\. "
            r"Then rewrite `\1brook\.md` with one Write.*"
            r"verbatim:\n- `\1brook\.\d{4}-\d\d-\d\d-\d{6}-pre-compact\.md`\nThen end your turn; the hook runs /compact\.$"
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
        ): Allow(
            system_message=r"^Long-running compaction handoff skipped: `\S+/same\.md` was not rewritten after the "
            r"directive\. Run /compact yourself; the hook stays quiet until the next compaction\.$"
        ),
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
        Input(
            transcript=FIXTURES / "usage-800k.jsonl",
            state=[CompactionState(active=True, plan_path=str(FIXTURES / "plans" / "same.md"), phase="declined")],
        ): Allow(),
        Input(
            transcript=LANES / "calm.jsonl",
            background_tasks=[DESK, REVIEWER, WATCHER, ARCHIVIST, SLEEPER],
            state=[CompactionState(active=True)],
        ): Allow(),
        Input(
            transcript=LANES / "calm.jsonl",
            background_tasks=[DESK],
            state=[CompactionState(active=True, rotated={DESK_ID: 0.0})],
        ): Allow(),
        Input(
            transcript=LANES / "calm.jsonl",
            agent_id="a1b2c3",
            background_tasks=[DESK],
            state=[CompactionState(active=True)],
        ): Allow(),
        Input(
            transcript=LANES / "full.jsonl",
            session_id="0123456789abcdef",
            cwd=str(FIXTURES / "project-600k"),
            background_tasks=[DESK],
            state=[CompactionState(active=True)],
        ): Block(
            pattern=r"(?s)^(?!.*(?:[Rr]otat|TaskUpdate|TaskStop))Context is at 460,000 of the 567,000-token .*"
            r"Then end your turn; the hook runs /compact\.$"
        ),
    },
)
def compaction_handoff(evt: BaseHookEvent) -> HookResult | None:
    state = CompactionState.load(evt)
    if not state.active:
        return None
    match state.phase:
        case "idle":
            if not (root := latest_turn(evt.transcript_path)):
                return None
            if result := begin_handoff(evt, state, root):
                return result
            nudge_rotation(evt, state, root)
            return None
        case "rewriting":
            return finish_handoff(evt, state)
        case "compacting" | "declined":
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
