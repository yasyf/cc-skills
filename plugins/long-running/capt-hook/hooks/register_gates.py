from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from captain_hook import (
    Allow,
    Annotated,
    BaseHookEvent,
    Block,
    Call,
    Event,
    FromSubagent,
    HookResult,
    Input,
    TaskCall,
    Tool,
    Warn,
    on,
)
from captain_hook.util import reqenv

from .compaction_handoff import CCN_TIMEOUT_SECONDS, REGISTER_FENCE, ROOT_LANE, CompactionState, bus_name, ccn, drive_entry, post_blocker, rulings, script
from .incident_lane_pr import FIX_LANE, INCIDENT_LANE
from .lane_landing import GH_REPO_FLAGS, lane_name, program
from .lane_rotation import DriveActive
from .nudges import once_per_hour
from .owner_facing import CCI_POST, INCIDENT_DIGEST, INCIDENT_STORE, inbox_text, incident_spawn, open_incidents, posted_text, prose, read_incident_source
from .pr_ledger import OPENERS
from .register_injection import drive_args, helper
from .root_context import FILE_WRITES
from .session_tree import drive_lane
from .tests.register_fixtures import (
    ACTIVE,
    CWD,
    DRIVE_CONFIG,
    GO_PATH,
    LEDGER,
    OPEN_FIX,
    OWNER_QUESTION,
    ROWED,
    ROWS,
    SHA,
    REPEATED,
    UNROWED,
    fixture,
    readings,
    reply,
    shown,
)
from .tests.rulings_consumers import separate

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
    "Warning, nothing was blocked: this GO or DECIDE cites `{ids}`, and register `{program}` has no row for it. "
    "Classify the owner's words with `rulings.py classify`."
)
UNCLASSIFIED = (
    'Owner capture {digest} ({at}) has no row in register {program}: "{excerpt}". '
    "Classify it with `rulings.py classify` before the next key moment."
)
UNCLASSIFIED_BATCH = (
    "{count} owner captures have no row in register {program}: {captures}. "
    "Classify them with `rulings.py classify` before the next key moment."
)
BATCH_ENTRY = '{digest} ({at}) "{excerpt}"'
BATCH_EXCERPT = 80
REGISTER_LANE_GONE = " `cci who` lists no live register lane `{lane}` nor a numbered successor of it, so this blocker goes to the root: relaunch the register lane as its next numbered successor from its brief to classify {these}."
SUCCESSOR_NUMBER = re.compile(r"(?:-\d{1,2})+$")
READ_FAILED = "{hook} could not read the register ({cause}) and passed the call; it passes every call it gates until the read works."
UNQUOTED = (
    "An owner question is open and this reply or brief touches it without quoting it. Quote its words, sentence and rows below,"
    " and say from them whether the thing should exist before any code read."
)
CAPTURES = "owner-prompts.jsonl"
WORDS_SHOWN = 200
NOTE_CLIS = frozenset({"ccn", "cc-notes"})
REPO_FLAGS = frozenset({"-R", "--repo"})
LEDGER_VERBS = (("row", "set"), ("row", "rm"), ("sync",), ("edit",), ("rm",), ("archive",))
LEDGER_TOOLS = frozenset(f"__ledger_{verb}" for verb in ("row_set", "row_rm", "sync", "edit", "rm", "archive"))
ANSWER_TOOLS = {"__answer_add": "labels", "__answer_edit": "add_labels"}
LABEL_FLAGS = frozenset({"--label", "--add-label"})
AUTHORIZES = "authorizes:"
SHORTEST_PREFIX = 4
SEED = "register-seed"
GRADERS = ("fidelity", "finder", "walker", "end-state", "incident-closeout")
SEATS = {
    "classify": (True, (SEED,)),
    "names": (True, (SEED,)),
    "authorize": (True, (SEED,)),
    "supersede": (True, (SEED,)),
    "status": (False, GRADERS),
    "anchor": (False, ("anchor", SEED)),
}
SEAT_NAMES = {
    "classify": "the root, the register-seed lane and the drive's register lane",
    "names": "the root, the register-seed lane and the drive's register lane",
    "authorize": "the root, the register-seed lane and the drive's register lane",
    "supersede": "the root, the register-seed lane and the drive's register lane",
    "status": "the grader lanes: fidelity desks, finder, walker, end-state and incident-closeout",
    "anchor": "the anchor and register-seed lanes",
}
DESTINATION_WRITERS = frozenset({"cp", "rsync", "install", "ln"})
TARGET_WRITERS = frozenset({"mv", "rm", "rmdir", "touch", "chmod", "chown", "truncate", "unlink", "mkdir", "tee", "patch"})
IN_PLACE_EDITORS = frozenset({"sed", "gsed", "perl"})
LEDGER_WRITE = (
    "Warning, nothing was blocked: Only `rulings.py` writes the register ledger of `{program}`, so this call that edits it by hand is refused."
    " Make the change with the `rulings.py` verb that owns it, from the lane that owns that verb."
)
SEAT_REFUSED = "Warning, nothing was blocked: `rulings.py {verb}` runs only in {seats}, and this session is {lane}. Hand the change to one of those lanes."
AUTHORIZES_LABEL = (
    "Warning, nothing was blocked: An `authorizes:` label is written only by `rulings.py authorize`. Run that verb from the root instead of labelling the answer by hand."
)
WIDENED_TO = "fleet"
WIDENING = "widen"
TARGET_FLAGS = frozenset({"--targets", "--target", "--groups"})
RELEASE_VALUE_FLAGS = TARGET_FLAGS | {"--env", "--commit", "--stack"}
GATES_OWED = "Warning, nothing was blocked: gate rows the register holds as owed name what this call starts. Meet each before going on."
CONTRACT_MISSING = "CONTRACT-MISSING "
ACCEPTED = "accepted "
ACCEPTED_CHECK = f"{sys.executable} {script('rulings.py')} accepted"
READBACK_MISSING = (
    "Warning, nothing was blocked: lane `{lane}` has no read-back a fidelity desk accepted yet."
    " Post `rulings.py readback` as a `[readback]` contract so a desk grades it."
)
LANE = {"CLAUDE_LONG_RUNNING_LANE": "lane-a", "CLAUDE_LONG_RUNNING_DRIVE": "brook-drive"}
READY_REPORT = "python3 ledger.py report --ledger L1 --drive brook-drive --pr 42 --head abc1234 --lane lane-a --verdict clean --text 'READY #42 green'"
LEDGER_LIST = f"{sys.executable} {script('ledger.py')} -C /repo list"
OPEN_PR = json.dumps([{"pr": "41", "lane": "lane-a"}])
SHIP = ("ccx", "vcs", "ship")
NEW_BRANCH = "--new-branch"
CACHE_ENV = {"CLAUDE_CONFIG_DIR": DRIVE_CONFIG["CLAUDE_CONFIG_DIR"]}
CACHE = Path(DRIVE_CONFIG["CLAUDE_CONFIG_DIR"]) / "plugins" / "cache" / "skills" / "long-running" / "0.7.217"
HOOK_EDIT = (
    "The installed long-running plugin changes only through a reviewed pull request, so this write into its `{path}` is refused."
    " Fix the check in a worktree and ship it as a pull request."
)
TIMERS = fixture("brief-158695")
ROSTER = "Lane lane-x: drop the roster refresh timer."


