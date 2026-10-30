from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from captain_hook import (
    Allow,
    BaseHookEvent,
    Event,
    FileFixture,
    HookResult,
    Input,
    Tool,
    Warn,
    WorkflowState,
    on,
    workflow_state,
)

from .compaction_handoff import CompactionState, ccn, rulings, script
from .owner_facing import CCI_POST, SLACK_CARD, posted_text
from .register_gates import fenced
from .register_injection import drive_args
from .tests.register_fixtures import CWD, DRIVE_CONFIG

DECISIONS = re.compile(r"^(#{2,3}) [^\n]*\bdecisions?\b[^\n]*$", re.IGNORECASE)
BOARD_VERBS = frozenset({"push", "update-block"})
CONFIRMS = "confirms:"
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
SLACK_ASK = {
    "questions": [
        {
            "question": 'Post this in C08ABCDEF12, thread 1760000000.000100? "Every side-feature carries over to the Go release with the same hard-fought UX; nothing is dropped."',
            "header": "Post",
        }
    ]
}
SLACK_MENTION_ASK = {
    "questions": [{"question": "Does every side-feature carry over to the Go release, or are some dropped from the Slack post?", "header": "Scope"}]
}
SLACK_WRITE = re.compile(r"^\s*(?:Post this|Edit the bot's message to this|Set the channel topic to this)\b")
SLACK_HEADERS = frozenset({"slack", "slack grant"})
SLACK_CHANNEL = re.compile(r"\bC[A-Z0-9]{8,}\b")
SLACK_VERB = re.compile(r"\b(?:post|reply|send)", re.IGNORECASE)
ORDERS_CARD = re.compile(r"\bcards?\b|\bthe owner's board\b|\bon (?:the|his) board\b|\bask (?:the owner|him)\b", re.IGNORECASE)
SENTENCE = re.compile(r"(?<=[.;!?])\s+|\n+")
NOT_RULINGS = ("corpus/", "no/")
QUOTE = 200
ROWS = f"{sys.executable} {script('rulings.py')} rows"
REGISTER = json.dumps({"rows": {
    "rule/4ac9243": {"kind": "order", "status": "in-force", "words": "there is only one local replica along side one local primary and everything is a client of it"},
    "corpus/b-tests": {"kind": "corpus", "words": ""},
}})
RULED_CARD = {"card": {"title": "Should every chat run its own local replica, or is everything a client of one local replica?", "options": ["One per chat", "One shared"]}}


@workflow_state("long_running_settled_questions")
class SettledState(WorkflowState):
    warned: list[str] = []


@dataclass(frozen=True)
class Settled:
    text: str


def strings(value: object) -> Iterator[str]:
    match value:
        case str():
            yield value
        case dict() if not str(value.get("id", "")).startswith(CONFIRMS):
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


def slack_write_approval(question: dict[str, str]) -> bool:
    text = question["question"]
    return bool(
        SLACK_WRITE.match(text)
        or question.get("header", "").strip().lower() in SLACK_HEADERS
        or (SLACK_CHANNEL.search(text) and SLACK_VERB.search(text))
    )


def asked(evt: BaseHookEvent) -> list[str]:
    if evt.tool_name == "AskUserQuestion":
        return [] if evt.agent_id else [question["question"] for question in evt._tool_input["questions"] if not slack_write_approval(question)]
    if evt.tool_name == SLACK_CARD:
        card = evt._tool_input.get("card") or {}
        return [card["title"]] if card.get("title") and (card.get("options") or card.get("waits") or card.get("asks")) else []
    found = []
    for call in evt.command.calls("cci"):
        values = CCI_POST.bind(call).values
        if values.get("words", ())[:1] == ("post",) and "root" in values.get("lane", ()) and {"decide", "decision"} & set(values.get("kind", ())):
            found += [sentence for sentence in SENTENCE.split(posted_text(values)) if ORDERS_CARD.search(sentence)]
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
    return [Settled(f"answer {answer['id'][:7]}: {answer['title']}") for answer in json.loads(listed.stdout or "[]")]


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


