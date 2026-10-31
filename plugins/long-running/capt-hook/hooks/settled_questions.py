from __future__ import annotations

import contextvars
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo

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

from captain_hook.util import reqenv

from .compaction_handoff import CompactionState, ccn, rulings, script, script_module
from .owner_facing import CCI_POST, SLACK_CARD, posted_text, shown
from .register_gates import fenced
from .register_injection import drive_args
from .ruling_misread import MATCH, candidate_rows
from .tests.board_fixtures import JUDGE, judged
from .tests.register_fixtures import CWD, DRIVE_CONFIG, STATES

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
SHARED_WORDS = 3
DECISIONS_PER_CARD = 6
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
QUOTE = 200
IN_FORCE = "in-force"
NOT_RULINGS = ("corpus/", "no/")
ROWS = f"{sys.executable} {script('rulings.py')} rows"
REPLICA = "4ac9243" + "0" * 33
REGISTER = json.dumps({"state_dir": str(STATES / "quiet"), "rows": {
    "rule/4ac9243": {"kind": "order", "status": "in-force", "answer": json.dumps([REPLICA]), "words": "there is only one local replica along side one local primary and everything is a client of it"},
    "corpus/b-tests": {"kind": "corpus", "words": ""},
}})
MATCHED = "- 4ac9243 How many local replicas run?\n  > there is only one local replica along side one local primary\n"
RULED_CARD = {"card": {"title": "Should every chat run its own local replica, or is everything a client of one local replica?", "options": ["One per chat", "One shared"]}}
OWNER_TAGS = frozenset({"from:owner", "scope:durable", "owner-ruling"})
PICK = re.compile(r"^(?:Pick:|Owner picked:\n)\s*(?:option \w+[,:] )?(.+?)\.?$", re.MULTILINE | re.IGNORECASE)
QUOTED = re.compile(r'"([^"]+)"')
PICKED_QUOTE = re.compile(r'\bpicked:?\s+"([^"]+)"', re.IGNORECASE)
BOARD_QUESTION = re.compile(r"^Board question:\s*(?:\d+\.\s*)?(.+)$", re.MULTILINE)
PACIFIC = ZoneInfo("America/Los_Angeles")


@workflow_state("long_running_settled_questions")
class SettledState(WorkflowState):
    judged: list[str] = []


@dataclass(frozen=True)
class Settled:
    text: str
    about: str = ""
    key: str = ""


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


def cards(value: object) -> Iterator[dict]:
    match value:
        case dict() if value.get("type") == "card":
            yield value
        case dict():
            for item in value.values():
                yield from cards(item)
        case list():
            for item in value:
                yield from cards(item)


def board_questions(args: list[str], cwd: str) -> list[tuple[str, str]]:
    found = []
    for arg in args:
        path = Path(cwd, Path(arg.split("=")[-1]).expanduser())
        if path.suffix == ".json" and path.is_file():
            doc = json.loads(path.read_text())
            for card in list(cards(doc)) or [doc]:
                if str(card.get("id", "")).startswith(CONFIRMS):
                    continue
                texts = [text for _, text in shown(card)]
                if question := next((text for text in texts if text.rstrip().endswith("?")), None):
                    found.append((question, "\n".join(texts)))
    return found


def slack_write_approval(question: dict[str, str]) -> bool:
    text = question["question"]
    return bool(
        SLACK_WRITE.match(text)
        or question.get("header", "").strip().lower() in SLACK_HEADERS
        or (SLACK_CHANNEL.search(text) and SLACK_VERB.search(text))
    )


def asked(evt: BaseHookEvent) -> list[tuple[str, str]]:
    if evt.tool_name == "AskUserQuestion":
        return [] if evt.agent_id else [(question["question"], "\n".join(strings(question))) for question in evt._tool_input["questions"] if not slack_write_approval(question)]
    if evt.tool_name == SLACK_CARD:
        card = evt._tool_input.get("card") or {}
        shown = "\n".join(strings(evt._tool_input))
        return [(card["title"], shown)] if card.get("title") and (card.get("options") or card.get("waits") or card.get("asks")) else []
    found = []
    for call in evt.command.calls("cci"):
        values = CCI_POST.bind(call).values
        if values.get("words", ())[:1] == ("post",) and "root" in values.get("lane", ()) and {"decide", "decision"} & set(values.get("kind", ())):
            text = posted_text(values)
            found += [(sentence, text) for sentence in SENTENCE.split(text) if ORDERS_CARD.search(sentence)]
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


