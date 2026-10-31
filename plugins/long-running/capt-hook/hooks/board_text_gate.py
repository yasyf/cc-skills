from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Call, Event, HookResult, Input, Tool, Warn, WorkflowState, ast_grep, on
from captain_hook.cmd import Cmd
from captain_hook.util import reqenv

from .compaction_handoff import CCN_TIMEOUT_SECONDS, CompactionState, ccn, rulings, script
from .owner_facing import OWNER_QUOTE, PROGRAM_ACTIVE, RULINGS, STDIN, Reads, board_bytes, board_sources, fenced, plain_words, plan_words, shown, squeeze
from .register_injection import drive_args
from .tests.register_fixtures import DRIVE_CONFIG
from .tests.board_fixtures import (
    JUDGE,
    BREACHES,
    FAILING,
    GLOSSARY,
    HELD,
    HEREDOC,
    HEREDOC_SHA,
    REPO,
    ROWS,
    SWEEPS_LOG,
    board,
    fidelity,
    held_copy_hit,
    judged,
    missing,
    redraft,
    sha12,
    sweeps,
)
from .tests.rulings_consumers import separate

GATED = ("push", "update-block", "reply")
WATCHED = ("start", "push")
CONFIRM_WORDS = re.compile(r"\bas written\b|\bdecided by you\b|\byour ruling\b", re.IGNORECASE)
CAPABILITY = re.compile(r"\b(?:today|already|currently|as built|existing)\b", re.IGNORECASE)
CITED = re.compile(r"[\w./-]+:\d+(?:[-,]\d+)*`?\s*@\s*`?[0-9a-f]{7,40}\b|\(unverified\b")
UNVERIFIED = re.compile(r"\(unverified\b")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
HIT_COUNT = re.compile(r"\b(?:\d+|no|zero|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:sweep\s+)?hits?\b", re.IGNORECASE)
ONLY_THESE = re.compile(r"\bthese changes only\b|\bonly these changes\b|\bonly this block\b", re.IGNORECASE)
SWEEP_LINE = re.compile(r"^(?P<state>open|deleted|authorized)\s+(?P<answer>\S+)\s+(?P<key>\S+)\s+(?P<where>\S+:\d+)\s+(?P<name>.+)$")
FIDELITY_TEXT = re.compile(r"^fidelity: (?P<verdict>ok|missing) text (?P<sha>[0-9a-f]{12})\b\s*(?P<detail>.*)$")
SUBSTITUTED = re.compile(r'^"?\$\((?P<inner>.*)\)"?$', re.DOTALL)
OK = "ok"
REVIEW_BUDGET = 200_000
SWEEP_CADENCE = timedelta(hours=1)
TEXT = "text"
REFUSAL = "Warning, nothing was blocked: Owner-facing text passes every check before the owner sees it. Fix each problem below, then run the same command again."
SKIPPED = "Warning, nothing was blocked: board_text_gate skipped text it could not read."
PIPED_SECTION = '{"id":"sec-merge-taps","type":"section","title":"Release plan and merges"}'


class BoardWatches(WorkflowState):
    sessions: list[str] = []


@dataclass(frozen=True)
class Published:
    verb: str
    data: bytes
    doc: object
    session: str
    path: Path | None
    where: str

    @property
    def sha12(self) -> str:
        return hashlib.sha256(self.data).hexdigest()[:12]

    @property
    def suffix(self) -> str:
        return ".md" if self.verb in ("reply", "plan") else ".json"


def first(values: dict[str, tuple], name: str) -> str | None:
    return next(iter(values.get(name, ())), None)


def calls(evt: BaseHookEvent, verbs: tuple[str, ...]) -> Iterator[tuple[Call, dict[str, tuple], str | None, int]]:
    for call, values, source, piped in board_sources(evt):
        if first(values, "verb") in verbs and not values.get("dry-run"):
            yield call, values, source, piped