def read_register(evt: BaseHookEvent, which: list[str]) -> dict:
    return json.loads(rulings(str(evt.cwd), "rows", *which, "--json", evt=evt))


def cause(error: Exception) -> str:
    detail = getattr(error, "stderr", None) or str(error) or type(error).__name__
    return " ".join(str(detail).split())[:200]


def blocker(evt: BaseHookEvent, key: str, text: str, to: str = ROOT_LANE) -> None:
    if evt.ctx.s.once(key, scope=BLOCKERS):
        post_blocker(evt, text, to=to)


def register_lane(evt: BaseHookEvent) -> str | None:
    entry = drive_entry(evt)
    return entry.get("register_lane") if entry else None


def live_successor(evt: BaseHookEvent, lane: str) -> str | None:
    """`lane` while `cci who` lists it live, else its newest live numbered successor (`register-seed-5` to `register-seed-6`), else None."""
    if not (entry := drive_entry(evt)):
        return None
    argv = ["cci", "who", "--drive", bus_name(entry), "--json"]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=CCN_TIMEOUT_SECONDS, env=dict(reqenv.env_map()))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode:
        return None
    live = [entry for line in done.stdout.splitlines() if line.strip() and (entry := json.loads(line)).get("live")]
    if any(entry.get("lane") == lane for entry in live):
        return lane
    family = re.compile(rf"{re.escape(SUCCESSOR_NUMBER.sub('', lane))}(?:-\d{{1,2}})*")
    found = [entry for entry in live if family.fullmatch(entry.get("lane") or "")]
    return max(found, key=lambda entry: entry["announced_at"])["lane"] if found else None


def unclassified_route(evt: BaseHookEvent, count: int) -> tuple[str, str]:
    if not (lane := register_lane(evt)):
        return ROOT_LANE, ""
    if live := live_successor(evt, lane):
        return live, ""
    return ROOT_LANE, REGISTER_LANE_GONE.format(lane=lane, these="these captures" if count > 1 else "this capture")


def failed_open(evt: BaseHookEvent, hook: str, error: Exception) -> None:
    blocker(evt, f"{hook}:read", READ_FAILED.format(hook=hook, cause=cause(error)))


def folded(text: str) -> str:
    return " ".join(text.split()).casefold()


def posted(evt: BaseHookEvent, kinds: frozenset[str]) -> list[str]:
    return [
        posted_text(values)
        for call in evt.command.calls("cci")
        if (values := CCI_POST.bind(call).values).get("words", ())[:1] == ("post",) and kinds & set(values.get("kind", ()))
    ]


def citing_lines(evt: BaseHookEvent) -> list[str]:
    return CITING_LINE.findall(inbox_text(evt)) + posted(evt, CITING_KINDS)


def captures(register: dict) -> Path:
    return Path(register["state_dir"]) / CAPTURES


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


def excerpt(capture: dict, width: int) -> str:
    return " ".join(capture["text"].split())[:width]


def standing(entries: list[dict]) -> list[dict]:
    gone = {digest for entry in entries for digest in entry.get("supersedes", [])}
    return [entry for entry in entries if entry["digest"] not in gone]


def report_unclassified(evt: BaseHookEvent, register: dict) -> None:
    prompts = captures(register)
    if not prompts.is_file():
        return
    classified = set(register["classified"])
    fresh = [
        capture
        for capture in standing([json.loads(line) for line in prompts.read_text().splitlines() if line.strip()])
        if capture["digest"] not in classified and evt.ctx.s.once(f"unclassified:{capture['digest']}", scope=BLOCKERS)
    ]
    if not fresh:
        return
    to, note = unclassified_route(evt, len(fresh))
    if len(fresh) == 1:
        [capture] = fresh
        text = UNCLASSIFIED.format(digest=capture["digest"], at=capture["at"], program=register["program"], excerpt=excerpt(capture, EXCERPT))
    else:
        entries = "; ".join(BATCH_ENTRY.format(digest=c["digest"], at=c["at"], excerpt=excerpt(c, BATCH_EXCERPT)) for c in fresh)
        text = UNCLASSIFIED_BATCH.format(count=len(fresh), program=register["program"], captures=entries)
    post_blocker(evt, text + note, to=to)


