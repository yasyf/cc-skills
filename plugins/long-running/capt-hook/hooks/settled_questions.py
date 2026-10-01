from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from captain_hook import (
    Allow,
    BaseHookEvent,
    Block,
    Event,
    FileFixture,
    FromSubagent,
    HookResult,
    Input,
    Tool,
    WorkflowState,
    on,
    workflow_state,
)

from .compaction_handoff import CompactionState, ccn

BURST_SECONDS = 10 * 60
DECISIONS = re.compile(r"^(#{2,3}) [^\n]*\bdecisions?\b[^\n]*$", re.IGNORECASE)
BOARD_VERBS = frozenset({"push", "update-block"})
WORD = re.compile(r"[a-z0-9][a-z0-9-]*")
SUFFIX = re.compile(r"(?:ies|s|ed|ing)$")
STOPWORDS = frozenset(
    "about after also because been before being both could does each either every from have here into just like make "
    "more most much need needs only over should some still such than that their them then there these they this those "
    "under were what when where whether which while will with would your".split()
)
SHARED_WORDS = 4
OVERLAP = 0.6
FEEDBACK = re.compile(r"^\s*type:\s*feedback\s*$", re.MULTILINE)
DESCRIPTION = re.compile(r"^description:\s*(.+)$", re.MULTILINE)
PLAN = (
    "# brook\n\n## Decisions (owner, binding)\n"
    "- Every side-feature carries over to the Go release with the same hard-fought UX; nothing is dropped.\n\n"
    "## Context\nprose\n"
)
SETTLED_ASK = {"questions": [{"question": "Does every side-feature carry over to the Go release, or are some dropped?"}]}
OPEN_ASK = {"questions": [{"question": "Which GitHub App gets read-only Checks and Commit statuses for the quota fix?"}]}


@workflow_state("long_running_settled_questions")
class SettledState(WorkflowState):
    blocked_at: float | None = None


@dataclass(frozen=True)
class Settled:
    text: str


def strings(value: object) -> Iterator[str]:
    match value:
        case str():
            yield value
        case dict():
            for item in value.values():
                yield from strings(item)
        case list():
            for item in value:
                yield from strings(item)


def board_questions(args: list[str], cwd: str) -> list[str]:
    found = []
    for arg in args:
        path = Path(cwd, Path(arg.split("=")[-1]).expanduser())
        if path.suffix == ".json" and path.is_file():
            found += [text for text in strings(json.loads(path.read_text())) if text.rstrip().endswith("?")]
    return found


def asked(evt: BaseHookEvent) -> list[str]:
    if evt.tool_name == "AskUserQuestion":
        return [question["question"] for question in evt._tool_input["questions"]]
    found = []
    for call in evt.command.calls():
        if Path(call.name).name != "cc-present" or not call.args or "--dry-run" in call.args:
            continue
        verb, *rest = call.args
        if verb in BOARD_VERBS or (verb == "start" and any(arg.split("=")[0] == "--doc" for arg in rest)):
            found += board_questions(rest, str(evt.cwd))
    return found


def plan_decisions(plan_path: str | None) -> list[Settled]:
    if not plan_path or not (plan := Path(plan_path).expanduser()).is_file():
        return []
    lines = plan.read_text().splitlines()
    if (start := next((i for i, line in enumerate(lines) if DECISIONS.match(line)), None)) is None:
        return []
    following = re.compile(rf"#{{1,{len(DECISIONS.match(lines[start])[1])}}} ")
    settled = []
    for number, line in enumerate(lines[start + 1 :], start + 2):
        if following.match(line):
            break
        if line.strip():
            settled.append(Settled(line.strip()))
    return settled


def durable_answers(cwd: str) -> list[Settled]:
    if shutil.which("ccn") is None:
        return []
    listed = ccn(cwd, "answer", "list", "--label", "scope:durable", "--limit", "0", "--json")
    if listed.returncode:
        return []
    return [Settled(answer["title"]) for answer in json.loads(listed.stdout or "[]")]