def reply_body(evt: BaseHookEvent, call: Call, values: dict[str, tuple]) -> str | Path | None:
    """The markdown a reply posts: its literal `--body`, or what a `--body "$(cat <<'EOF' ... EOF)"` heredoc or `--body "$(cat <file>)"` substitutes, as the shell would; the file's path when it does not exist yet, and None when it has no body."""
    if (body := first(values, "body")) is not None:
        return body
    words = call.source.words
    raw = next((word.raw for flag, word in zip(words, words[1:]) if flag.value == "--body"), None)
    raw = raw or next((word.raw.removeprefix("--body=") for word in words if word.raw.startswith("--body=")), None)
    if raw is None or not (substituted := SUBSTITUTED.match(raw)):
        return None
    inner = substituted["inner"]
    if redirect := next(ast_grep.find_kinds(inner, "bash", frozenset({"heredoc_redirect"})), None):
        text = next(ast_grep.find_kinds(inner, "bash", frozenset({"heredoc_body"}))).text
        return ("\n".join(line.lstrip("\t") for line in text.split("\n")) if redirect.text.startswith("<<-") else text).rstrip("\n")
    read = inner.strip()
    command = Cmd.parse(f"cat {read.removeprefix('<')}" if read.startswith("<") else read)
    match [[word.value for word in found.source.words] for found in command.calls()] if command else []:
        case [["cat", str(path)]]:
            file = Path(str(call.cwd or evt.cwd), Path(path).expanduser())
            return file.read_text().rstrip("\n") if file.exists() else file
        case _:
            return None


def as_text(verb: str, data: bytes, session: str, path: Path | None, where: str) -> Published:
    return Published(verb, data, {"type": TEXT, "id": where, "md": data.decode()}, session, path, where)


def parsed(data: bytes) -> tuple[bytes, object]:
    return data, json.loads(data)


def published(evt: BaseHookEvent, reads: Reads, skipped: list[str]) -> list[Published]:
    if evt.tool_name == "ExitPlanMode":
        plan = CompactionState.load(evt).plan_path
        data = reads.read("the plan file", lambda: Path(plan).expanduser().read_bytes()) if plan else None
        return [] if data is None else [as_text("plan", data, "", Path(plan).expanduser(), "the plan")]
    found = []
    for index, (call, values, source, piped) in enumerate(calls(evt, GATED), 1):
        session = first(values, "session") or evt.session_id
        if first(values, "verb") == "reply":
            where = f"the reply on {first(values, 'block')}"
            body = reads.read(f"the body of {where}, command {index}", lambda call=call, values=values: reply_body(evt, call, values))
            if isinstance(body, Path):
                skipped.append(f"{where}: its body file {body} does not exist when the hook runs, so its text went unchecked; write the file in an earlier call or pass --body inline")
            elif body is not None:
                found.append(as_text("reply", body.encode(), session, None, where))
            continue
        path = None if source in (None, STDIN) else Path(str(call.cwd or evt.cwd), Path(source).expanduser())
        if path and not path.exists():
            skipped.append(f"the board {source}: {path} does not exist when the hook runs, so its text went unchecked; write the file in an earlier call or pass its absolute path")
            continue
        if source and (loaded := reads.read(f"the board {source} {piped}", lambda: parsed(board_bytes(evt, call, source, piped)))):
            found.append(Published(first(values, "verb"), *loaded, session, path, source))
    return found


def blocks(value: object, card: dict | None = None) -> Iterator[tuple[dict, dict | None]]:
    match value:
        case dict():
            if isinstance(value.get("type"), str) and isinstance(value.get("id"), str):
                yield value, card
                card = value if value["type"] == "card" else card
            for item in value.values():
                yield from blocks(item, card)
        case list():
            for item in value:
                yield from blocks(item, card)


def own_text(block: dict) -> list[str]:
    return [text for where, text in shown(block, block["id"]) if where == block["id"]]