def is_answer(evt: BaseHookEvent, token: str) -> bool:
    done = ccn(str(evt.cwd), "answer", "show", token, "--json")
    if done.returncode == NOT_FOUND:
        return False
    done.check_returncode()
    return True


def row_answers(row: dict) -> list[str]:
    repeats = json.loads(row.get("repeats") or "[]")
    return [str(row.get("answer") or ""), *(json.dumps(repeat.get("answer")) for repeat in repeats)]


def unrowed(evt: BaseHookEvent, lines: list[str], register: dict) -> list[str]:
    rowed = {found[:SHORT] for row in register["rows"].values() for found in ANSWER_ID.findall(" ".join(row_answers(row)))}
    cited = dict.fromkeys(token for line in lines for token in ANSWER_ID.findall(line))
    return [token for token in cited if token[:SHORT] not in rowed and is_answer(evt, token)]


def go(text: str, kind: str = "go") -> str:
    return f"cci post --lane root --kind {kind} --to walker --text '{text}'"


def register_case(command: str | None = None, state: str = "quiet", *answers: tuple[str, str | tuple[int, str]], **extra: object) -> Input:
    fields = {"command": command} if command is not None else {}
    return Input(**({"env": DRIVE_CONFIG} | fields | extra), state=ACTIVE, cwd=CWD, commands=readings(state, *answers))


def spawn(name: str, prompt: str, state: str = "quiet", **extra: object) -> Input:
    return register_case(None, state, tool="Agent", tool_input={"name": name, "prompt": prompt}, **extra)


@on(
    Event.PreToolUse,
    only_if=[DriveActive(), Tool("Bash", "Agent", "Edit", "Write")],
    skip_if=[FromSubagent(), Annotated("raw")],
    tests=separate("register_gates.register_gate", {
        register_case(go(f"cut web under {ROWED[:7]}"), "quiet", (ROWED[:7], shown(ROWED))): Allow(),
        register_case(go(f"cut web under {UNROWED[:7]}"), "quiet", (UNROWED[:7], shown(UNROWED))): Warn(
            pattern=r"^Warning, nothing was blocked: this GO or DECIDE cites `1dfd09d`, and register `brook` has no row for it\."
        ),
        register_case(go(f"lanes stop until {UNROWED[:7]} lands", "decide"), "quiet", (UNROWED[:7], shown(UNROWED))): Warn(pattern=r"`1dfd09d`, and register"),
        register_case(
            f'echo "GO root (4:50 AM) walker: cut web under {UNROWED}" >> /s/inbox/deploy-go.md', "quiet", (UNROWED, shown(UNROWED))
        ): Warn(pattern=r"has no row for it"),
        register_case(f'echo "STATUS: previous GO root (4:50 AM) under {UNROWED[:7]} was withdrawn" >> /s/inbox/deploy-go.md', "repeat"): Allow(),
        register_case(go("land 1234567 now"), "quiet", ("1234567", shown("1234567"))): Warn(pattern=r"`1234567`, and register"),
        register_case(f"cci post --lane root --kind go --to walker --path {GO_PATH}", "quiet", (UNROWED[:7], shown(UNROWED))): Warn(
            pattern=r"`1dfd09d`, and register"
        ),
        register_case(go(f"land {SHA[:7]} now"), "quiet", (SHA[:7], (3, "not-found: entity not found"))): Allow(),
        register_case(go(f"land head {SHA} now"), "quiet", (SHA, (3, "not-found: entity not found"))): Allow(),
        register_case(go(f"lift the hold under {UNROWED[:7]}", "lift")): Allow(),
        register_case(go(f"cut web under {REPEATED[:7]}"), "quiet", (REPEATED[:7], shown(REPEATED))): Allow(),
        register_case(go(f"cut web under {UNROWED[:7]}"), "quiet", (UNROWED[:7], (1, "ccn: store locked"))): Allow(),
        register_case(go("cut web"), "repeat"): Allow(),
        spawn("lane-x", "Lane lane-x: build the slice verb.", "repeat"): Allow(),
        register_case("codex-ask -m sol /s/briefs/lane-x.md", "repeat"): Allow(),
        register_case("cc-present push /tmp/board.json", "repeat"): Allow(),
        spawn("incident-api-500s-fix", "Fix the api 500s.", "repeat"): Allow(),
        register_case("cc-present reply --block r3 --body 'Done.'", "repeat"): Allow(),
        register_case("ls", "repeat"): Allow(),
        spawn("lane-x", "Lane lane-x: build the slice verb."): Allow(),
        Input(command=go("cut web"), state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG, commands={ROWS: (1, "ccn: no ledger"), "cci post": "#1"}): Allow(),
        Input(command=go("cut web"), cwd=CWD, commands=readings("repeat")): Allow(),
        register_case(go("cut web"), "repeat", agent_id="a1b2c3"): Allow(),
    }),
)
def register_gate(evt: BaseHookEvent) -> HookResult | None:
    citing = citing_lines(evt)
    moment = key_moment(evt)
    if not (citing or moment) or not (which := drive_args(evt)) or incident_fix_spawn(evt):
        return None
    try:
        register = read_register(evt, which)
        report_unclassified(evt, register)
        missing = unrowed(evt, citing, register)
    except READ_ERRORS as error:
        failed_open(evt, "register_gate", error)
        return None
    missing = once_per_hour(evt, missing)
    return evt.context(UNROWED_ANSWER.format(ids=", ".join(missing), program=register["program"])) if missing else None


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


