from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from captain_hook import (
    CONFIRMED,
    UNCONFIRMED,
    Allow,
    Annotated,
    BaseHookEvent,
    Block,
    Call,
    Confirm,
    Event,
    FromSubagent,
    HookResult,
    Input,
    TaskCall,
    Tool,
    on,
)

from .compaction_handoff import REGISTER_FENCE, CompactionState, ccn, post_blocker, rulings
from .lane_landing import program
from .lane_rotation import DriveActive
from .owner_facing import CCI_POST, inbox_text, incident_spawn, prose, record_text
from .register_injection import drive_args
from .tests.register_fixtures import (
    ACTIVE,
    CWD,
    OWNER_QUESTION,
    ROWED,
    ROWS,
    SHA,
    UNROWED,
    fixture,
    readings,
    reply,
    shown,
)

SHORT = 7
ANSWER_ID = re.compile(r"\b[0-9a-f]{7,40}\b")
CITING_LINE = re.compile(r"(?:^|(?<=[\"']))[ \t]*(?:GO|DECIDE) root \(.*", re.MULTILINE)
GO_LINE = re.compile(r"(?:^|(?<=[\"']))[ \t]*GO root \(.*", re.MULTILINE)
CITING_KINDS = frozenset({"go", "decide"})
GO_KINDS = frozenset({"go"})
BOARD_WRITES = frozenset({"push", "update-block"})
BRIEF_LAUNCHERS = frozenset({"orca-launch.sh", "codex-ask"})
NOT_FOUND = 3
EXCERPT = 160
BLOCKERS = "register-blockers"
READ_ERRORS = (subprocess.SubprocessError, OSError, ValueError, KeyError)
UNROWED_ANSWER = (
    "A GO or DECIDE cites only owner answers the register holds, and `{ids}` has no row in register `{program}`. "
    "Classify the owner's words with `rulings.py classify` first, then post the line."
)
REPEAT_OPEN = (
    "The owner repeated `{keys}`, and its sweep hit list is not posted. "
    "Post the hits and mark them with `rulings.py sweep <key> --posted <ref>` before this {moment}."
)
UNCLASSIFIED = (
    'Owner capture {digest} ({at}) has no row in register {program}: "{excerpt}". '
    "Classify it with `rulings.py classify` before the next key moment."
)
READ_FAILED = "{hook} could not read the register ({cause}) and passed the call; it passes every call it gates until the read works."
QUESTION_OPEN = (
    "An owner question is open, so a reply or writer brief quotes the words, sentence and rows below and says from them whether"
    " the thing should exist before any code read. It never defines the term or writes the answer for the writer."
)
UNQUOTED = (
    "This reply or brief does not quote the open owner question below. Quote its words, sentence and rows, and say from them"
    " whether the thing should exist before any code read."
)
CAPTURE_LOG = "owner-prompts"
CAPTURE_FORMAT = "jsonl"
QUESTION_JUDGE = Confirm(
    rule="Block when a reply or writer brief, written while the owner's question is open, leaves out his words, the sentence"
    " under question or the register rows bearing on it, or reads code before saying from those rulings whether the thing"
    " exists at all; block too when it defines the thing from code or the root's own reading, or writes the answer for the writer."
)
WORDS_SHOWN = 200
TIMERS = fixture("brief-158695")


def read_register(evt: BaseHookEvent, which: list[str]) -> dict:
    return json.loads(rulings(str(evt.cwd), "rows", *which, "--json"))


def cause(error: Exception) -> str:
    detail = getattr(error, "stderr", None) or str(error) or type(error).__name__
    return " ".join(str(detail).split())[:200]


def blocker(evt: BaseHookEvent, key: str, text: str) -> None:
    if evt.ctx.s.once(key, scope=BLOCKERS):
        post_blocker(evt, text)


def failed_open(evt: BaseHookEvent, hook: str, error: Exception) -> None:
    blocker(evt, f"{hook}:read", READ_FAILED.format(hook=hook, cause=cause(error)))


def folded(text: str) -> str:
    return " ".join(text.split()).casefold()


def posted(evt: BaseHookEvent, kinds: frozenset[str]) -> list[str]:
    return [
        record_text(values)
        for call in evt.command.calls("cci")
        if (values := CCI_POST.bind(call).values).get("words", ())[:1] == ("post",) and kinds & set(values.get("kind", ()))
    ]


def citing_lines(evt: BaseHookEvent) -> list[str]:
    return CITING_LINE.findall(inbox_text(evt)) + posted(evt, CITING_KINDS)


def captures(register: dict) -> Path:
    return Path(register["state_dir"]) / f"{CAPTURE_LOG}.{CAPTURE_FORMAT}"