def confirms(found: list[tuple[dict, dict | None]], said: list[str]) -> list[str]:
    problems = []
    for block, card in found:
        if block["type"] != "approval":
            continue
        prompt = block.get("prompt", "")
        head, colon, rest = prompt.partition(":")
        labelled = bool(colon) and bool(CONFIRM_WORDS.search(head))
        framed = card is not None and bool(CONFIRM_WORDS.search(f"{card.get('title', '')} {card.get('summary', '')}"))
        if (labelled or framed or CONFIRM_WORDS.search(prompt)) and not any(squeeze(rest if labelled else prompt) in source for source in said):
            problems.append(f"{block['id']}: the confirm does not quote your words verbatim")
    return problems


def capabilities(found: list[tuple[dict, dict | None]]) -> list[str]:
    problems = []
    for block, _ in found:
        texts = own_text(block)
        sentences = [sentence.strip() for text in texts for sentence in SENTENCE.split(OWNER_QUOTE.sub(" ", text))]
        problems += [
            f'{block["id"]}: a claim about what exists cites no path:line @ ref and is not marked (unverified), "{sentence[:120]}"'
            for sentence in sentences
            if CAPABILITY.search(sentence) and not CITED.search(sentence)
        ]
        if block["type"] == "approval" and any(UNVERIFIED.search(text) for text in texts):
            problems.append(f"{block['id']}: an approval carries an unverified claim")
    return problems


def titles(found: list[tuple[dict, dict | None]]) -> list[str]:
    return [
        f'{block["id"]}: the title does not end in a question mark, "{block.get("title", "")}"'
        for block, _ in found
        if block["type"] == "card" and not str(block.get("title", "")).rstrip().endswith("?")
    ]


def sweep(reads: Reads, *artifacts: Path) -> list[re.Match[str]] | None:
    flags = [arg for artifact in artifacts for arg in ("--artifact", str(artifact))]
    lines = reads.read(f"rulings.py sweep {' '.join(map(str, artifacts)) or 'default'}", lambda: rulings(reads.repo, "sweep", *reads.which, *flags, evt=reads.evt))
    return None if lines is None else [hit for line in lines.splitlines() if (hit := SWEEP_LINE.match(line.strip())) and hit["state"] == "open"]


def ran(argv: list[str]) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=CCN_TIMEOUT_SECONDS, env=dict(reqenv.env_map()))
    done.check_returncode()
    return done.stdout


def revising(reads: Reads, session: str) -> list[str]:
    def load() -> list[str]:
        return json.loads(ran(["cc-present", "outcomes", "--session", session, "--no-doc"]))["revising"]["blockIds"]

    return reads.read("cc-present outcomes", load) or []


def strikes(item: Published, found: list[tuple[dict, dict | None]], reads: Reads) -> list[str]:
    texts = [text for block, _ in found for text in own_text(block)]
    problems = []
    if item.verb == "update-block" and found and found[0][0]["id"] in revising(reads, item.session) and not any(HIT_COUNT.search(text) for text in texts):
        problems.append(f"{found[0][0]['id']}: an update after a strike names no sweep hit count")
    if any(ONLY_THESE.search(text) for text in texts) and (hits := sweep(reads)):
        problems.append(f"{item.where}: says these changes only while the sweep has {len(hits)} open hits")
    return problems


def swept(reads: Reads, item: Published) -> list[re.Match[str]] | None:
    if item.path is not None:
        return sweep(reads, item.path)
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / f"{item.sha12}{item.suffix}"
        path.write_bytes(item.data)
        return sweep(reads, path)


def grades(reads: Reads, register: dict, sha: str) -> list[re.Match[str]] | None:
    def load() -> list[re.Match[str]] | None:
        if not (config := Path(register["state_dir"]) / "runner.json").exists():
            return None
        runner = json.loads(config.read_text())
        lanes = runner["landing"]["fidelity_lane"]
        named = [lanes] if isinstance(lanes, str) else lanes
        argv = ["cci", "grep", f"fidelity: (ok|missing) text {sha}", "--drive", runner["drive"], "--json", "--kind", "review", "--budget", str(REVIEW_BUDGET), *(arg for lane in named for arg in ("--lane", lane))]
        records = [json.loads(line) for line in ran(argv).splitlines() if line.strip()]
        return [hit for record in records if record.get("lane") in named and (hit := FIDELITY_TEXT.match(record["text"])) and hit["sha"] == sha]

    return reads.read(f"the fidelity reviews of {sha}", load)