def bears(question: dict, text: str, blocks: set[str], rows: dict) -> bool:
    names = [name for key in question["rows"] if key in rows for name in json.loads(rows[key].get("names") or "[]")]
    needles = (question["term"], question["sentence"], *question["rows"], *names)
    return bool(blocks & set(question["blocks"])) or any(folded(needle) in folded(text) for needle in needles if needle.strip())


def quotes(text: str, question: dict) -> bool:
    return folded(question["text"]) in folded(text)


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
    return Input(**({"env": DRIVE_CONFIG} | extra), command=text, state=ACTIVE, cwd=CWD, commands=readings(state))


def asks(name: str, prompt: str, state: str, **extra: object) -> Input:
    return Input(**({"env": DRIVE_CONFIG} | extra), tool="Agent", tool_input={"name": name, "prompt": prompt}, state=ACTIVE, cwd=CWD, commands=readings(state))


@on(
    Event.PreToolUse,
    only_if=[DriveActive(), Tool("Bash", "Agent")],
    skip_if=[Annotated("raw")],
    tests=separate("register_gates.owner_question_gate", {
        asks("reply-lookup", fixture("brief-158585"), "question-lookup"): Allow(),
        asks("timers-writer", TIMERS, "question-timers"): Allow(),
        question(reply("c7-timers", fixture("correction-1244")), "question-timers"): Warn(pattern=r"touches it without quoting it"),
        asks("ship-pr", 'The owner asked "what is a relay slot? why is this here?"; this PR touches none of it. Open the PR.', "question-timers"): Allow(),
        asks("lane-x", "Lane lane-x: fix the flaky api test.", "question-timers"): Allow(),
        asks("register-seed", "Lane register-seed: classify the eleven queued captures.", "question-chat"): Allow(),
        asks("register-seed", "Lane register-seed: classify the record repair step captures.", "question-chat"): Warn(pattern=r'"what is this record repair step\?'),
        asks("lane-x", "Lane lane-x: explain the relay slot from src/relay/slot.go.", "question-timers"): Warn(pattern=r"touches it without quoting it"),
        asks("lane-x", ROSTER, "question-timers"): Warn(
            pattern=r'^An owner question is open and this reply or brief touches it without quoting it\..*\n~{12}\n- The owner asked: "what is a relay slot\? why is this here\?"'
        ),
        question(reply("c7-timers", "Every 30 s a worker asks the server for new jobs."), "question-timers"): Warn(pattern=r"touches it without quoting it"),
        question(f"orca-launch.sh roster-writer standard xhigh {OWNER_QUESTION / 'brief-roster.md'}", "question-timers"): Warn(pattern=r"roster refresh"),
        question(f"codex-ask -m sol {OWNER_QUESTION / 'brief-158695.md'}", "question-timers"): Allow(),
        asks("lane-x", ROSTER, "question-closed"): Allow(),
        asks("lane-x", ROSTER, "quiet"): Allow(),
        asks("incident-api-500s-fix", f"Fix the api 500s. {ROSTER}", "question-timers"): Allow(),
        question("cc-present push /tmp/board.json", "question-timers"): Allow(),
        question("ls", "question-timers"): Allow(),
        Input(tool="Agent", tool_input={"name": "lane-x", "prompt": ROSTER}, state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG, commands={ROWS: (1, "boom"), "cci post": "#1"}): Allow(),
        Input(tool="Agent", tool_input={"name": "lane-x", "prompt": ROSTER}, cwd=CWD, commands=readings("question-timers")): Allow(),
    }),
)
def owner_question_gate(evt: BaseHookEvent) -> HookResult | None:
    if incident_fix_spawn(evt) or not (which := drive_args(evt)):
        return None
    try:
        text, briefs = answer_text(evt)
        if not text:
            return None
        register = read_register(evt, which)
        blocks = {block for args in replies(evt) for block in option(args, "--block")}
        unquoted = [found for found in open_questions(register) if bears(found, text, blocks, register["rows"]) and not quotes(text, found)]
    except READ_ERRORS as error:
        failed_open(evt, "owner_question_gate", error)
        return None
    due = set(once_per_hour(evt, [f"owner-question:{found['term']}" for found in unquoted]))
    if not (unquoted := [found for found in unquoted if f"owner-question:{found['term']}" in due]):
        return None
    shown = [fenced("\n".join(asked(found, register["rows"]) for found in unquoted)), *([fenced(briefs.strip())] if briefs else [])]
    return evt.context("\n".join([UNQUOTED, *shown]))


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
    tests=separate("register_gates.close_replied_questions", {
        question(reply("c7-repair", "Done."), "question-timers"): Allow(),
        question("cc-present push /tmp/board.json", "question-timers"): Allow(),
    }),
)
def close_replied_questions(evt: BaseHookEvent) -> HookResult | None:
    if blocks := {block for args in replies(evt) for block in option(args, "--block")}:
        close(evt, "close_replied_questions", lambda found: bool(blocks & set(found.get("blocks") or ())), f"reply {' '.join(sorted(blocks))}")
    return None


@on(
    Event.Stop,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    tests=separate("register_gates.close_chat_questions", {
        Input(state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG, commands=readings("question-timers")): Allow(),
        Input(state=[CompactionState(active=True)], cwd=CWD): Allow(),
    }),
)
def close_chat_questions(evt: BaseHookEvent) -> HookResult | None:
    close(evt, "close_chat_questions", lambda found: not found.get("blocks"), "turn end")
    return None


def note_words(call: Call) -> list[str]:
    words: list[str] = []
    skip = False
    for arg in call.args:
        if skip:
            skip = False
        elif arg in REPO_FLAGS:
            skip = True
        elif not arg.startswith(("--repo=", "-R")):
            words.append(arg)
    return words


