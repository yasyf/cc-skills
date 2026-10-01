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
LAUNCH = re.compile(rb'"skill":\s*"(?:long-running:)?long-running"|<command-name>/(?:long-running:)?long-running</command-name>')
PLAN_ARG =re.compile(r"[^\s`'\"]*\.claude/plans/[^\s/`'\"]+\.md")
POINTER_PREFIX = "- **Progress (read first after any compaction):**"
SLUG = re.compile(r"progress:([\w.-]+)")
DOC_SECTIONS = "how the drive runs; owner asks and their state; landed; waiting on the owner; root's next actions"
GENERATED = (
    "standing owner rules, durable answers, open asks, open tasks, lanes and monitors, inbox heads and cursors, "
    "and lint findings"
)
COMPACT_JOB = Path(__file__).with_name("compact_job.py")
SCRIPTS = Path(__file__).parents[2] / "skills" / "long-running" / "scripts"
STANDING = SCRIPTS / "standing.py"
HANDOFF = SCRIPTS / "handoff.py"
VIOLATIONS = 3
GENERATE_TIMEOUT_SECONDS = 120
GENERATED_STEM = "-generated"
GENERATED_TITLE = "(generated)"
FRESH_SECONDS = 300
FIXTURES = Path(__file__).parent / "tests" / "fixtures"
GENERATED_STUB = json.dumps({"id": "d" * 40, "file": "/p/brook-progress/x-generated.md", "digest": "Compacted long-running drive `brook`."})
FIRE_FRACTION = 0.8
CCN_TIMEOUT_SECONDS = 20
COMPACT_RETRY_SECONDS = MAX_LIFETIME_SECONDS + 60


@workflow_state("long_running_compaction")
class CompactionState(WorkflowState):
    active: bool = False
    model: str | None = None
    plan_path: str | None = None
    phase: Literal["idle", "due", "written", "compacting"] = "idle"
    store: Literal["ccn", "folder"] = "ccn"
    slug: str | None = None
    prior: list[str] = []
    compacting_since: float | None = None
    scanned: int = 0
    background: list[dict] = []
    digest: str | None = None
    failure: str | None = None
    generated_at: float | None = None


def launched(state: CompactionState, transcript: Path) -> bool:
    if not state.active:
        with transcript.open("rb") as file:
            file.seek(state.scanned)
            tail = file.read()
        state.scanned += tail.rfind(b"\n") + 1
        state.active = LAUNCH.search(tail) is not None
    return state.active


def ccn(cwd: str, *args: str) -> subprocess.CompletedProcess[str]:
    argv = ["ccn", "-R", cwd, *args]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=CCN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", "timed out")


def has_cc_notes(cwd: str) -> bool:
    return shutil.which("ccn") is not None and ccn(cwd, "doc", "list", "--limit", "1", "--json").returncode == 0


def slug_of(plan: Path) -> str:
    text = plan.read_text() if plan.exists() else ""
    pointer = next((line for line in text.splitlines() if line.startswith(POINTER_PREFIX)), "")
    return match[1] if (match := SLUG.search(pointer)) else plan.stem


def progress_folder(plan: Path) -> Path:
    return plan.with_name(f"{plan.stem}-progress")


def resolve_record(state: CompactionState, cwd: str) -> None:
    if state.plan_path and state.slug is None:
        state.slug = slug_of(Path(state.plan_path).expanduser())
        state.store = "ccn" if has_cc_notes(cwd) else "folder"


def progress_docs(state: CompactionState, cwd: str) -> list[dict] | None:
    listed = ccn(cwd, "doc", "list", "--label", f"progress:{state.slug}", "--json")
    return json.loads(listed.stdout or "[]") if listed.returncode == 0 else None


def records(state: CompactionState, cwd: str) -> list[str]:
    if state.store == "folder":
        return [path.name for path in progress_folder(Path(state.plan_path or "")).glob("*.md")]
    return [doc["id"] for doc in progress_docs(state, cwd) or []]


def standing(state: CompactionState, cwd: str, verb: str, *args: str) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(STANDING), verb, "--program", state.slug or "", "--repo", cwd, *args]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=CCN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", "timed out")


def session_json(evt: BaseHookEvent, state: CompactionState) -> str:
    tasks = [{"id": task.id, "status": task.status, "subject": task.subject} for task in evt.tasks.open]
    return json.dumps({"session_id": evt.session_id, "tasks": tasks, "background": state.background})