def verify_reports(item: Published, reads: Reads, register: dict) -> list[str]:
    return [f"verify: a fidelity desk lists {hit['detail']}" for hit in grades(reads, register, item.sha12) or [] if hit["verdict"] != OK]


def notes(repo: str, *args: str) -> object:
    done = ccn(repo, *args, "--json")
    done.check_returncode()
    return json.loads(done.stdout)


def passes(reads: Reads, program: str) -> list[datetime] | None:
    def load() -> list[datetime]:
        logs = [log["id"] for log in notes(reads.repo, "log", "list") if log["title"] == f"sweeps: {program}"]
        return [datetime.fromisoformat(entry["ts"]) for log in logs[:1] for entry in notes(reads.repo, "log", "show", log)["entries"]]

    return reads.read("the sweeps log", load)


def text_grade(item: Published, reads: Reads, register: dict) -> list[str]:
    problems = []
    if (graded := grades(reads, register, item.sha12)) is not None and not any(hit["verdict"] == OK for hit in graded):
        problems.append(f"text grade: no fidelity desk review names {item.where} ({item.sha12}); push again once one posts fidelity: ok text {item.sha12}")
    match passes(reads, register["program"]):
        case []:
            problems.append(f"text grade: no hourly sweep pass is logged for {register['program']}")
        case [*stamps] if (age := datetime.now(UTC) - max(stamps)) >= SWEEP_CADENCE:
            problems.append(f"text grade: the newest hourly sweep pass is {int(age.total_seconds() // 60)} minutes old")
    return problems


def envelope(doc: object) -> list[tuple[dict, dict | None]]:
    if not isinstance(doc, dict) or "type" in doc:
        return []
    return [({"type": "document", "id": "the document", **{key: value for key, value in doc.items() if isinstance(value, str)}}, None)]


def graded(reads: Reads, item: Published) -> list[str]:
    found = [*envelope(item.doc), *blocks(item.doc)]
    register = reads.register()
    words = reads.said() if register else None
    problems = [
        *(confirms(found, words) if words is not None else []),
        *capabilities(found),
        *titles(found),
        *strikes(item, found, reads),
    ]
    if register:
        problems += verify_reports(item, reads, register)
        problems += [f'sweep: {hit["where"]} names "{hit["name"]}", which {hit["key"]} rules out' for hit in swept(reads, item) or []]
    problems += plan_words(item.data.decode(), reads) if item.verb == "plan" else plain_words([text for _, text in shown(item.doc)], reads)
    if register:
        problems += text_grade(item, reads, register)
    return problems


def posting(command: str | None = None, *, sweep_out: str = "", reviewed: tuple[str, ...] = (), minutes: int = 5, revised: tuple[str, ...] = (), **extra: object) -> Input:
    commands = {
        f"{RULINGS} rows": ROWS,
        f"{RULINGS} sweep": sweep_out,
        f"{RULINGS} glossary": GLOSSARY,
        "cci grep": fidelity(*reviewed),
        "cci post": "#1\n",
        f"ccn -R {REPO} log list": SWEEPS_LOG,
        f"ccn -R {REPO} log show": sweeps(minutes),
        f"ccn -R {REPO} answer list": "[]",
        "cc-present outcomes": json.dumps({"revising": {"blockIds": list(revised)}}),
        **judged(),
    }
    return Input(
        command=command,
        state=extra.pop("state", PROGRAM_ACTIVE),
        cwd=REPO,
        env={**DRIVE_CONFIG, **extra.pop("env", {})},
        commands={**commands, **extra.pop("commands", {})},
        **extra,
    )


