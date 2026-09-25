from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
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
PLAN_ARG = re.compile(r"[^\s`'\"]*\.claude/plans/[^\s/`'\"]+\.md")
LEADING_FLOAT = re.compile(r"\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
ARCHIVE_SUFFIX = "-pre-compact.md"
SYNTHETIC_MODEL = "<synthetic>"
FIXTURES = Path(__file__).parent / "tests" / "fixtures"
LANES = FIXTURES / "lanes" / "projects" / "p"
REVIEWER = {"id": "a0d0d0d0d0d0d0d0d", "type": "subagent", "status": "running", "description": "lane"}

WINDOW_FLOOR = 100_000
WINDOW_CEILING = 1_000_000
DEFAULT_WINDOW = 200_000
OUTPUT_RESERVE = 20_000
AUTOCOMPACT_BUFFER = 13_000
FIRE_FRACTION = 0.8
MAX_REMINDERS = 2
GAVE_UP = MAX_REMINDERS + 1
LANE_ROTATE_TOKENS = 200_000


@workflow_state("long_running_compaction")
class CompactionState(WorkflowState):
    active: bool = False
    model: str | None = None
    plan_path: str | None = None
    phase: Literal["idle", "rewriting", "compacting"] = "idle"
    archive_path: str | None = None
    reminders: int = 0
    rotated: dict[str, int] = {}


@dataclass(frozen=True)
class Lane:
    name: str
    agent_id: str
    tokens: int


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


def pct_override() -> float | None:
    raw = reqenv.getenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE") or ""
    if (match := LEADING_FLOAT.match(raw)) and 0 < (pct := float(match[0])) <= 100:
        return pct
    return None


def threshold(model: str | None, project: Path | None) -> int:
    cap = model_window(model)
    window = min(configured_window(project) or cap, cap)
    limit = window - OUTPUT_RESERVE - AUTOCOMPACT_BUFFER
    if pct := pct_override():
        limit = min(int((window - OUTPUT_RESERVE) * pct / 100), limit)
    return limit


def used_tokens(transcript: Path, *, sidechain: bool = False) -> int:
    for line in reversed(transcript.read_text().splitlines()):
        entry = json.loads(line)
        if (
            entry.get("type") == "assistant"
            and entry.get("isSidechain", False) == sidechain
            and entry["message"].get("model") != SYNTHETIC_MODEL
            and (usage := entry["message"].get("usage"))
        ):
            return usage["input_tokens"] + usage["cache_creation_input_tokens"] + usage["cache_read_input_tokens"]
    return 0


def spawned_at(transcript: Path) -> str | None:
    if not transcript.is_file():
        return None
    with transcript.open() as lines:
        first = next(lines, None)
    return json.loads(first)["timestamp"] if first else None


def team_members(config_dir: Path, team: str) -> set[str]:
    config = config_dir / "teams" / team / "config.json"
    return {member["name"] for member in json.loads(config.read_text())["members"]} if config.is_file() else set()


def live_lanes(evt: BaseHookEvent) -> list[Lane]:
    transcript = evt.transcript_path
    live_subagents = {task.id for task in evt.background_tasks if task.type == "subagent"}
    newest: dict[str, tuple[str, str, Path]] = {}
    for meta_path in sorted((transcript.with_suffix("") / "subagents").glob("agent-*.meta.json")):
        meta = json.loads(meta_path.read_text())
        if not (name := meta.get("name")):
            continue
        agent_id = meta_path.name.removeprefix("agent-").removesuffix(".meta.json")
        if team := meta.get("teamName"):
            if name not in team_members(transcript.parents[2], team):
                continue
        elif agent_id not in live_subagents:
            continue
        lane_transcript = meta_path.with_name(f"agent-{agent_id}.jsonl")
        if not (started := spawned_at(lane_transcript)):
            continue
        if name not in newest or started > newest[name][0]:
            newest[name] = (started, agent_id, lane_transcript)
    return [
        Lane(name, agent_id, used_tokens(path, sidechain=True))
        for name, (_, agent_id, path) in sorted(newest.items())
    ]


def scan_lanes(evt: BaseHookEvent, state: CompactionState) -> list[Lane]:
    lanes = live_lanes(evt)
    live = {lane.agent_id for lane in lanes}
    state.rotated = {agent_id: sent for agent_id, sent in state.rotated.items() if agent_id in live}
    return [lane for lane in lanes if lane.tokens >= LANE_ROTATE_TOKENS]


def listed(lanes: list[Lane]) -> str:
    return ", ".join(f"`{lane.name}` ({lane.tokens:,})" for lane in lanes)


def rotation_protocol(lanes: list[Lane]) -> str:
    return (
        f"Live lanes at or over the {LANE_ROTATE_TOKENS:,}-token rotation line: {listed(lanes)}. "
        "Rotate each before ending this turn (long-running skill, Lane rotation): SendMessage it "
        '`ROTATE: record anything not yet in the ledger or cc-notes, reply "flushed <ledger id>", then stop.`; '
        "on `flushed`, TaskStop it first, then spawn a fresh lane with the Agent tool under the same name, with its "
        "original spawn brief plus the ledger id. Never SendMessage the stopped lane: that resumes the same transcript "
        "and reloads its whole history. A pr-watcher needs no flush: TaskStop it and respawn it with the same inputs. "
        "A lane with nothing left to do is TaskStopped, not respawned."
    )


def compact_instructions(plan: str) -> str:
    return (
        f"Long-running compaction handoff. `{plan}` is the authoritative restart state; "
        "keep only in-flight details from the last turn that it lacks."
    )


def directive(
    *, used: int, limit: int, archive: Path | None, plan: Path, archives: list[Path], lanes: list[Lane], now: str
) -> str:
    archived = f"The current plan is archived at `{archive}`. " if archive else ""
    rotation = f"{rotation_protocol(lanes)} Record each new agent id in `## Restart here`.\n" if lanes else ""
    bullets = "\n".join(f"- `{path}`" for path in archives)
    return (
        f"Context is at {used:,} of the {limit:,}-token auto-compaction threshold ({round(100 * used / limit)}%). "
        f"Compaction handoff — do not enter plan mode. {archived}"
        "Before rewriting, record any durable state still living only in this conversation in cc-notes "
        "(ledger, rulings log, notes), then delete every completed task with TaskUpdate status `deleted`. "
        f"Then rewrite `{plan}` with one Write: dump all context needed on restart, "
        "get rid of everything unnecessary (finished work, superseded state, anything the archives already hold). "
        f"Use exactly these sections: `# <title> (compacted {now}Z)`, `## Restart here (read first)`, "
        "`## Mandate (owner, verbatim)`, `## Standing constraints`, `## End state`, "
        f"`## Owner decisions (never re-ask)`, `## State at {now}Z`, `## Live lanes`, `## Owed follow-ups`, "
        "`## Owner actions pending`, `## Key notes`, `## Done means`, "
        f"`## Archived plans (history only, never needed to restart)` with these bullets verbatim:\n{bullets}\n"
        f"{rotation}Then end your turn; the hook runs /compact."
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


def activate(evt: BaseHookEvent, args: str) -> None:
    state = CompactionState.load(evt)
    state.active = True
    if match := PLAN_ARG.search(args):
        state.plan_path = str(Path(match[0]).expanduser())
    state.save(evt)


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
        state = CompactionState.load(evt)
        state.plan_path = str(path)
        state.save(evt)
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
                    archive_path="/p/brook.2026-09-24-1630-pre-compact.md",
                    phase="rewriting",
                    reminders=1,
                )
            ],
        ): Warn(
            pattern=r"^Compacted long-running session\. Read `/p/brook\.md` .*The compaction handoff directive is "
            r"still pending: rewrite `/p/brook\.md` with one Write \(the previous plan is archived at "
            r"`/p/brook\.2026-09-24-1630-pre-compact\.md`\), then end your turn; the hook runs /compact\.$"
        ),
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
    if evt.source == "compact" and state.phase == "compacting":
        state.phase = "idle"
        state.reminders = 0
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


