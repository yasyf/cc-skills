from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
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

from .installed import script
from .nudges import queue_nudge
from .tests.handoff_fixtures import USAGE_262K_FABLE, USAGE_460K, USAGE_800K
from .turns import threshold, turn_of

SKILL_NAMES = ("long-running",)
PLAN_ARG = re.compile(r"[^\s`'\"]*\.claude/plans/[^\s/`'\"]+\.md")
POINTER_PREFIX = "- **Progress (read first after any compaction):**"
SLUG = re.compile(r"progress:([\w.-]+)")
NO_REGISTER = {f"{sys.executable} {script('rulings.py')} register": "null"}
VIOLATIONS = 3
SEVERAL_ACTIVE = 4
OVERSIZED = 5
GENERATE_TIMEOUT_SECONDS = 120
GENERATED_STEM = "-generated"
GENERATED_TITLE = "(generated)"
FRESH_SECONDS = 300
FIXTURES = Path(__file__).parent / "tests" / "fixtures"
GENERATED_STUB = json.dumps(
    {
        "id": "d" * 40,
        "file": "/p/brook-progress/x-generated.md",
        "register": "e" * 40,
        "fresh": False,
        "digest": "Compacted long-running drive `brook`.",
    }
)
FIRE_FRACTION = 0.8
TURN_WINDOW = 256
ORCA_UNSET = "The handoff is recorded, but the hook cannot type `/compact` here. Run `/compact` now."
CCN_TIMEOUT_SECONDS = 20
COMPACT_RETRY_SECONDS = 33 * 60
RESTORE_BUDGET = 2000
SHORT = 7
REGISTER_CONTEXT_CHARS = 9000
REGISTER_FENCE = "~" * 12


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
    background: list[dict] = []
    digest: str | None = None
    failure: str | None = None
    generated_at: float | None = None
    active_doc: str | None = None
    generated_doc: str | None = None
    compacted_at: float | None = None
    register_doc: str | None = None
    register_body: str | None = None


def ccn(cwd: str, *args: str) -> subprocess.CompletedProcess[str]:
    argv = ["ccn", "-R", cwd, *args]
    return subprocess.run(argv, capture_output=True, text=True, timeout=CCN_TIMEOUT_SECONDS)


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


def session_json(evt: BaseHookEvent, state: CompactionState) -> str:
    tasks = [{"id": task.id, "status": task.status, "subject": task.subject} for task in evt.tasks.open]
    return json.dumps({"session_id": evt.session_id, "tasks": tasks, "background": state.background})


def generate(evt: BaseHookEvent, state: CompactionState, *args: str) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(script("handoff.py")), "generate", "--program", state.slug or "", "--plan", state.plan_path or ""]
    argv += ["--session", "-", "--repo", evt.cwd, *(["--folder"] if state.store == "folder" else []), *args]
    if state.generated_doc:
        argv += ["--generated-doc", state.generated_doc]
    if state.compacted_at:
        argv += ["--fresh-since", datetime.fromtimestamp(state.compacted_at, timezone.utc).isoformat()]
    return subprocess.run(
        argv, input=session_json(evt, state), capture_output=True, text=True, timeout=GENERATE_TIMEOUT_SECONDS, cwd=evt.cwd
    )


def adopt(state: CompactionState, generated: subprocess.CompletedProcess[str]) -> None:
    if generated.returncode:
        state.digest = state.active_doc = None
        state.failure = next(iter((generated.stderr or generated.stdout).strip().splitlines()[-1:]), f"exit {generated.returncode}")
        return
    result = json.loads(generated.stdout)
    state.digest, state.failure, state.generated_at = result["digest"], None, time.time()
    if result["fresh"] and state.phase == "due":
        state.phase = "written"
    state.active_doc = result["id"]
    state.register_doc = result["register"]
    if result["id"]:
        state.generated_doc = result["id"]
        point_doc(state, result["id"])
    else:
        point_file(state, Path(result["file"]))


def resume_steps(state: CompactionState) -> str:
    plan = Path(state.plan_path or "")
    if state.store == "folder":
        return f"then the newest file in `{progress_folder(plan)}/`"
    if state.active_doc:
        return f"then the active progress doc: `ccn doc show {state.active_doc[:8]}`"
    return f"then the progress doc: `ccn doc list --label progress:{state.slug}`, then `ccn doc show <id>`"


def newest_doc(state: CompactionState, cwd: str) -> dict | None:
    return max(progress_docs(state, cwd) or [], key=lambda doc: doc["updated_at"], default=None)