def register_rulings(evt: BaseHookEvent) -> list[Settled]:
    if not (flags := drive_args(evt)):
        return []
    rows = json.loads(rulings(str(evt.cwd), "rows", *flags))["rows"]
    return [
        Settled(f'{key}, the owner\'s words: "{" ".join(fields["words"].split())[:QUOTE]}"')
        for key, fields in rows.items()
        if not key.startswith(NOT_RULINGS) and fields.get("status") == "in-force" and fields.get("words")
    ]


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


def digest(question: str) -> str:
    return hashlib.sha256(" ".join(question.split()).encode()).hexdigest()[:16]


GATE_MESSAGE = (
    "Warning, nothing was blocked: This asks the owner what a settled plan decision, owner ruling, owner answer, or feedback memory already answers."
    " Apply the answer below and drop the question; the same question re-issued passes."
)


@on(
    Event.PreToolUse,
    only_if=[Tool("AskUserQuestion", "Bash", SLACK_CARD)],
    tests={
        Input(
            tool="AskUserQuestion",
            tool_input=SETTLED_ASK,
            file=FileFixture(home=True, name="brook.md", content=PLAN),
            state=[CompactionState(active=True, plan_path="~/brook.md")],
        ): Warn(pattern=r"settled plan decision"),
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
            state=[CompactionState(active=True, plan_path="~/brook.md"), SettledState(warned=[digest(SETTLED_ASK["questions"][0]["question"])])],
        ): Allow(),
        Input(tool=SLACK_CARD, tool_input=RULED_CARD, cwd=CWD, env=DRIVE_CONFIG, state=[CompactionState(active=True)], commands={ROWS: REGISTER}): Warn(
            pattern=r"already answers[\s\S]*rule/4ac9243, the owner's words: \"there is only one local replica"
        ),
        Input(
            command="cci post --lane board-refresher-2 --kind note --text 'board ready'",
            agent_id="a1b2c3",
            cwd=CWD,
            env=DRIVE_CONFIG,
            state=[CompactionState(active=True)],
            commands={ROWS: REGISTER},
        ): Allow(),
        Input(
            command="cci post --lane root --kind decide --to board-refresher-2 --text 'board-refresher-2: card whether everything is a client of one local replica.'",
            cwd=CWD,
            env=DRIVE_CONFIG,
            state=[CompactionState(active=True)],
            commands={ROWS: REGISTER},
        ): Warn(pattern=r"rule/4ac9243"),
        Input(
            command="cci post --lane root --kind decide --to lane-a --text 'lane-a: every chat keeps its own local replica cache.'",
            cwd=CWD,
            env=DRIVE_CONFIG,
            state=[CompactionState(active=True)],
            commands={ROWS: REGISTER},
        ): Allow(),
        Input(
            tool="AskUserQuestion",
            tool_input=SLACK_ASK,
            file=FileFixture(home=True, name="brook.md", content=PLAN),
            state=[CompactionState(active=True, plan_path="~/brook.md")],
        ): Allow(),
        Input(
            tool="AskUserQuestion",
            tool_input=SLACK_MENTION_ASK,
            file=FileFixture(home=True, name="brook.md", content=PLAN),
            state=[CompactionState(active=True, plan_path="~/brook.md")],
        ): Warn(pattern=r"settled plan decision"),
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
    if not (questions := asked(evt)):
        return None
    drive = CompactionState.load(evt)
    settled = register_rulings(evt)
    if drive.active:
        settled += plan_decisions(drive.plan_path) + durable_answers(str(evt.cwd)) + feedback_memories(evt)
    with SettledState.mutate(evt) as state:
        if not (found := [(question, item) for question, item in matches(questions, settled) if digest(question) not in state.warned]):
            return None
        state.warned = [*state.warned, *(digest(question) for question, _ in found)]
    return evt.context("\n".join([GATE_MESSAGE, fenced("\n".join(f"{question}\n  {item.text}" for question, item in found))]))