def owner_answers(cwd: str) -> list[dict]:
    listed = ccn(cwd, "answer", "list", "--include-superseded", "--full", "--limit", "0", "--json")
    return [answer for answer in json.loads(listed.stdout or "[]") if OWNER_TAGS & set(answer.get("tags", ()))] if listed.returncode == 0 else []


def owner_picks(answers: list[dict]) -> list[Settled]:
    picks = []
    for answer in answers:
        body = answer.get("body") or ""
        lead = next((line.strip() for line in body.splitlines() if line.strip()), "")
        if found := PICK.search(body):
            chosen = (quoted := QUOTED.search(found[1])) and quoted[1] or found[1]
        elif found := PICKED_QUOTE.search(body):
            chosen = found[1]
        else:
            chosen = lead
        asked_then = (question := BOARD_QUESTION.search(body)) and question[1] or answer["title"]
        on = datetime.fromisoformat(answer["created_at"].replace("Z", "+00:00")).astimezone(PACIFIC).strftime("%b %-d")
        replaced = "; a later answer of his replaced it" if answer.get("superseded_by") else ""
        line = f'He picked "{chosen.strip()[:QUOTE]}" on {on}, asked "{" ".join(asked_then.split())[:QUOTE]}"{replaced}'
        picks.append(Settled(line, f"{answer['title']} {lead} {chosen}"))
    return picks


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


def register_rulings(evt: BaseHookEvent, flags: list[str] | None) -> list[Settled]:
    if not flags:
        return []
    rows = json.loads(rulings(str(evt.cwd), "rows", *flags))["rows"]
    return [
        Settled(f'His words on {key}: "{" ".join(fields["words"].split())[:QUOTE]}"', fields["words"], key)
        for key, fields in rows.items()
        if not key.startswith(NOT_RULINGS) and fields.get("status") == IN_FORCE and fields.get("words")
    ]


def stem(word: str) -> str:
    if len(word) <= 4:
        return word
    word = SUFFIX.sub(lambda suffix: "y" if suffix[0] == "ies" else "", word)
    return word.removesuffix("e")


def words(text: str) -> set[str]:
    return {stem(word) for word in WORD.findall(text.lower()) if len(word) > 3 and word not in STOPWORDS}


def candidates(settled: list[Settled], card: str) -> list[Settled]:
    asking = words(card)
    scored = [(len(asking & known) / len(known), item) for item in settled if len(asking & (known := words(item.about or item.text))) >= SHARED_WORDS]
    return [item for _, item in sorted(scored, key=lambda hit: -hit[0])[:DECISIONS_PER_CARD]]


def ruled(evt: BaseHookEvent, judge: ModuleType, flags: list[str] | None, settled: list[Settled], card: str) -> dict | None:
    state, matched = candidate_rows(evt, flags, card) if flags else (None, {})
    rows = {key: row for key, row in matched.items() if row.get("status") == IN_FORCE}
    decisions = candidates([item for item in settled if item.key not in rows], card)
    if not rows and not decisions:
        return None
    env = reqenv.env_map()
    verdict = judge.judge(
        "ruled",
        judge.settled([judge.owner_words(key, judge.said(row, state, env)) for key, row in rows.items()], [item.text for item in decisions]),
        card,
        backend=judge.backend_from(env),
        drive=flags[1] if flags else None,
        timeout=reqenv.clamp_timeout(judge.JUDGE_SECONDS),
        env=env,
    )
    return None if verdict is None else asdict(verdict)


def digest(question: str) -> str:
    return hashlib.sha256(" ".join(question.split()).encode()).hexdigest()[:16]


GATE_MESSAGE = (
    "Warning, nothing was blocked: A judge reads this as asking the owner what he already ruled, or as offering an option that misreads his words."
    " Fix it, or ask it again unchanged if the judge is wrong; the same question re-issued passes."
)
FINDING = '{question}\n  {sentence}\n  Asked: "{artifact_phrase}"\n  His words: "{evidence_phrase}"'
PLAN_ASK = {"tool": "AskUserQuestion", "file": FileFixture(home=True, name="brook.md", content=PLAN), "state": [CompactionState(active=True, plan_path="~/brook.md")]}
RULED = judged("Should every chat run its own local replica", "He already ruled one local replica that everything is a client of.")
DRIVEN = {"cwd": CWD, "env": DRIVE_CONFIG, "state": [CompactionState(active=True)]}