def launches(evt: BaseHookEvent) -> list[tuple[Call, tuple[str, ...]]]:
    return [(call, run) for call in evt.command.calls() if (run := program((call.name, *call.args))) and Path(run[0]).name in BRIEF_LAUNCHERS]


def key_moment(evt: BaseHookEvent) -> str | None:
    if evt.tool_name == "Agent" or launches(evt):
        return "spawn"
    if GO_LINE.search(inbox_text(evt)) or posted(evt, GO_KINDS):
        return "GO"
    if any(call.args[:1] and call.args[0] in BOARD_WRITES for call in evt.command.calls("cc-present")):
        return "board push"
    return None


def incident_fix_spawn(evt: BaseHookEvent) -> bool:
    call = evt.as_input(TaskCall)
    return call is not None and incident_spawn(call, prose(call)[:1])


def register_files(register: dict, pattern: str) -> list[tuple[Path, dict]]:
    return [(path, json.loads(path.read_text())) for path in sorted((Path(register["state_dir"]) / "register").glob(pattern))]


def report_unclassified(evt: BaseHookEvent, register: dict) -> None:
    prompts = captures(register)
    if not prompts.is_file():
        return
    classified = set(register["classified"])
    for capture in (json.loads(line) for line in prompts.read_text().splitlines() if line.strip()):
        if capture["digest"] not in classified:
            excerpt = " ".join(capture["text"].split())[:EXCERPT]
            text = UNCLASSIFIED.format(digest=capture["digest"], at=capture["at"], program=register["program"], excerpt=excerpt)
            blocker(evt, f"unclassified:{capture['digest']}", text)


def open_repeats(register: dict) -> list[str]:
    return [repeat["key"] for _, repeat in register_files(register, "repeat-*.json") if repeat.get("posted") is None]


def is_answer(evt: BaseHookEvent, token: str) -> bool:
    done = ccn(str(evt.cwd), "answer", "show", token, "--json")
    if done.returncode == NOT_FOUND:
        return False
    done.check_returncode()
    return True


def unrowed(evt: BaseHookEvent, lines: list[str], register: dict) -> list[str]:
    rowed = {found[:SHORT] for row in register["rows"].values() for found in ANSWER_ID.findall(str(row.get("answer") or ""))}
    cited = dict.fromkeys(token for line in lines for token in ANSWER_ID.findall(line))
    return [token for token in cited if token[:SHORT] not in rowed and is_answer(evt, token)]


def go(text: str, kind: str = "go") -> str:
    return f"cci post --lane root --kind {kind} --to walker --text '{text}'"


def register_case(command: str | None = None, state: str = "quiet", *answers: tuple[str, str | tuple[int, str]], **extra: object) -> Input:
    fields = {"command": command} if command is not None else {}
    return Input(**(fields | extra), state=ACTIVE, cwd=CWD, commands=readings(state, *answers))


def spawn(name: str, prompt: str, state: str = "quiet", **extra: object) -> Input:
    return register_case(None, state, tool="Agent", tool_input={"name": name, "prompt": prompt}, **extra)