def begin_handoff(evt: BaseHookEvent, state: CompactionState, lanes: list[Lane]) -> HookResult | None:
    used = used_tokens(evt.transcript_path)
    limit = threshold(state.model, evt.cwd)
    if used < FIRE_FRACTION * limit:
        return None
    now = datetime.now(UTC)
    if state.plan_path:
        plan = Path(state.plan_path).expanduser()
        archive = plan.with_name(f"{plan.stem}.{now:%Y-%m-%d-%H%M}{ARCHIVE_SUFFIX}")
        with plan.open("rb") as source, archive.open("xb") as target:
            shutil.copyfileobj(source, target)
    else:
        plan = Path.home() / ".claude" / "plans" / f"long-running-{evt.session_id[:8]}.md"
        archive = None
    archives = sorted(plan.parent.glob(f"{plan.stem}.*{ARCHIVE_SUFFIX}"), reverse=True)
    for lane in lanes:
        state.rotated.setdefault(lane.agent_id, 0)
    state.plan_path = str(plan)
    state.archive_path = str(archive) if archive else None
    state.phase = "rewriting"
    state.reminders = 0
    state.save(evt)
    return evt.block(
        directive(
            used=used,
            limit=limit,
            archive=archive,
            plan=plan,
            archives=archives,
            lanes=lanes,
            now=f"{now:%Y-%m-%d %H:%M}",
        )
    )