@on(
    Event.PreToolUse,
    only_if=[Tool("AskUserQuestion", "Bash", SLACK_CARD)],
    tests={
        Input(tool_input=SETTLED_ASK, commands=judged("are some dropped", "His plan says nothing is dropped."), **PLAN_ASK): Warn(
            pattern=r"^Warning, nothing was blocked: A judge reads this as asking the owner what he already ruled[\s\S]*His plan says nothing is dropped\."
        ),
        Input(tool_input=SETTLED_ASK, commands=judged(), **PLAN_ASK): Allow(),
        Input(tool_input=SETTLED_ASK, commands={JUDGE: (1, "claude: overloaded")}, **PLAN_ASK): Allow(),
        Input(tool_input=OPEN_ASK, commands=judged("Which GitHub App"), **PLAN_ASK): Allow(),
        Input(
            tool="Bash",
            tool_input={"command": "cc-present start --session s --doc ~/owner-board.json --new"},
            file=FileFixture(home=True, name="owner-board.json", content=json.dumps({"blocks": [{"id": "c1", "type": "card", "title": SETTLED_ASK["questions"][0]["question"]}]})),
            state=[CompactionState(active=True)],
            commands=judged("are some dropped"),
        ): Allow(),
        Input(
            tool_input=SETTLED_ASK,
            commands=judged("are some dropped"),
            **{**PLAN_ASK, "state": [*PLAN_ASK["state"], SettledState(judged=[digest(SETTLED_ASK["questions"][0]["question"])])]},
        ): Allow(),
        Input(tool=SLACK_CARD, tool_input=RULED_CARD, commands={ROWS: REGISTER, MATCH: MATCHED, **RULED}, **DRIVEN): Warn(pattern=r"He already ruled one local replica"),
        Input(tool=SLACK_CARD, tool_input=RULED_CARD, commands={ROWS: REGISTER, MATCH: MATCHED, **judged()}, **DRIVEN): Allow(),
        Input(command="cci post --lane board-refresher-2 --kind note --text 'board ready'", agent_id="a1b2c3", commands={ROWS: REGISTER, MATCH: MATCHED, **RULED}, **DRIVEN): Allow(),
        Input(
            command="cci post --lane root --kind decide --to board-refresher-2 --text 'board-refresher-2: card whether everything is a client of one local replica.'",
            commands={ROWS: REGISTER, MATCH: MATCHED, **RULED},
            **DRIVEN,
        ): Warn(pattern=r"He already ruled one local replica"),
        Input(
            command="cci post --lane root --kind decide --to lane-a --text 'lane-a: every chat keeps its own local replica cache.'",
            commands={ROWS: REGISTER, MATCH: MATCHED, **RULED},
            **DRIVEN,
        ): Allow(),
        Input(tool_input=SLACK_ASK, commands=judged("Post this"), **PLAN_ASK): Allow(),
        Input(tool_input=SLACK_MENTION_ASK, commands=judged("are some dropped"), **PLAN_ASK): Warn(pattern=r"already ruled"),
        Input(tool="AskUserQuestion", tool_input=SETTLED_ASK, commands=judged("are some dropped")): Allow(),
        Input(tool="Bash", tool_input={"command": "cc-present remove-block o1-card --session s"}, state=[CompactionState(active=True)]): Allow(),
        Input(tool_input=SETTLED_ASK, agent_id="a1b2c3", commands=judged("are some dropped"), **PLAN_ASK): Allow(),
    },
)
def check_settled_before_asking(evt: BaseHookEvent) -> HookResult | None:
    """Warn an asking call a judge reads as asking the owner what he already ruled, or as offering an option that misreads his words (root #211144)."""
    if not (asking := asked(evt)):
        return None
    drive = CompactionState.load(evt)
    answers = owner_answers(str(evt.cwd)) if drive.active and shutil.which("ccn") else []
    seen = SettledState.load(evt).judged
    fresh = list(dict.fromkeys((question, shown) for question, shown in asking if digest(shown) not in seen))
    if not fresh:
        return None
    flags, judge = drive_args(evt), script_module("long_running/judge")
    settled = register_rulings(evt, flags)
    if drive.active:
        settled += [*plan_decisions(drive.plan_path), *owner_picks(answers), *feedback_memories(evt)]
    contexts = [contextvars.copy_context() for _ in fresh]
    with ThreadPoolExecutor(len(fresh)) as pool:
        verdicts = list(pool.map(lambda context, pair: context.run(ruled, evt, judge, flags, settled, pair[1]), contexts, fresh))
    judged = [(question, shown, verdict) for (question, shown), verdict in zip(fresh, verdicts) if verdict is not None]
    with SettledState.mutate(evt) as state:
        state.judged = [*state.judged, *(digest(shown) for _, shown, _ in judged)]
    if not (found := [FINDING.format(question=question, **verdict) for question, _, verdict in judged if verdict["verdict"] == "yes"]):
        return None
    return evt.context("\n".join([GATE_MESSAGE, fenced("\n\n".join(found))]))