@on(
    Event.PreToolUse,
    only_if=[DriveActive(), Tool("Bash", "Agent", "Edit", "Write")],
    skip_if=[FromSubagent(), Annotated("raw")],
    tests={
        register_case(go(f"cut web under {ROWED[:7]}"), "quiet", (ROWED[:7], shown(ROWED))): Allow(),
        register_case(go(f"cut web under {UNROWED[:7]}"), "quiet", (UNROWED[:7], shown(UNROWED))): Block(
            pattern=r"^A GO or DECIDE cites only owner answers the register holds, and `1dfd09d` has no row in register `brook`\."
        ),
        register_case(go(f"lanes stop until {UNROWED[:7]} lands", "decide"), "quiet", (UNROWED[:7], shown(UNROWED))): Block(pattern=r"`1dfd09d` has no row"),
        register_case(
            f'echo "GO root (4:50 AM) walker: cut web under {UNROWED}" >> /s/inbox/deploy-go.md', "quiet", (UNROWED, shown(UNROWED))
        ): Block(pattern=r"has no row in register"),
        register_case(f'echo "STATUS: previous GO root (4:50 AM) under {UNROWED[:7]} was withdrawn" >> /s/inbox/deploy-go.md', "repeat"): Allow(),
        register_case(go("land 1234567 now"), "quiet", ("1234567", shown("1234567"))): Block(pattern=r"`1234567` has no row"),
        register_case(go(f"land {SHA[:7]} now"), "quiet", (SHA[:7], (3, "not-found: entity not found"))): Allow(),
        register_case(go(f"land head {SHA} now"), "quiet", (SHA, (3, "not-found: entity not found"))): Allow(),
        register_case(go(f"lift the hold under {UNROWED[:7]}", "lift")): Allow(),
        register_case(go(f"cut web under {UNROWED[:7]}"), "quiet", (UNROWED[:7], (1, "ccn: store locked"))): Allow(),
        register_case(go("cut web"), "repeat"): Block(pattern=r"^The owner repeated `rule/0a1b2c3`, and its sweep hit list is not posted\..* before this GO\.$"),
        spawn("lane-x", "Lane lane-x: build the slice verb.", "repeat"): Block(pattern=r"before this spawn\.$"),
        register_case("codex-ask -m sol /s/briefs/lane-x.md", "repeat"): Block(pattern=r"before this spawn\.$"),
        register_case("cc-present push /tmp/board.json", "repeat"): Block(pattern=r"before this board push\.$"),
        register_case("cc-present update-block --id c7-timers /tmp/block.json", "repeat"): Block(pattern=r"board push"),
        spawn("incident-api-500s-fix", "Fix the api 500s.", "repeat"): Allow(),
        register_case("cc-present reply --block r3 --body 'Done.'", "repeat"): Allow(),
        register_case("ls", "repeat"): Allow(),
        spawn("lane-x", "Lane lane-x: build the slice verb."): Allow(),
        Input(command=go("cut web"), state=ACTIVE, cwd=CWD, commands={ROWS: (1, "ccn: no ledger"), "cci post": "#1"}): Allow(),
        Input(command=go("cut web"), cwd=CWD, commands=readings("repeat")): Allow(),
        register_case(go("cut web"), "repeat", agent_id="a1b2c3"): Allow(),
    },
)
def register_gate(evt: BaseHookEvent) -> HookResult | None:
    citing = citing_lines(evt)
    moment = key_moment(evt)
    if not (citing or moment) or not (which := drive_args(evt)) or incident_fix_spawn(evt):
        return None
    try:
        register = read_register(evt, which)
        report_unclassified(evt, register)
        repeated = open_repeats(register) if moment else []
        missing = [] if repeated else unrowed(evt, citing, register)
    except READ_ERRORS as error:
        failed_open(evt, "register_gate", error)
        return None
    if repeated:
        return evt.block(REPEAT_OPEN.format(keys=", ".join(repeated), moment=moment))
    return evt.block(UNROWED_ANSWER.format(ids=", ".join(missing), program=register["program"])) if missing else None


def option(args: tuple[str, ...], name: str) -> list[str]:
    values = [value for flag, value in zip(args, args[1:], strict=False) if flag == name]
    return values + [arg.removeprefix(f"{name}=") for arg in args if arg.startswith(f"{name}=")]


def replies(evt: BaseHookEvent) -> list[tuple[str, ...]]:
    return [call.args for call in evt.command.calls("cc-present") if call.args[:1] == ("reply",)]


def brief_files(evt: BaseHookEvent) -> list[Path]:
    return [path for call, run in launches(evt) for arg in run[1:] if (path := Path(call.cwd or evt.cwd) / Path(arg).expanduser()).is_file()]


def answer_text(evt: BaseHookEvent) -> tuple[str, str]:
    if (call := evt.as_input(TaskCall)) is not None:
        return call.raw.get("prompt") or "", ""
    briefs = "\n".join(path.read_text() for path in brief_files(evt))
    return "\n".join([*(body for args in replies(evt) for body in option(args, "--body")), briefs]).strip(), briefs


def open_questions(register: dict) -> list[dict]:
    return [question for _, question in register_files(register, "question-*.json") if not question.get("closed")]


def quotes(text: str, question: dict) -> bool:
    return any(folded(needle) in folded(text) for needle in (question["text"], question["term"]) if needle.strip())


def asked(question: dict, rows: dict) -> str:
    bearing = [f'{key}: "{" ".join(rows[key]["words"].split())[:WORDS_SHOWN]}"' for key in question["rows"] if key in rows]
    return "\n".join(
        [
            f'- The owner asked: "{question["text"]}"',
            f'  Sentence under question: "{question["sentence"]}"',
            *(f"  Row {row}" for row in bearing),
        ]
    )


def fenced(text: str) -> str:
    return f"{REGISTER_FENCE}\n{text}\n{REGISTER_FENCE}"


def question(text: str, state: str, **extra: object) -> Input:
    return Input(command=text, state=ACTIVE, cwd=CWD, commands=readings(state), **extra)


def asks(name: str, prompt: str, state: str, **extra: object) -> Input:
    return Input(tool="Agent", tool_input={"name": name, "prompt": prompt}, state=ACTIVE, cwd=CWD, commands=readings(state), **extra)