def generate(evt: BaseHookEvent, state: CompactionState, *args: str) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(HANDOFF), "generate", "--program", state.slug or "", "--plan", state.plan_path or ""]
    argv += ["--session", "-", "--repo", evt.cwd, *(["--folder"] if state.store == "folder" else []), *args]
    try:
        return subprocess.run(
            argv, input=session_json(evt, state), capture_output=True, text=True, timeout=GENERATE_TIMEOUT_SECONDS, cwd=evt.cwd
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", "timed out")


def adopt(state: CompactionState, generated: subprocess.CompletedProcess[str]) -> None:
    if generated.returncode:
        state.digest = None
        state.failure = next(iter((generated.stderr or generated.stdout).strip().splitlines()[-1:]), f"exit {generated.returncode}")
        return
    result = json.loads(generated.stdout)
    state.digest, state.failure, state.generated_at = result["digest"], None, time.time()
    if result["id"]:
        point_doc(state, result["id"])
    else:
        point_file(state, Path(result["file"]))


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
        f"When convenient, {write}, with sections: {DOC_SECTIONS}. The hook generates {GENERATED} from their "
        "sources and files your record beneath them as the root narrative, so write only what the sources lack. "
        f"Never rewrite `{plan}`. "
        "The hook supersedes the previous progress record, points the plan's one progress line at the generated "
        "one, and runs /compact once the input line is empty."
    )


def point_plan(plan: Path, line: str) -> None:
    plan = plan.expanduser()
    lines = plan.read_text().rstrip("\n").split("\n") if plan.exists() else []
    at = next((i for i, text in enumerate(lines) if text.startswith(POINTER_PREFIX)), None)
    if at is None:
        lines += ["", line]
    else:
        lines[at] = line
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("\n".join(lines) + "\n")


def point_doc(state: CompactionState, doc_id: str) -> None:
    point_plan(
        Path(state.plan_path or ""),
        f"{POINTER_PREFIX} the latest execution state is the active cc-notes doc labelled `progress:{state.slug}` "
        f"(`ccn doc list --label progress:{state.slug}`, now `{doc_id[:8]}`; `ccn doc show <id>`). Each "
        "handoff adds a new doc and supersedes the previous one, so history is the supersede chain; only this "
        "line's id changes.",
    )


def point_file(state: CompactionState, path: Path) -> None:
    point_plan(
        Path(state.plan_path or ""),
        f"{POINTER_PREFIX} the latest execution state is the newest file in `{path.parent}/`, "
        f"now `{path.name}`; only this line's name changes.",
    )


def record(state: CompactionState, evt: BaseHookEvent) -> bool | str:
    if state.store == "folder":
        folder = progress_folder(Path(state.plan_path or ""))
        fresh = [path for path in folder.glob("*.md") if path.name not in state.prior and not path.stem.endswith(GENERATED_STEM)]
        if not fresh:
            return False
        narrative = ["--narrative-file", str(max(fresh, key=lambda path: path.stat().st_mtime))]
    else:
        docs = progress_docs(state, evt.cwd) or []
        if not (fresh := [doc for doc in docs if doc["id"] not in state.prior and not doc["title"].endswith(GENERATED_TITLE)]):
            return False
        narrative = ["--narrative-doc", max(fresh, key=lambda doc: doc["updated_at"])["id"]]
    generated = generate(evt, state, *narrative, "--strict")
    if generated.returncode == VIOLATIONS:
        return (
            "The drive's handoff fails the standing-rules lint (long-running Compaction handoff); "
            f"make the edit each finding names at its file and line, then stop again:\n{generated.stdout.strip()}"
        )
    adopt(state, generated)
    return generated.returncode == 0