def memory_dirs(evt: BaseHookEvent) -> set[Path]:
    dirs = {evt.ctx.t.path.parent / "memory"} if evt.ctx.t.path else set()
    common = subprocess.run(
        ["git", "-C", str(evt.cwd), "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True
    )
    if common.returncode == 0:
        main = re.sub(r"[^A-Za-z0-9]", "-", str(Path(common.stdout.strip()).parent))
        dirs.add(Path.home() / ".claude" / "projects" / main / "memory")
    return dirs


def feedback_memories(evt: BaseHookEvent) -> list[Settled]:
    settled = []
    for path in sorted(path for memory in memory_dirs(evt) for path in memory.glob("*.md")):
        text = path.read_text()
        if FEEDBACK.search(text) and (description := DESCRIPTION.search(text)):
            settled.append(Settled(description[1].strip()))
    return settled


def stem(word: str) -> str:
    if len(word) <= 4:
        return word
    word = SUFFIX.sub(lambda suffix: "y" if suffix[0] == "ies" else "", word)
    return word.removesuffix("e")


def words(text: str) -> set[str]:
    return {stem(word) for word in WORD.findall(text.lower()) if len(word) > 3 and word not in STOPWORDS}


def matches(questions: list[str], settled: list[Settled]) -> list[tuple[str, Settled]]:
    indexed = [(item, words(item.text)) for item in settled]
    found = []
    for question in questions:
        asking = words(question)
        hits = [
            (shared, item)
            for item, known in indexed
            if (shared := len(asking & known)) >= SHARED_WORDS and shared >= OVERLAP * min(len(asking), len(known))
        ]
        if hits:
            found.append((question, max(hits, key=lambda hit: hit[0])[1]))
    return found


GATE_MESSAGE = (
    "This question matches a settled plan decision, owner answer, or feedback memory. "
    "Apply it and re-issue the call without the question."
)


@on(
    Event.PreToolUse,
    only_if=[Tool("AskUserQuestion", "Bash")],
    skip_if=[FromSubagent()],
    tests={
        Input(
            tool="AskUserQuestion",
            tool_input=SETTLED_ASK,
            file=FileFixture(home=True, name="brook.md", content=PLAN),
            state=[CompactionState(active=True, plan_path="~/brook.md")],
        ): Block(pattern=r"settled plan decision"),
        Input(
            tool="AskUserQuestion",
            tool_input=OPEN_ASK,
            file=FileFixture(home=True, name="brook.md", content=PLAN),
            state=[CompactionState(active=True, plan_path="~/brook.md")],
        ): Allow(),
        Input(
            tool="Bash",
            tool_input={"command": "cc-present start --session s --doc ~/owner-board.json --new"},
            file=FileFixture(home=True, name="owner-board.json", content=json.dumps({"blocks": [{"title": SETTLED_ASK["questions"][0]["question"]}]})),
            state=[CompactionState(active=True)],
        ): Allow(),
        Input(
            tool="AskUserQuestion",
            tool_input=SETTLED_ASK,
            file=FileFixture(home=True, name="brook.md", content=PLAN),
            state=[CompactionState(active=True, plan_path="~/brook.md"), SettledState(blocked_at=time.time())],
        ): Allow(),
        Input(tool="AskUserQuestion", tool_input=SETTLED_ASK): Allow(),
        Input(
            tool="Bash",
            tool_input={"command": "cc-present remove-block o1-card --session s"},
            state=[CompactionState(active=True)],
        ): Allow(),
        Input(
            tool="AskUserQuestion",
            tool_input=SETTLED_ASK,
            agent_id="a1b2c3",
            file=FileFixture(home=True, name="brook.md", content=PLAN),
            state=[CompactionState(active=True, plan_path="~/brook.md")],
        ): Allow(),
    },
)
def check_settled_before_asking(evt: BaseHookEvent) -> HookResult | None:
    drive = CompactionState.load(evt)
    if not drive.active or not (questions := asked(evt)):
        return None
    now = time.time()
    with SettledState.mutate(evt) as state:
        if state.blocked_at is not None and now - state.blocked_at < BURST_SECONDS:
            return None
        if not (found := matches(questions, plan_decisions(drive.plan_path) + durable_answers(str(evt.cwd)) + feedback_memories(evt))):
            return None
        state.blocked_at = now
    return evt.block(GATE_MESSAGE)