def names_ledger(token: str, register: dict) -> bool:
    return token == f"register: {register['program']}" or (len(token) >= SHORTEST_PREFIX and register["ledger"].startswith(token))


def ledger_targets(evt: BaseHookEvent) -> list[str]:
    targets = [
        token
        for call in evt.command.calls()
        if call.name in NOTE_CLIS
        for words in [note_words(call)]
        if words[:1] == ["ledger"]
        for verb in LEDGER_VERBS
        if tuple(words[1 : 1 + len(verb)]) == verb
        for token in [next((word for word in words[1 + len(verb) :] if not word.startswith("-")), "")]
    ]
    tool = evt.tool_name or ""
    if tool.startswith("mcp__") and tool.endswith(tuple(LEDGER_TOOLS)):
        targets.append(str(evt._tool_input.get("id") or ""))
    return targets


def labels(args: tuple[str, ...]) -> list[str]:
    paired = [value for flag, value in zip(args, args[1:], strict=False) if flag in LABEL_FLAGS]
    return paired + [arg.split("=", 1)[1] for arg in args if arg.split("=", 1)[0] in LABEL_FLAGS and "=" in arg]


def writes_authorizes(evt: BaseHookEvent) -> bool:
    written = [
        label
        for call in evt.command.calls()
        if call.name in NOTE_CLIS
        for words in [note_words(call)]
        if words[:1] == ["answer"] and words[1:2] in (["add"], ["edit"])
        for label in labels(tuple(words))
    ]
    tool = evt.tool_name or ""
    written += [
        str(label)
        for suffix, field in ANSWER_TOOLS.items()
        if tool.startswith("mcp__") and tool.endswith(suffix)
        for label in evt._tool_input.get(field) or ()
    ]
    return any(label.startswith(AUTHORIZES) for label in written)


def seated(verb: str, lane: str | None, register: str | None) -> bool:
    root, lanes = SEATS[verb]
    if lane is None:
        return root
    return lane.startswith(lanes) or (root and lane == register)


def unseated_verbs(evt: BaseHookEvent) -> list[str]:
    verbs = [
        run[1]
        for call in evt.command.calls()
        if (run := program((call.name, *call.args)))[1:] and Path(run[0]).name == "rulings.py" and run[1] in SEATS
    ]
    if not verbs:
        return []
    lane, register = lane_name(evt), register_lane(evt)
    return [verb for verb in verbs if not seated(verb, lane, register)]


def lane_label(lane: str | None) -> str:
    return "the root" if lane is None else f"lane `{lane or 'unnamed'}`"


def lane(name: str) -> dict[str, str]:
    return DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": name}


@on(
    Event.PreToolUse,
    tests=separate("register_gates.register_write_guard", {
        register_case(f"ccn ledger row set {LEDGER[:7]} rule/0a1b2c3 --field status=met"): Warn(
            pattern=r"^Warning, nothing was blocked: Only `rulings\.py` writes the register ledger of `brook`"
        ),
        register_case(f"ccn -R /repo ledger sync {LEDGER} --file -"): Warn(pattern=r"`brook`"),
        register_case("ccn ledger archive 'register: brook'"): Warn(pattern=r"`brook`"),
        register_case(f"ccn -R/repo ledger rm {LEDGER[:7]}"): Warn(pattern=r"`brook`"),
        register_case("ccn ledger edit 9f8e7d6 --body 'register: brook'"): Allow(),
        register_case("ccn ledger row set 9f8e7d6 rule/0a1b2c3 --field status=met"): Allow(),
        register_case(f"ccn ledger show {LEDGER[:7]}"): Allow(),
        register_case(None, tool="mcp__plugin_cc-notes_cc-notes__ledger_row_set", tool_input={"id": LEDGER[:7], "key": "rule/0a1b2c3"}): Warn(
            pattern=r"`brook`"
        ),
        register_case(None, tool="mcp__plugin_cc-notes_cc-notes__ledger_show", tool_input={"id": LEDGER[:7]}): Allow(),
        register_case(f"ccn ledger row set {LEDGER[:7]} rule/0a1b2c3 --field status=met", "quiet", env=lane("register-seed")): Warn(pattern=r"`brook`"),
        Input(command="python3 rulings.py classify 629293ae14388dc9", state=ACTIVE, env=DRIVE_CONFIG, cwd=CWD): Allow(),
        Input(command="python3 rulings.py classify 629293ae14388dc9", state=ACTIVE, cwd=CWD, env=lane("register-seed")): Allow(),
        Input(command="python3 rulings.py classify 629293ae14388dc9", state=ACTIVE, cwd=CWD, env=lane("capture-desk")): Allow(),
        Input(command="python3 rulings.py classify 629293ae14388dc9", state=ACTIVE, cwd=CWD, env=lane("p1-hooks")): Warn(
            pattern=r"^Warning, nothing was blocked: `rulings\.py classify` runs only in the root, the register-seed lane and the drive's register lane, and this session is lane `p1-hooks`\."
        ),
        Input(command="rulings.py status rule/0a1b2c3 met --evidence '#1'", state=ACTIVE, env=DRIVE_CONFIG, cwd=CWD): Warn(pattern=r"this session is the root\."),
        Input(command="rulings.py status rule/0a1b2c3 met --evidence '#1'", state=ACTIVE, cwd=CWD, env=lane("fidelity-desk")): Allow(),
        Input(command="rulings.py anchor rule/0a1b2c3", state=ACTIVE, cwd=CWD, env=lane("anchor-lane")): Allow(),
        Input(command="rulings.py anchor rule/0a1b2c3", state=ACTIVE, cwd=CWD, env=lane("walker")): Warn(pattern=r"anchor and register-seed lanes"),
        Input(command="rulings.py anchor rule/0a1b2c3", state=ACTIVE, cwd=CWD, env=lane("capture-desk")): Warn(pattern=r"anchor and register-seed lanes"),
        Input(command="rulings.py rows --program brook --json", state=ACTIVE, cwd=CWD, env=lane("p1-hooks")): Allow(),
        Input(command="ccn answer edit 8b1048a --add-label authorizes:rule/0a1b2c3", state=ACTIVE, env=DRIVE_CONFIG, cwd=CWD): Warn(
            pattern=r"^Warning, nothing was blocked: An `authorizes:` label is written only by `rulings\.py authorize`\."
        ),
        Input(command="ccn answer add 'Ship it?' --label=authorizes:rule/0a1b2c3", state=ACTIVE, env=DRIVE_CONFIG, cwd=CWD): Warn(pattern=r"authorizes:"),
        Input(command="ccn answer add 'Ship it?' --label scope:durable", state=ACTIVE, env=DRIVE_CONFIG, cwd=CWD): Allow(),
        Input(tool="mcp__plugin_cc-notes_cc-notes__answer_edit", tool_input={"id": "8b1048a", "add_labels": ["authorizes:rule/0a1b2c3"]}, state=ACTIVE, env=DRIVE_CONFIG, cwd=CWD): Warn(
            pattern=r"authorizes:"
        ),
        Input(command="python3 rulings.py classify 629293ae14388dc9", cwd=CWD, env={"CLAUDE_LONG_RUNNING_LANE": "p1-hooks"}): Allow(),
        Input(command=f"ccn ledger row set {LEDGER[:7]} rule/0a1b2c3", state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG, commands={ROWS: (1, "ccn: no ledger"), "cci post": "#1"}): Allow(),
    }),
)
def register_write_guard(evt: BaseHookEvent) -> HookResult | None:
    if not drive_args(evt):
        return None
    if (verbs := unseated_verbs(evt)) and once_per_hour(evt, [f"seat:{verbs[0]}"]):
        return evt.context(SEAT_REFUSED.format(verb=verbs[0], seats=SEAT_NAMES[verbs[0]], lane=lane_label(lane_name(evt))))
    if writes_authorizes(evt):
        return evt.context(AUTHORIZES_LABEL)
    if not (targets := ledger_targets(evt)):
        return None
    try:
        register = read_register(evt, drive_args(evt) or [])
    except READ_ERRORS as error:
        failed_open(evt, "register_write_guard", error)
        return None
    hits = any(names_ledger(token, register) for token in targets)
    return evt.context(LEDGER_WRITE.format(program=register["program"])) if hits and once_per_hour(evt, [LEDGER_WRITE]) else None