@on(
    Event.PostToolUse,
    only_if=[Tool("Skill")],
    skip_if=[FromSubagent()],
    tests={
        Input(
            tool="Skill",
            tool_input={"skill": "long-running:long-running", "args": "~/.claude/plans/x.md"},
            cwd=str(FIXTURES / "project-600k"),
        ): Allow(),
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
        Input(
            prompt="/long-running:long-running Continue the plan at ~/.claude/plans/x.md",
            cwd=str(FIXTURES / "project-600k"),
        ): Allow(),
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
            state.slug = None
            resolve_record(state, evt.cwd)


@on(
    Event.PostToolUse,
    only_if=[Tool("Write", "Edit")],
    skip_if=[FromSubagent()],
    tests={
        Input(
            tool="Write", file="/home/u/.claude/plans/brook.md", content="# plan", cwd=str(FIXTURES / "project-600k")
        ): Allow(),
    },
)
def track_plan(evt: BaseHookEvent) -> HookResult | None:
    path = evt.file.path
    if not path.match(".claude/plans/*.md"):
        return None
    with CompactionState.mutate(evt) as state:
        if state.plan_path != str(path):
            state.plan_path = str(path)
            state.slug = None
        resolve_record(state, evt.cwd)
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
        if (
            not launched(state, evt.transcript_path)
            or state.phase != "idle"
            or not (root := latest_turn(evt.transcript_path))
        ):
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
        state.slug = None
        resolve_record(state, evt.cwd)
        state.prior = records(state, evt.cwd)
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
            pattern=r"^Compacted long-running session\. .* Your narrative was not written before compaction; "
            r"write a progress record when convenient\.$"
        ),
        Input(
            source="compact", transcript=FIXTURES / "usage-460k.jsonl", state=[CompactionState(plan_path="/p/brook.md")]
        ): Allow(),
        Input(
            source="compact",
            transcript=FIXTURES / "launched-460k.jsonl",
            state=[CompactionState(plan_path="/p/brook.md", slug="brook")],
        ): Warn(pattern=r"^Compacted long-running session\. Read `/p/brook\.md` before anything else"),
        Input(source="startup", state=[CompactionState(active=True, plan_path="/p/brook.md")]): Allow(),
        Input(
            source="compact",
            state=[CompactionState(active=True, plan_path="/p/brook.md", slug="brook", digest="Compacted long-running drive `brook`.")],
        ): Warn(pattern=r"^Compacted long-running drive `brook`\.$"),
        Input(
            source="compact",
            state=[CompactionState(active=True, plan_path="/p/brook.md", slug="brook", failure="ccn: timed out")],
        ): Warn(pattern=r"The generated handoff failed before compaction \(ccn: timed out\); write the progress record now\.$"),
    },
)
def reground_after_compact(evt: BaseHookEvent) -> HookResult | None:
    with CompactionState.mutate(evt) as state:
        if model := evt._raw.get("model"):
            state.model = model
        if evt.source != "compact":
            return None
        pending = (
            " Your narrative was not written before compaction; write a progress record when convenient."
            if state.phase == "due"
            else ""
        )
        digest, failure = state.digest, state.failure
        state.phase = "idle"
        state.compacting_since = None
        state.digest = state.failure = None
        if not (launched(state, evt.transcript_path) and state.plan_path):
            return None
        resolve_record(state, evt.cwd)
    if digest:
        return evt.context(digest + pending)
    failed = f" The generated handoff failed before compaction ({failure}); write the progress record now." if failure else ""
    return evt.context(
        f"Compacted long-running session. Read `{state.plan_path}` before anything else, {resume_steps(state)}; "
        "they supersede the summary. "
        "The long-running skill stays active — reload its rules (Skill `long-running`) if they are not in context."
        + (failed or pending)
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


def compact_due(state: CompactionState, evt: BaseHookEvent) -> bool | str:
    if state.phase == "due":
        recorded = record(state, evt)
        if isinstance(recorded, str):
            return recorded
        if recorded:
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
        if not state.active:
            return None
        state.background = [{"type": task.type, "status": task.status, "description": task.description} for task in evt.background_tasks]
        if not state.plan_path or not (due := compact_due(state, evt)):
            return None
        if isinstance(due, str):
            return evt.block(due)
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
    skip_if=[FromSubagent()],
    tests={
        Input(
            session_id="s1",
            file=FileFixture(home=True, name="brook.md", content="# brook\n"),
            state=[CompactionState(active=True, plan_path="~/brook.md", slug="brook")],
            commands={f"{sys.executable} {STANDING} titles": "", f"{sys.executable} {HANDOFF} generate": GENERATED_STUB},
        ): Warn(
            pattern=r"^Long-running compaction handoff\. `~/brook\.md` and its progress record are the "
            r"authoritative restart state: read the plan, then the progress doc: `ccn doc list --label progress:brook`, then "
            r"`ccn doc show <id>`\. Keep only in-flight details from the last turn that they lack\.$"
        ),
        Input(
            session_id="s1",
            file=FileFixture(home=True, name="brook.md", content="# brook\n"),
            state=[CompactionState(active=True, plan_path="~/brook.md", slug="brook")],
            commands={
                f"{sys.executable} {STANDING} titles": "- 4ffc9a5 When does a merged change get released?\n",
                f"{sys.executable} {HANDOFF} generate": GENERATED_STUB,
            },
        ): Warn(
            pattern=r"(?s)they lack\.\nStanding owner rules \(scope:durable answers; keep every title verbatim in the "
            r"summary\):\n- 4ffc9a5 When does a merged change get released\?$"
        ),
        Input(transcript=FIXTURES / "usage-460k.jsonl", state=[CompactionState(plan_path="/p/brook.md")]): Allow(),
        Input(
            transcript=FIXTURES / "launched-460k.jsonl",
            session_id="s1",
            file=FileFixture(home=True, name="brook.md", content="# brook\n"),
            state=[CompactionState(plan_path="~/brook.md", slug="brook")],
            commands={f"{sys.executable} {STANDING} titles": "", f"{sys.executable} {HANDOFF} generate": GENERATED_STUB},
        ): Warn(pattern=r"^Long-running compaction handoff\. `~/brook\.md` and its progress record"),
    },
)
def compaction_instructions(evt: BaseHookEvent) -> HookResult | None:
    with CompactionState.mutate(evt) as state:
        if not (launched(state, evt.transcript_path) and state.plan_path):
            return None
        resolve_record(state, evt.cwd)
        if not (state.generated_at and time.time() - state.generated_at < FRESH_SECONDS):
            adopt(state, generate(evt, state))
        titles = standing(state, evt.cwd, "titles").stdout.strip() if state.store == "ccn" else ""
    rules = f"\nStanding owner rules (scope:durable answers; keep every title verbatim in the summary):\n{titles}" if titles else ""
    return evt.context(compact_instructions(state) + rules)