def edited_since_generation(doc: dict, generated_at: float) -> bool:
    return datetime.fromisoformat(doc["updated_at"]).timestamp() > generated_at


def handed_off(state: CompactionState, cwd: str) -> bool:
    if not (state.generated_at and time.time() - state.generated_at < FRESH_SECONDS):
        return False
    newest = newest_doc(state, cwd) if state.store == "ccn" else None
    return newest is None or (newest["id"] == state.active_doc and not edited_since_generation(newest, state.generated_at))


def newest_record(state: CompactionState, cwd: str) -> tuple[str, str] | None:
    if state.store == "folder":
        files = progress_folder(Path(state.plan_path or "")).glob("*.md")
        if not (path := max(files, key=lambda path: path.stat().st_mtime, default=None)):
            return None
        return f"`{path}`", path.read_text()
    if not (doc := newest_doc(state, cwd)):
        return None
    shown = ccn(cwd, "doc", "show", doc["id"], "--json")
    return f"`ccn doc show {doc['id'][:SHORT]}`", json.loads(shown.stdout)["body"] if shown.returncode == 0 else ""


def resume_restore(state: CompactionState, cwd: str) -> str:
    if not (newest := newest_record(state, cwd)):
        return f"Read `{state.plan_path}` before anything else, {resume_steps(state)}; they supersede the conversation so far."
    pointer, body = newest
    head = (
        f"Resumed long-running drive `{state.slug}`. Before acting, read the progress record {pointer} "
        f"(it supersedes the conversation so far), then `{state.plan_path}`. "
        "Reload Skill `long-running` if its rules are gone."
    )
    room = RESTORE_BUDGET - len(head.encode()) - 1
    return f"{head}\n{body.encode()[:room].decode(errors='ignore')}".rstrip()


def compact_instructions(state: CompactionState) -> str:
    if state.store == "ccn" and state.active_doc:
        doc = state.active_doc[:8]
        return (
            f"Resume from `{state.plan_path}`, then `ccn doc show {doc}`; keep only in-flight details they lack. "
            f"Quote: active progress doc: {doc}; the id in this summary wins over any id captured earlier in the conversation."
        )
    failed = f"The generated handoff failed: {state.failure}. " if state.failure else ""
    return (
        f"{failed}Resume the drive from `{state.plan_path}` and its progress record: read the plan, "
        f"{resume_steps(state)}. Keep only in-flight details they lack."
    )


def register_context(evt: BaseHookEvent, register: dict) -> HookResult:
    name = register["id"][:SHORT]
    if len(register["body"]) > REGISTER_CONTEXT_CHARS:
        return evt.context(
            f"Standing rules register `{name}` is over the injection budget and binds this session; "
            f"read it in full with `ccn doc show {name}` before acting."
        )
    return evt.context(
        f"Standing rules register `{name}`, verbatim; it binds this session and every lane brief, and outranks any summary.",
        f"{REGISTER_FENCE}\n{register['body'].rstrip()}\n{REGISTER_FENCE}",
    )


def rulings(cwd: str, *args: str, stdin: str = "") -> str:
    argv = [sys.executable, str(script("rulings.py")), *args, "--repo", cwd]
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=CCN_TIMEOUT_SECONDS, check=True, cwd=cwd).stdout


def register_of(cwd: str, *which: str) -> dict | None:
    return json.loads(rulings(cwd, "register", *which))


def queue_register(state: CompactionState, cwd: str) -> None:
    if state.store == "ccn" and state.slug and (register := register_of(cwd, "--program", state.slug)):
        state.register_doc, state.register_body = register["id"], register["body"]