def update(name: str, **extra: object) -> Input:
    return posting(f"cc-present update-block {board(name)}", reviewed=(sha12(name),), **extra)


def reply(name: str, **extra: object) -> Input:
    text = board(name).read_text()
    return posting(f"cc-present reply --block r3-confirm-loops --body {shlex.quote(text)}", reviewed=(hashlib.sha256(text.encode()).hexdigest()[:12],), **extra)


def exits(name: str, **extra: object) -> Input:
    state = [CompactionState(active=True, slug="brook", plan_path=str(board(name)))]
    return posting(tool="ExitPlanMode", tool_input={"plan": board(name).read_text()}, state=state, **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash", "ExitPlanMode")],
    tests=separate("board_text_gate.board_text_gate", {
        **{update(name, sweep_out=HELD.get(name, "")): Warn(pattern=BREACHES[name]) for name in FAILING},
        **{update(redraft(name)): Allow() for name in FAILING},
        reply("reply-4ae100d5.md", sweep_out=f"open - rule/5c0e9a1 {board('reply-4ae100d5.md')}:1 held copy\n"): Warn(pattern=r"names \"held copy\""),
        reply("reply-4ae100d5.redraft.md"): Allow(),
        posting('cc-present reply --block r3-confirm-loops --body "$(cat /nonexistent/fork-refusals-reply.md)"'): Warn(pattern=r"skipped text[\s\S]*/nonexistent/fork-refusals-reply.md does not exist when the hook runs"),
        posting("cc-present push /nonexistent/preview-doc2.json"): Warn(pattern=r"skipped text[\s\S]*the board /nonexistent/preview-doc2.json: /nonexistent/preview-doc2.json does not exist when the hook runs"),
        posting(f"cc-present push {board('r3-titles.redraft.json')}"): Warn(pattern=r"no fidelity desk review names"),
        update("clean-card.json", minutes=90): Warn(pattern=r"the newest hourly sweep pass is 9\d minutes old"),
        update("clean-card.json", commands={f"ccn -R {REPO} log list": "[]"}): Warn(pattern=r"no hourly sweep pass is logged for brook"),
        update("strike-update.json", revised=("share-open",)): Warn(pattern=r"share-open: an update after a strike names no sweep hit count"),
        update("strike-update.redraft.json", revised=("share-open",)): Allow(),
        update("strike-update.json"): Allow(),
        update("only-these.json", sweep_out=held_copy_hit("plan.md")): Warn(pattern=r"says these changes only while the sweep has 1 open hits"),
        update("only-these.json"): Allow(),
        posting(f"cc-present update-block - <<'EOF'\n{HEREDOC}EOF", reviewed=(HEREDOC_SHA,)): Allow(),
        posting(f"cc-present update-block - <<'EOF'\n{HEREDOC}EOF"): Warn(pattern=HEREDOC_SHA),
        posting(f"cc-present update-block {board('r3-titles.pre-grade.json')} --dry-run"): Allow(),
        update("r3-titles.pre-grade.json", state=[], env={"CLAUDE_LONG_RUNNING_DRIVE": "brook"}): Warn(pattern=r"r3-build: the title does not end"),
        update("r3-titles.pre-grade.json", state=[], env={"CLAUDE_LONG_RUNNING_DRIVE": ""}): Allow(),
        update("clean-card.json", commands={f"{RULINGS} rows": "no register for brook"}): Allow(),
        update("r3-titles.pre-grade.json", commands={f"{RULINGS} rows": "no register for brook"}): Allow(),
        update("r3-titles.pre-grade.json", commands={"cci grep": "cci: no drive brook"}): Allow(),
        update("clean-card.json", commands={"cci grep": fidelity(sha12("clean-card.json")) + missing(sha12("clean-card.json"))}): Warn(
            pattern=r"verify: a fidelity desk lists rule/5c0e9a1 the card offers a held copy"
        ),
        update("clean-card.json", commands={"cci grep": fidelity(sha12("clean-card.json")) + missing(sha12("r3-titles.redraft.json"))}): Allow(),
        update("clean-card.json", commands={"cci grep": fidelity(sha12("clean-card.json"), lane="another-desk")}): Warn(
            pattern=r"no fidelity desk review names"
        ),
        update("clean-card.json", commands=judged("read-only", "Say a copy he can open but not change.")): Warn(
            pattern=r'plain words, "read-only": Say a copy he can open but not change\.'
        ),
        update("clean-card.json", commands={JUDGE: (1, "claude: overloaded")}): Allow(),
        posting(f"cat <<'EOF' > /tmp/clean.json\n{HEREDOC}EOF\ncc-present update-block - <<'EOF'\n{board('title-not-question.json').read_text()}EOF", reviewed=(HEREDOC_SHA,)): Warn(
            pattern=r"share-open: the title does not end"
        ),
        posting("ls -la"): Allow(),
        posting(f"echo '{PIPED_SECTION}' | cc-present update-block -", reviewed=(hashlib.sha256(f"{PIPED_SECTION}\n".encode()).hexdigest()[:12],)): Allow(),
        posting(f"echo '{PIPED_SECTION}' | cc-present update-block -"): Warn(pattern=r"no fidelity desk review names -"),
        posting("python3 gen.py | cc-present update-block -"): Allow(),
        exits("plan.md", reviewed=(sha12("plan.md"),)): Allow(),
        exits("plan-title.md", reviewed=(sha12("plan-title.md"),), commands=judged("Notes R3b")): Warn(pattern=r'plain words, "Notes R3b"'),
        exits("plan-title.redraft.md", reviewed=(sha12("plan-title.redraft.md"),)): Allow(),
        exits("plan.md"): Warn(pattern=r"no fidelity desk review names the plan"),
        exits("plan-ids.md", reviewed=(sha12("plan-ids.md"),), commands=judged("C221")): Warn(pattern=r'plain words, "C221"'),
        exits("plan-only-these.md", reviewed=(sha12("plan-only-these.md"),), sweep_out=held_copy_hit("plan-only-these.md")): Warn(
            pattern=r"says these changes only"
        ),
    }),
)
def board_text_gate(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    reads = Reads(evt, which)
    skipped: list[str] = []
    items = published(evt, reads, skipped)
    problems = [problem for item in items for problem in graded(reads, item)]
    reads.report("board_text_gate")
    if problems and not reads.failures:
        return evt.context(fenced(REFUSAL, problems))
    return evt.context(fenced(SKIPPED, skipped)) if skipped else None


@on(
    Event.PostToolUse,
    only_if=[Tool("Bash")],
    tests=separate("board_text_gate.start_board_watch", {
        Input(command="cc-present push board.json --session s1", state=[*PROGRAM_ACTIVE, BoardWatches(sessions=["s1"])]): Allow(),
        Input(command="cc-present push board.json --session s1 --dry-run", state=PROGRAM_ACTIVE): Allow(),
        Input(command="cc-present push board.json --session s1"): Allow(),
        Input(command="cc-present update-block card.json --session s1", state=PROGRAM_ACTIVE): Allow(),
    }),
)
def start_board_watch(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    started = BoardWatches.load(evt).sessions
    boards = [(first(values, "verb"), first(values, "session") or evt.session_id) for _, values, _, _ in calls(evt, WATCHED)]
    fresh = list(dict.fromkeys(session for verb, session in boards if verb == "start" or session not in started))
    for session in fresh:
        argv = [sys.executable, str(script("board_watch.py")), "start", "--session", session, *which]
        subprocess.Popen(argv, env=dict(reqenv.env_map()), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    if unseen := [session for session in fresh if session not in started]:
        with BoardWatches.mutate(evt) as state:
            state.sessions = [*state.sessions, *unseen]
    return None