@on(
    Event.PreToolUse,
    only_if=[DriveActive(), Tool("Bash", "Agent")],
    skip_if=[Annotated("raw")],
    tests={
        asks("reply-lookup", fixture("brief-158585"), "question-lookup", decide=CONFIRMED): Block(
            pattern=r'^An owner question is open, .*\n~{12}\n- The owner asked: "why does it need this lookup\?"\n  Sentence under question: "6\. How does'
            r' the export learn a report\'s owner\?"\n  Row rule/4d5e6f7: "The export never needs to know about laptop-only reports\."\n~{12}$'
        ),
        asks("timers-writer", TIMERS, "question-timers", decide=CONFIRMED): Block(pattern=r'"what is a relay slot\? why is this here\?"'),
        question(reply("c7-timers", fixture("correction-1244")), "question-timers", decide=UNCONFIRMED): Allow(),
        asks("timers-writer", TIMERS, "question-timers", decide=UNCONFIRMED): Allow(),
        asks("lane-x", "Lane lane-x: fix the flaky api test.", "question-timers"): Block(pattern=r"^This reply or brief does not quote the open owner question below\."),
        question(reply("c7-timers", "Every 30 s a worker asks the server for new jobs."), "question-timers"): Block(pattern=r"does not quote"),
        question(f"orca-launch.sh timers-writer standard xhigh {OWNER_QUESTION / 'brief-158695.md'}", "question-timers", decide=CONFIRMED): Block(
            pattern=r"relay slot"
        ),
        question(f"codex-ask -m sol {OWNER_QUESTION / 'brief-158695.md'}", "question-timers", decide=CONFIRMED): Block(pattern=r"relay slot"),
        question(f"codex-ask -m sol {OWNER_QUESTION / 'brief-158695.md'}", "question-timers", decide=UNCONFIRMED): Allow(),
        asks("lane-x", "Lane lane-x: fix the flaky api test.", "question-closed"): Allow(),
        asks("lane-x", "Lane lane-x: fix the flaky api test.", "quiet"): Allow(),
        asks("incident-api-500s-fix", "Fix the api 500s.", "question-timers"): Allow(),
        question("cc-present push /tmp/board.json", "question-timers"): Allow(),
        question("ls", "question-timers"): Allow(),
        Input(tool="Agent", tool_input={"name": "lane-x", "prompt": "Fix it."}, state=ACTIVE, cwd=CWD, commands={ROWS: (1, "boom"), "cci post": "#1"}): Allow(),
        Input(tool="Agent", tool_input={"name": "lane-x", "prompt": "Fix it."}, cwd=CWD, commands=readings("question-timers")): Allow(),
    },
)
def owner_question_gate(evt: BaseHookEvent) -> HookResult | None:
    if incident_fix_spawn(evt) or not (which := drive_args(evt)):
        return None
    try:
        text, briefs = answer_text(evt)
        if not text:
            return None
        register = read_register(evt, which)
        if not (questions := open_questions(register)):
            return None
        shown = [fenced("\n".join(asked(question, register["rows"]) for question in questions)), *([fenced(briefs.strip())] if briefs else [])]
        quoted = all(quotes(text, question) for question in questions)
    except READ_ERRORS as error:
        failed_open(evt, "owner_question_gate", error)
        return None
    if quoted:
        return evt.block("\n".join([QUESTION_OPEN, *shown]), confirm=QUESTION_JUDGE)
    return evt.block("\n".join([UNQUOTED, *shown]))


def close(evt: BaseHookEvent, hook: str, answered: Callable[[dict], bool], how: str) -> None:
    if not (which := drive_args(evt)):
        return
    try:
        for path, found in register_files(read_register(evt, which), "question-*.json"):
            if not found.get("closed") and answered(found):
                path.write_text(json.dumps(found | {"closed": how}))
    except READ_ERRORS as error:
        failed_open(evt, hook, error)


@on(
    Event.PostToolUse,
    only_if=[DriveActive(), Tool("Bash")],
    tests={
        question(reply("c7-repair", "Done."), "question-timers"): Allow(),
        question("cc-present push /tmp/board.json", "question-timers"): Allow(),
    },
)
def close_replied_questions(evt: BaseHookEvent) -> HookResult | None:
    if blocks := {block for args in replies(evt) for block in option(args, "--block")}:
        close(evt, "close_replied_questions", lambda found: bool(blocks & set(found.get("blocks") or ())), f"reply {' '.join(sorted(blocks))}")
    return None


@on(
    Event.Stop,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    tests={
        Input(state=ACTIVE, cwd=CWD, commands=readings("question-timers")): Allow(),
        Input(state=[CompactionState(active=True)], cwd=CWD): Allow(),
    },
)
def close_chat_questions(evt: BaseHookEvent) -> HookResult | None:
    close(evt, "close_chat_questions", lambda found: not found.get("blocks"), "turn end")
    return None