def handoff_nudge(state: CompactionState) -> str:
    if state.store == "folder":
        write = f"Write the drive's execution state to a new file in `{progress_folder(Path(state.plan_path or ''))}/`."
    else:
        write = (
            "Write the drive's execution state with "
            f'`ccn doc add "<drive>: progress" --label progress:{state.slug} --when "Resuming the <drive> drive: '
            'read after the plan" --body -`.'
        )
    return f"Context is near the auto-compaction threshold. {write}"


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
        f"(`ccn doc list --label progress:{state.slug}`, now `{doc_id[:8]}`; `ccn doc show <id>`). It is the only "
        "active one: a hand-written progress doc written for the coming compaction gains the generated sections "
        "in place, else the session's generated doc is edited in place; only this line's id changes.",
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
        if fresh := [doc for doc in docs if doc["id"] not in state.prior and not doc["title"].endswith(GENERATED_TITLE)]:
            narrative = ["--narrative-doc", max(fresh, key=lambda doc: doc["updated_at"])["id"]]
        elif state.generated_at and any(doc["id"] == state.active_doc and edited_since_generation(doc, state.generated_at) for doc in docs):
            narrative = []
        else:
            return False
    generated = generate(evt, state, *narrative, "--strict")
    if generated.returncode == VIOLATIONS:
        return (
            "The drive's handoff fails the standing-rules lint. "
            f"Fix each finding at its file and line, then stop again:\n{generated.stdout.strip()}"
        )
    if generated.returncode == SEVERAL_ACTIVE:
        return f"The drive's handoff left more than one active progress doc. Fix it, then stop again:\n{generated.stdout.strip()}"
    if generated.returncode == OVERSIZED:
        return f"The drive's progress record is over its size cap. Trim the section it names, then stop again:\n{generated.stdout.strip()}"
    adopt(state, generated)
    return state.phase == "written"


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
    transcript_events=TURN_WINDOW,
    tests={
        Input(tool="Bash", tool_input={"command": "ls"}, transcript=USAGE_460K): Allow(),
        Input(
            tool="Bash",
            tool_input={"command": "ls"},
            transcript=USAGE_262K_FABLE,
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, model="claude-fable-5-1")],
        ): Allow(),
        Input(
            tool="Bash",
            tool_input={"command": "ls"},
            file=FileFixture(home=True, name="brook.md", content="# brook\n"),
            transcript=USAGE_460K,
            cwd=str(FIXTURES / "project-600k"),
            state=[CompactionState(active=True, plan_path="~/brook.md")],
        ): Allow(),
        Input(
            tool="Bash",
            tool_input={"command": "ls"},
            transcript=USAGE_800K,
            agent_id="a1b2c3",
            state=[CompactionState(active=True)],
        ): Allow(),
    },
)
def nudge_at_threshold(evt: BaseHookEvent) -> HookResult | None:
    with CompactionState.mutate(evt) as state:
        if (
            not state.active
            or state.phase != "idle"
            or not (root := turn_of(evt.ctx.t.events))
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
        queue_nudge(evt, handoff_nudge(state))
    return None


@on(
    Event.SessionStart,
    tests={
        Input(
            source="compact",
            commands=NO_REGISTER,
            state=[CompactionState(active=True, plan_path="/p/brook.md", slug="brook", phase="compacting")],
        ): Warn(
            pattern=r"^Read `/p/brook\.md` before anything else, then the progress "
            r"doc: `ccn doc list --label progress:brook`, then `ccn doc show <id>`; .*Skill `long-running` .*context\.$"
        ),
        Input(
            source="compact",
            commands=NO_REGISTER,
            state=[CompactionState(active=True, plan_path="/p/brook.md", slug="brook", phase="due")],
        ): Warn(
            pattern=r"^Read `/p/brook\.md` .* Your narrative was not written before compaction; "
            r"write a progress record when convenient\.$"
        ),
        Input(
            source="compact", transcript=USAGE_460K, state=[CompactionState(plan_path="/p/brook.md")]
        ): Allow(),
        Input(
            source="compact", commands=NO_REGISTER,
            state=[CompactionState(active=True, plan_path="/p/brook.md", slug="brook")]
        ): Warn(pattern=r"^Read `/p/brook\.md` before anything else"),
        Input(source="startup", state=[CompactionState(active=True, plan_path="/p/brook.md")]): Allow(),
        Input(source="resume", state=[CompactionState(plan_path="/p/brook.md", slug="brook")]): Allow(),
        Input(
            source="resume",
            commands=NO_REGISTER,
            state=[CompactionState(active=True, plan_path="/p/brook.md", slug="brook", store="folder")],
        ): Warn(pattern=r"^Read `/p/brook\.md` before anything else, then the newest file in `/p/brook-progress/`; "),
        Input(
            source="compact",
            commands=NO_REGISTER,
            state=[CompactionState(active=True, plan_path="/p/brook.md", slug="brook", digest="Compacted long-running drive `brook`.")],
        ): Warn(pattern=r"^Compacted long-running drive `brook`\.$"),
        Input(
            source="compact",
            commands=NO_REGISTER,
            state=[CompactionState(active=True, plan_path="/p/brook.md", slug="brook", failure="ccn: timed out")],
        ): Warn(pattern=r"The generated handoff failed; write the progress record now\.$"),
    },
)
def reground(evt: BaseHookEvent) -> HookResult | None:
    with CompactionState.mutate(evt) as state:
        if model := evt._raw.get("model"):
            state.model = model
        if evt.source == "resume" and state.active and state.plan_path:
            resolve_record(state, evt.cwd)
            return evt.context(resume_restore(state, evt.cwd))
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
        state.compacted_at = time.time()
        state.digest = state.failure = None
        if not (state.active and state.plan_path):
            return None
        resolve_record(state, evt.cwd)
        queue_register(state, evt.cwd)
    if digest:
        return evt.context(digest + pending)
    follow_up = (
        " The generated handoff failed; write the progress record now."
        if failure
        else pending or " Reload the long-running rules with Skill `long-running` if they are not in context."
    )
    return evt.context(
        f"Read `{state.plan_path}` before anything else, {resume_steps(state)}; they supersede the compaction summary."
        + follow_up
    )


@on(
    Event.PostToolUse | Event.UserPromptSubmit,
    skip_if=[FromSubagent()],
    tests={
        Input(
            tool="Bash",
            tool_input={"command": "ls"},
            state=[CompactionState(active=True, register_doc="e" * 40, register_body="# Register\n\n1. Pulumi state is the only truth.\n")],
        ): Warn(
            pattern=r"^Standing rules register `eeeeeee`, verbatim; it binds this session and every lane brief, and outranks any summary\.\n"
            r"~{12}\n# Register\n\n1\. Pulumi state is the only truth\.\n~{12}$"
        ),
        Input(prompt="continue", state=[CompactionState(active=True, register_doc="e" * 40, register_body="1. rule\n" * 2000)]): Warn(
            pattern=r"^Standing rules register `eeeeeee` is over the injection budget and binds this session; "
            r"read it in full with `ccn doc show eeeeeee` before acting\.$"
        ),
        Input(tool="Bash", tool_input={"command": "ls"}, state=[CompactionState(active=True)]): Allow(),
        Input(
            tool="Bash",
            tool_input={"command": "ls"},
            agent_id="a1b2c3",
            state=[CompactionState(active=True, register_doc="e" * 40, register_body="1. rule\n")],
        ): Allow(),
    },
)
def deliver_register(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).register_body:
        return None
    with CompactionState.mutate(evt) as state:
        body, state.register_body = state.register_body, None
        name = state.register_doc or ""
    return register_context(evt, {"id": name, "body": body}) if body else None


def send_compact(handle: str, instructions: str, transcript: Path) -> None:
    subprocess.Popen(
        [sys.executable, str(script("compact_job.py")), handle, f"/compact {instructions}", str(transcript)],
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
    transcript_events=1,
    tests={
        Input(transcript=USAGE_800K, state=[CompactionState(active=True)]): Allow(),
        Input(state=[CompactionState(active=True, plan_path="/p/brook.md", slug="brook", phase="written")]): Allow(
            system_message=r"^The handoff is recorded, but the hook cannot type `/compact` here\. Run `/compact` now\.$"
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
            return evt.allow(system_message=ORCA_UNSET)
        state.compacting_since = time.time()
        send_compact(handle, compact_instructions(state), evt.ctx.t.path)
    return None


@on(
    Event.PreCompact,
    skip_if=[FromSubagent()],
    tests={
        Input(
            session_id="s1",
            file=FileFixture(home=True, name="brook.md", content="# brook\n"),
            state=[CompactionState(active=True, plan_path="~/brook.md", slug="brook")],
            commands={f"{sys.executable} {script('handoff.py')} generate": GENERATED_STUB},
        ): Warn(
            pattern=r"^Resume from `~/brook\.md`, then `ccn doc show dddddddd`; keep only in-flight details they lack\. "
            r"Quote: active progress doc: dddddddd; "
            r"the id in this summary wins over any id captured earlier in the conversation\.$"
        ),
        Input(transcript=USAGE_460K, state=[CompactionState(plan_path="/p/brook.md")]): Allow(),
    },
)
def compaction_instructions(evt: BaseHookEvent) -> HookResult | None:
    with CompactionState.mutate(evt) as state:
        if not (state.active and state.plan_path):
            return None
        resolve_record(state, evt.cwd)
        if not handed_off(state, evt.cwd):
            adopt(state, generate(evt, state))
    return evt.context(compact_instructions(state))