def plugin_cache() -> Path:
    return Path(reqenv.getenv("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser() / "plugins" / "cache" / "skills" / "long-running"


def absolute(raw: str, cwd: Path) -> Path:
    return Path(os.path.abspath(cwd / Path(raw).expanduser()))


def bash_writes(evt: BaseHookEvent) -> list[Path]:
    found: list[Path] = []
    for call in evt.command.calls():
        cwd = Path(call.cwd or evt.cwd)
        found += [absolute(redirect.target.strip("'\""), cwd) for redirect in call.redirects if redirect.op in FILE_WRITES]
        operands = [absolute(target.value, cwd) for target in call.targets if target.value is not None]
        if call.name in DESTINATION_WRITERS:
            found += operands[-1:]
        elif call.name in TARGET_WRITERS or (call.name in IN_PLACE_EDITORS and any(in_place(arg) for arg in call.args)):
            found += operands
    return found


def in_place(arg: str) -> bool:
    return arg.startswith("--in-place") or (arg.startswith("-") and not arg.startswith("--") and "i" in arg[1:])


def written_paths(evt: BaseHookEvent) -> list[Path]:
    if evt.tool_name == "Bash":
        return bash_writes(evt)
    return [absolute(str(evt.file.path), Path(evt.cwd))] if evt.file is not None else []


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash", "Edit", "Write", "MultiEdit", "NotebookEdit")],
    tests=separate("register_gates.hook_edit_guard", {
        Input(tool="Edit", tool_input={"file_path": f"{CACHE}/capt-hook/hooks/register_gates.py", "old_string": "a", "new_string": "b"}, state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG): Block(
            pattern=r"^The installed long-running plugin changes only through a reviewed pull request, so this write into its `0\.7\.217/capt-hook/hooks/register_gates\.py` is refused\."
        ),
        Input(command=f"sed -i '' s/a/b/ {CACHE}/skills/long-running/scripts/rulings.py", state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG): Block(pattern=r"rulings\.py` is refused"),
        Input(command=f"echo x >> {CACHE}/capt-hook/hooks/board_text_gate.py", state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG): Block(pattern=r"is refused"),
        Input(command=f"cp /tmp/x.py {CACHE}/capt-hook/hooks/x.py", state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG): Block(pattern=r"is refused"),
        Input(command=f"cp {CACHE}/capt-hook/hooks/x.py /tmp/x.py", state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG): Allow(),
        Input(command=f"cat {CACHE}/capt-hook/hooks/x.py", state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG): Allow(),
        Input(command=f"mv {CACHE}/capt-hook/hooks/x.py /tmp/x.py", state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG): Block(pattern=r"is refused"),
        Input(command=f"perl -pi -e s/a/b/ {CACHE}/capt-hook/hooks/x.py", state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG): Block(pattern=r"is refused"),
        Input(command=f"sed -e s/a/b/ {CACHE}/capt-hook/hooks/x.py", state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG): Allow(),
        Input(tool="Write", tool_input={"file_path": "/repo/plugins/long-running/capt-hook/hooks/x.py", "content": "x"}, state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG): Allow(),
        Input(tool="Write", tool_input={"file_path": f"{CACHE}/x.py", "content": "x"}, cwd=CWD, env=CACHE_ENV): Allow(),
    }),
)
def hook_edit_guard(evt: BaseHookEvent) -> HookResult | None:
    if not drive_args(evt):
        return None
    cache = plugin_cache()
    inside = next((path for path in written_paths(evt) if path.is_relative_to(cache)), None)
    return evt.block(HOOK_EDIT.format(path=inside.relative_to(cache))) if inside else None


def flag_targets(flag: str, value: str) -> list[str]:
    return value.split(",") if flag in TARGET_FLAGS else []


def start_targets(args: tuple[str, ...]) -> list[str]:
    named: list[str] = []
    pending = ""
    for arg in args:
        flag, equals, value = arg.partition("=")
        if pending:
            named += flag_targets(pending, arg)
            pending = ""
        elif equals and flag in RELEASE_VALUE_FLAGS:
            named += flag_targets(flag, value)
        elif arg in RELEASE_VALUE_FLAGS:
            pending = arg
        elif not arg.startswith("-"):
            named += arg.split(",")
    return [target for target in named if target]


def release_targets(evt: BaseHookEvent) -> list[str]:
    found = []
    for call in evt.command.calls("ci"):
        args = call.args
        if args[:2] == ("release", "start"):
            found += start_targets(args[2:])
        elif args[:3] == ("release", "channel", "promote") and args[4:5] == (WIDENED_TO,):
            found.append(WIDENING)
    return found


def go_texts(evt: BaseHookEvent) -> list[str]:
    return GO_LINE.findall(inbox_text(evt)) + posted(evt, GO_KINDS)


def names_token(text: str, token: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(token)}(?!\w|\.\w)", text) is not None


def owed_gates(rows: dict, texts: list[str]) -> list[str]:
    return [
        f"- {key} ({', '.join(named)}): {row.get('words', '')}"
        for key, row in rows.items()
        if row.get("kind") == "gate" and row.get("status") == "owed"
        for named in [[token for token in json.loads(row.get("blocks") or "[]") if any(names_token(text, token) for text in texts)]]
        if named
    ]


def cut_case(command: str, **extra: object) -> Input:
    return Input(command=command, state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG, commands=readings("quiet"), **extra)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash", "Edit", "Write")],
    tests=separate("register_gates.go_and_cut_gate", {
        cut_case("tools/ci release channel promote next fleet"): Warn(pattern=r"^Warning, nothing was blocked[\s\S]*- rule/7e8f901 \(widen\): Run every scenario"),
        cut_case("tools/ci release channel promote fleet next"): Allow(),
        cut_case("tools/ci release start sand && tools/ci release channel promote next fleet"): Warn(pattern=r"\(widen\)"),
        cut_case("tools/ci release start sand"): Allow(),
        cut_case("cci post --lane root --kind go --to walker --text 'start step 1.7 now'"): Warn(pattern=r"- rule/7e8f901 \(1\.7\)"),
        cut_case("cci post --lane root --kind go --to walker --text 'start step 1.75 now'"): Allow(),
        cut_case("cci post --lane root --kind go --to walker --text 'start step 1.7.'"): Warn(pattern=r"\(1\.7\)"),
        cut_case("cci post --lane root --kind note --to walker --text 'step 1.7 is next'"): Allow(),
        Input(command="tools/ci release channel promote next fleet", cwd=CWD, commands=readings("quiet")): Allow(),
        Input(command="tools/ci release channel promote next fleet", state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG, commands={ROWS: (1, "ccn: no ledger"), "cci post": "#1"}): Allow(),
    }),
)
def go_and_cut_gate(evt: BaseHookEvent) -> HookResult | None:
    texts = go_texts(evt) + release_targets(evt)
    if not texts or not (which := drive_args(evt)):
        return None
    try:
        owed = owed_gates(read_register(evt, which)["rows"], texts)
    except READ_ERRORS as error:
        failed_open(evt, "go_and_cut_gate", error)
        return None
    owed = once_per_hour(evt, owed)
    return evt.context("\n".join([GATES_OWED, fenced("\n".join(owed))])) if owed else None