def rotate_lanes(evt: BaseHookEvent, state: CompactionState, lanes: list[Lane]) -> HookResult | None:
    fresh = [lane for lane in lanes if lane.agent_id not in state.rotated]
    stuck = [lane for lane in lanes if state.rotated.get(lane.agent_id, GAVE_UP) < MAX_REMINDERS]
    abandoned = [lane for lane in lanes if state.rotated.get(lane.agent_id) == MAX_REMINDERS]
    for lane in fresh:
        state.rotated[lane.agent_id] = 0
    for lane in stuck:
        state.rotated[lane.agent_id] += 1
    for lane in abandoned:
        state.rotated[lane.agent_id] = GAVE_UP
    state.save(evt)
    gave_up = (
        f"Long-running lane rotation gave up: {listed(abandoned)} still live at or over the "
        f"{LANE_ROTATE_TOKENS:,}-token line after {MAX_REMINDERS} reminders. Rotate or stop it by hand."
        if abandoned
        else None
    )
    reasons = [rotation_protocol(fresh)] if fresh else []
    if stuck:
        reasons.append(f"Still unrotated: {listed(stuck)}. Rotate per the Lane rotation protocol before ending this turn.")
    if reasons:
        return evt.block(" ".join(reasons), system_message=gave_up)
    return evt.allow(system_message=gave_up) if gave_up else None


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
            state=[CompactionState(active=True, model="claude-opus-5-5[1m]")],
        ): Block(pattern=r"^Context is at 800,000 of the 967,000-token "),
        Input(
            transcript=FIXTURES / "usage-170k.jsonl",
            session_id="0123456789abcdef",
            env={"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "2000000"},
            state=[CompactionState(active=True, model="claude-sonnet-5")],
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
            r"`(\S+/)brook\.\d{4}-\d\d-\d\d-\d{4}-pre-compact\.md`\. .*\(ledger, rulings log, notes\), then delete "
            r"every completed task with TaskUpdate status `deleted`\. Then rewrite `\1brook\.md` with one Write.*"
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
        Input(transcript=LANES / "calm.jsonl", state=[CompactionState(active=True)]): Block(
            pattern=r"^Live lanes at or over the 200,000-token rotation line: `landing-desk` \(230,000\)\. Rotate each "
            r"before ending this turn .*SendMessage it `ROTATE: record anything not yet in the ledger or cc-notes, "
            r'reply "flushed <ledger id>", then stop\.`; on `flushed`, TaskStop it first, then spawn a fresh lane .*'
            r"Never SendMessage the stopped lane: that resumes the same transcript"
        ),
        Input(transcript=LANES / "calm.jsonl", background_tasks=[REVIEWER], state=[CompactionState(active=True)]): Block(
            pattern=r"^Live lanes at or over the 200,000-token rotation line: "
            r"`landing-desk` \(230,000\), `reviewer` \(220,000\)\. Rotate each "
        ),
        Input(
            transcript=LANES / "calm.jsonl",
            state=[CompactionState(active=True, rotated={"alanding-desk-0a0a0a0a0a0a0a0a": 0})],
        ): Block(pattern=r"^Live lanes at or over the 200,000-token rotation line: `landing-desk` \(230,000\)\. "),
        Input(
            transcript=LANES / "calm.jsonl",
            state=[CompactionState(active=True, rotated={"alanding-desk-0b0b0b0b0b0b0b0b": 0})],
        ): Block(
            pattern=r"^Still unrotated: `landing-desk` \(230,000\)\. "
            r"Rotate per the Lane rotation protocol before ending this turn\.$"
        ),
        Input(
            transcript=LANES / "calm.jsonl",
            background_tasks=[REVIEWER],
            state=[CompactionState(active=True, rotated={"alanding-desk-0b0b0b0b0b0b0b0b": 1})],
        ): Block(
            pattern=r"^Live lanes at or over the 200,000-token rotation line: `reviewer` \(220,000\)\. .* "
            r"Still unrotated: `landing-desk` \(230,000\)\. Rotate per the Lane rotation protocol"
        ),
        Input(
            transcript=LANES / "calm.jsonl",
            state=[CompactionState(active=True, rotated={"alanding-desk-0b0b0b0b0b0b0b0b": MAX_REMINDERS})],
        ): Allow(
            system_message=r"^Long-running lane rotation gave up: `landing-desk` \(230,000\) still live at or over "
            r"the 200,000-token line after 2 reminders\."
        ),
        Input(
            transcript=LANES / "calm.jsonl",
            background_tasks=[REVIEWER],
            state=[
                CompactionState(
                    active=True,
                    rotated={"alanding-desk-0b0b0b0b0b0b0b0b": MAX_REMINDERS, "a0d0d0d0d0d0d0d0d": 0},
                )
            ],
        ): Block(
            pattern=r"^Still unrotated: `reviewer` \(220,000\)\.",
            system_message=r"^Long-running lane rotation gave up: `landing-desk` \(230,000\)",
        ),
        Input(
            transcript=LANES / "calm.jsonl",
            state=[CompactionState(active=True, rotated={"alanding-desk-0b0b0b0b0b0b0b0b": GAVE_UP, "gone": 0})],
        ): Allow(),
        Input(transcript=LANES / "calm.jsonl", background_tasks=[REVIEWER]): Allow(),
        Input(transcript=LANES / "calm.jsonl", agent_id="a1b2c3", state=[CompactionState(active=True)]): Allow(),
        Input(
            transcript=LANES / "full.jsonl",
            session_id="0123456789abcdef",
            state=[CompactionState(active=True)],
        ): Block(
            pattern=r"(?s)^Context is at 460,000 of the 167,000-token .*verbatim:\n.*Live lanes at or over the "
            r"200,000-token rotation line: `landing-desk` \(200,000\)\. Rotate each .*Record each new agent id in "
            r"`## Restart here`\.\nThen end your turn; the hook runs /compact\.$"
        ),
        Input(
            transcript=LANES / "calm.jsonl",
            state=[
                CompactionState(
                    active=True,
                    plan_path=str(FIXTURES / "plans" / "same.md"),
                    archive_path=str(FIXTURES / "plans" / "same.2026-09-24-1630-pre-compact.md"),
                    phase="rewriting",
                )
            ],
        ): Block(pattern=r"^Compaction handoff pending: rewrite `\S+/same\.md`"),
        Input(
            transcript=LANES / "calm.jsonl",
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
            lanes = scan_lanes(evt, state)
            return begin_handoff(evt, state, lanes) or rotate_lanes(evt, state, lanes)
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