def unflagged(argv: tuple[str, ...]) -> tuple[str, ...]:
    kept: list[str] = []
    values = iter(argv)
    for arg in values:
        if arg in GH_REPO_FLAGS:
            next(values, None)
        elif not arg.startswith(("--repo=", "-R=")):
            kept.append(arg)
    return tuple(kept)


def runs(evt: BaseHookEvent) -> list[tuple[str, ...]]:
    return [unflagged(program((call.name, *call.args))) for call in evt.command.calls()]


def opens_pr(run: tuple[str, ...]) -> bool:
    return any(run[: len(argv)] == argv for argv in OPENERS)


def new_branch(run: tuple[str, ...]) -> bool:
    return run[:3] == SHIP and any(arg == NEW_BRANCH or arg.startswith(f"{NEW_BRANCH}=") for arg in run)


def reports_ready(run: tuple[str, ...]) -> bool:
    return "ledger.py" in {Path(arg).name for arg in run[:2]} and "report" in run and any(text.startswith("READY") for text in option(run, "--text"))


def owning_lane(evt: BaseHookEvent) -> str | None:
    return (evt.agent_id and drive_lane(evt, helper)) or lane_name(evt)


def incident_lane(evt: BaseHookEvent, lane: str) -> bool:
    return bool(INCIDENT_LANE.match(lane) or (FIX_LANE.search(lane) and open_incidents(evt, read_incident_source)))


def has_pr(entry: dict, lane: str) -> bool:
    argv = [sys.executable, str(script("ledger.py")), "-C", entry["checkout"], "list", "--ledger", entry["ledger"], "--repo", entry["repo"], "--lane", lane, "--json"]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=CCN_TIMEOUT_SECONDS, env=dict(reqenv.env_map()))
    done.check_returncode()
    return bool(json.loads(done.stdout))


def acceptance(evt: BaseHookEvent, lane: str, drive: str) -> str:
    try:
        said = rulings(str(evt.cwd), "accepted", "--lane", lane, "--drive", drive, evt=evt)
    except subprocess.CalledProcessError as refused:
        if not (refused.stdout or "").startswith(CONTRACT_MISSING):
            raise
        said = refused.stdout
    if not said.startswith((ACCEPTED, CONTRACT_MISSING)):
        raise ValueError(f"`rulings.py accepted` printed {said.strip()[:EXCERPT]!r}")
    return said


def gated(command: str, said: str = "accepted lane-a\n", env: dict[str, str] = LANE, incidents: str | tuple[int, str] = "[]", prs: str | tuple[int, str] = "[]") -> Input:
    commands = {ACCEPTED_CHECK: said, LEDGER_LIST: prs, "cci post": "#1", " ".join(INCIDENT_STORE): incidents, " ".join(INCIDENT_DIGEST[:2]): "{}", "cci grep": ""}
    return Input(command=command, cwd=CWD, env=DRIVE_CONFIG | env, commands=commands)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests=separate("register_gates.readback_gate", {
        gated("ccx vcs ship -m 'long-running: ✨ x' --new-branch lane-a-x"): Allow(),
        gated("ccx vcs ship -m 'long-running: ✨ x'", "CONTRACT-MISSING lane-a: no fidelity desk has graded a read-back\n"): Warn(
            pattern=r"^Warning, nothing was blocked: lane `lane-a` has no read-back a fidelity desk accepted yet\.[\s\S]*CONTRACT-MISSING lane-a: no fidelity desk has graded a read-back\n~+$"
        ),
        gated("gh pr create --title x --body-file b.md", "CONTRACT-MISSING lane-a: the newest grade, #9, is missing: rule/bbbbbbb\n"): Warn(pattern=r"the newest grade, #9, is missing"),
        gated("gh -R yasyf/cc-skills pr create --title x --body-file b.md", "CONTRACT-MISSING lane-a: none\n"): Warn(pattern=r"has no read-back"),
        gated("ccx vcs ship -m x", "CONTRACT-MISSING lane-a: the grade #9 covers slice-hash 0; the lane's slice is now 1\n", prs=OPEN_PR): Allow(),
        gated("ccx vcs stack continue", "CONTRACT-MISSING lane-a: none\n", prs=OPEN_PR): Allow(),
        gated("ccx vcs ship -m x --new-branch lane-a-y", "CONTRACT-MISSING lane-a: none\n", prs=OPEN_PR): Warn(pattern=r"has no read-back"),
        gated(READY_REPORT, "CONTRACT-MISSING lane-a: no fidelity desk has graded a read-back\n", prs=OPEN_PR): Warn(pattern=r"has no read-back"),
        gated(READY_REPORT.replace("READY #42 green", "STATE #42 waiting on CI"), "CONTRACT-MISSING lane-a: none\n"): Allow(),
        gated("git push origin HEAD", "CONTRACT-MISSING lane-a: none\n"): Allow(),
        gated("ccx vcs ship -m x", "CONTRACT-MISSING x: none\n", env={"CLAUDE_LONG_RUNNING_LANE": ""}): Allow(),
        gated("ccx vcs ship -m x", "CONTRACT-MISSING x: none\n", env={"CLAUDE_LONG_RUNNING_DRIVE": ""}): Allow(),
        gated("ccx vcs ship -m x", "CONTRACT-MISSING inc-api-fix: none\n", env={"CLAUDE_LONG_RUNNING_LANE": "inc-api-fix"}): Allow(),
        gated("ccx vcs ship -m x", "CONTRACT-MISSING alerts-api-0305-fix: none\n", env={"CLAUDE_LONG_RUNNING_LANE": "alerts-api-0305-fix"}, incidents=OPEN_FIX): Allow(),
        gated("ccx vcs ship -m x", "CONTRACT-MISSING alerts-api-0305-fix: none\n", env={"CLAUDE_LONG_RUNNING_LANE": "alerts-api-0305-fix"}): Warn(pattern=r"`alerts-api-0305-fix`"),
        gated("ccx vcs ship -m x", "CONTRACT-MISSING alerts-api-0305-fix: none\n", env={"CLAUDE_LONG_RUNNING_LANE": "alerts-api-0305-fix"}, incidents=(1, "store down")): Allow(),
        gated("ccx vcs ship -m x", "CONTRACT-MISSING lane-a: none\n", prs=(1, "ccn down")): Allow(),
        gated("ccx vcs ship -m x", "Traceback: boom\n"): Allow(),
    }),
)
def readback_gate(evt: BaseHookEvent) -> HookResult | None:
    called = runs(evt)
    always = any(new_branch(run) or reports_ready(run) for run in called)
    if not always and not any(opens_pr(run) for run in called):
        return None
    try:
        if not (lane := owning_lane(evt)) or not (entry := drive_entry(evt)) or incident_lane(evt, lane):
            return None
        if not always and has_pr(entry, lane):
            return None
        said = acceptance(evt, lane, entry["drive"])
    except READ_ERRORS as error:
        failed_open(evt, "readback_gate", error)
        return None
    if not said.startswith(CONTRACT_MISSING) or not once_per_hour(evt, [f"readback:{lane}"]):
        return None
    return evt.context("\n".join([READBACK_MISSING.format(lane=lane), fenced(said.strip())]))
