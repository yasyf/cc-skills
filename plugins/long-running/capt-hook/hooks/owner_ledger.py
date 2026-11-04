from __future__ import annotations

import fcntl
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, Tool, Warn, WorkflowState, hook, on, workflow_state
from captain_hook.util import reqenv

from .compaction_handoff import CCN_TIMEOUT_SECONDS, COMMUNICATOR_LANE, Communicator, CompactionState, bus_name, communicator_tests, drive_entry
from .owner_facing import REPLY_WINDOW
from .register_capture import CREDENTIAL, REDACTED, STAMP, slack_dms
from .register_gates import written_paths
from .root_context import PACIFIC, OwnerPrompt, answers, message_label, ruling_body
from .tests.register_fixtures import CWD, DRIVE_CONFIG

ACTIVE = [CompactionState(active=True, communicator=True)]
LEDGER_DIR = "owner-relay"
LEDGER = "owner-ledger.md"
LOCK = ".owner-ledger.lock"
HEADER = "| id | time | message | routed | state |\n|---|---|---|---|---|\n"
ROW = re.compile(r"^\| (m(\d+)(?:\.\d+)?) \| ([^|]*) \| (.*) \| [^|]* \| (open|responded|addressed|no-order) \|$", re.MULTILINE)
PARAGRAPH = re.compile(r"\n\s*\n")
AFTER_QUESTION = re.compile(r"(?<=\?)\s+")
BEFORE_ALSO = re.compile(
    r"(?:[,;.]\s*|\s+and\s+|\n\s*)(?=also\W+(?:add|build|bring|change|check|close|consider|cut|delete|design|dig|drop|explain|figure|find|fix|get|give|keep|land|let|make|merge|move|mute|put|raise|redesign|release|remove|restore|run|send|ship|show|spin|start|stop|switch|tell|turn|use|write)\b)",
    re.IGNORECASE,
)
COUNTED = re.compile(r"\b(two|three|four|five|six|[2-6])\s+(?:\w+\s+)?(?:issues|things|questions|asks|problems|bugs)\b", re.IGNORECASE)
COUNTS = ("two", "three", "four", "five", "six")
CLAUSE = re.compile(r"\s*[,;]\s*")
ASK_WORDS = 4
MOST_ASKS = 6
ROW_TIME = "%b %-d %-I:%M %p"
SHOWN = 120
OWNER_ANSWER = "from:owner"
ANSWER_TITLE = "What did the owner say at {at}?"
RULINGS = "root/rulings/*.md"
LR = Path(__file__).parents[2] / "bin" / "lr"
AUDIT = f"{LR} owner audit --drive brook-drive"
AUDIT_SECONDS = 60
CARRY = "Every order below is still owed, and a reply or a route does not close one. Carry each into the summary by its row id.\n\n```\n{orders}\n```"
REREAD = "You just compacted as the communicator. Run `{lr} owner audit --drive {drive}` before any other search: every order it prints is still owed."
MARK = "`{lr} owner mark {row} responded|addressed [--routed <seq>] --drive {drive}`"
NEW_ROW = f"Owner message {{rows}} is open in the communicator's ledger, one row per ask. Once each ask is answered or routed, run {MARK} for its row."
OPEN_ROWS = f"A communicator turn ends only when every owner message is marked responded or addressed. Run {MARK} for each row below.\n\n```\n{{rows}}\n```"
HAND_RULING = "The `owner_ledger` hook records every owner message in the owner ledger and a cc-notes answer, so nothing writes to `root/rulings/` by hand. Drop this write and post anything else for the coordinator with `cci post`."
NO_ASK = "The communicator never calls `AskUserQuestion`. Put the question on the owner's board with `cc-present push`, or send an urgent yes/no to the owner's Slack with `cc-present ask <card>`."


@workflow_state("long_running_owner_ledger")
class LedgeredDms(WorkflowState):
    digests: list[str] = []


def ledger_of(entry: dict) -> Path:
    return Path(entry["state_dir"]) / LEDGER_DIR / LEDGER


def continues(before: str, piece: str) -> bool:
    return min(len(before.split()), len(piece.split())) < ASK_WORDS or before.startswith(">") or before.count("(") > before.count(")")


def issues(piece: str) -> list[str]:
    clauses = CLAUSE.split(piece)
    if not (named := next(((at, found[1]) for at, clause in enumerate(clauses) if (found := COUNTED.search(clause))), None)):
        return [piece]
    at, count = named[0], int(named[1]) if named[1].isdigit() else COUNTS.index(named[1].lower()) + 2
    if len(clauses) - at - 1 != count:
        return [piece]
    return [", ".join(clauses[: at + 2]), *clauses[at + 2 :]]


def asks(text: str) -> list[str]:
    pieces = [
        issue.strip()
        for paragraph in PARAGRAPH.split(text.strip())
        for question in AFTER_QUESTION.split(paragraph)
        for piece in BEFORE_ALSO.split(question)
        for issue in issues(piece)
    ]
    merged: list[str] = []
    for piece in filter(None, pieces):
        if merged and continues(merged[-1], piece):
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    return merged if 1 < len(merged) <= MOST_ASKS else [text]


def add_rows(path: Path, at: datetime, text: str) -> list[tuple[str, str]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / LOCK).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = path.read_text() if path.exists() else ""
        rows = ROW.findall(ledger)
        split = asks(CREDENTIAL.sub(REDACTED, text))
        cells = ["<br>".join(ask.replace("|", r"\|").splitlines()) for ask in split]
        if [found[3:] for found in rows[-len(cells) :]] == [(cell, "open") for cell in cells]:
            return []
        number = max((int(found[1]) for found in rows), default=0) + 1
        ids = [f"m{number}"] if len(cells) == 1 else [f"m{number}.{part}" for part in range(1, len(cells) + 1)]
        heading = "" if HEADER in ledger else f"\n{HEADER}"
        stamp = at.astimezone(PACIFIC).strftime(ROW_TIME)
        with path.open("a") as file:
            file.write(heading + "".join(f"| {row} | {stamp} | {cell} |  | open |\n" for row, cell in zip(ids, cells, strict=True)))
    return list(zip(ids, split, strict=True))


def post_owner_row(entry: dict, row: str, text: str) -> None:
    argv = ["cci", "post", "--drive", bus_name(entry), "--lane", COMMUNICATOR_LANE, "--kind", "owner", "--id", f"owner:{row}", "--attr", f"row={row}", "--quoted", CREDENTIAL.sub(REDACTED, text)]
    subprocess.run(argv, capture_output=True, text=True, timeout=CCN_TIMEOUT_SECONDS, env=dict(reqenv.env_map()))


def add_answer(evt: BaseHookEvent, at: datetime) -> None:
    labels = (OWNER_ANSWER, f"program:{CompactionState.load(evt).slug}", message_label(evt))
    title = ANSWER_TITLE.format(at=at.astimezone(PACIFIC).strftime(ROW_TIME))
    answers(evt, "add", *(f"--label={label}" for label in labels), f"--body={CREDENTIAL.sub(REDACTED, ruling_body(evt))}", "--", title)


def open_rows(path: Path) -> list[str]:
    ledger = path.read_text() if path.exists() else ""
    return [f"{row} {at}: {text[:SHOWN]}" for row, _, at, text, state in ROW.findall(ledger) if state == "open"]


@on(
    Event.UserPromptSubmit,
    only_if=[Communicator(), OwnerPrompt()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(prompt="keep the nightly report", state=ACTIVE): Allow(),
        Input(prompt="keep the nightly report"): Allow(),
        Input(prompt="keep the nightly report", state=ACTIVE, agent_id="a1b2c3"): Allow(),
    }),
)
def add_owner_message(evt: BaseHookEvent) -> HookResult | None:
    if not (entry := drive_entry(evt)):
        return None
    at, text = datetime.now(UTC), evt.user_prompt or ""
    if not (rows := add_rows(ledger_of(entry), at, text)):
        return None
    for row, ask in rows:
        post_owner_row(entry, row, ask)
    add_answer(evt, at)
    ids = [row for row, _ in rows]
    return evt.context(NEW_ROW.format(lr=LR, rows=", ".join(f"`{row}`" for row in ids), row=ids[0] if len(ids) == 1 else "<row>", drive=entry["drive"]))


@on(
    Event.PostToolUse | Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests=communicator_tests({
        Input(command="ls", state=ACTIVE): Allow(),
        Input(command="ls"): Allow(),
    }),
)
def add_owner_slack_dms(evt: BaseHookEvent) -> HookResult | None:
    ledgered = LedgeredDms.load(evt).digests
    if not (new := [dm for dm in slack_dms(evt, evt.ctx.t.events) if dm["digest"] not in ledgered]) or not (entry := drive_entry(evt)):
        return None
    with LedgeredDms.mutate(evt) as state:
        for dm in new:
            for row, ask in add_rows(ledger_of(entry), datetime.strptime(dm["at"], STAMP).replace(tzinfo=UTC), dm["text"]):
                post_owner_row(entry, row, ask)
            state.digests.append(dm["digest"])
    return None


@on(  # capt-hook: no-escape the owner ruled that the communicator's turn cannot end on an unmarked message
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(state=ACTIVE, env=DRIVE_CONFIG): Allow(),
        Input(env=DRIVE_CONFIG): Allow(),
    }),
)
def owner_messages_close_before_stop(evt: BaseHookEvent) -> HookResult | None:
    if not (entry := drive_entry(evt)) or not (rows := open_rows(ledger_of(entry))):
        return None
    return evt.block(OPEN_ROWS.format(lr=LR, row="<row>", drive=entry["drive"], rows="\n".join(rows)))


def asked(text: str = "Merge #48401 now?", **call: object) -> Input:
    return Input(tool="AskUserQuestion", tool_input={"questions": [{"question": text, "header": "Ask", "options": [{"label": "Go"}, {"label": "Hold"}], "multiSelect": False}]}, **call)


hook(  # capt-hook: no-escape the owner ruled this tool off the communicator with no exception
    Event.PreToolUse,
    only_if=[Communicator(), Tool("AskUserQuestion")],
    skip_if=[FromSubagent()],
    block=True,
    message=NO_ASK,
    tests=communicator_tests({
        asked(state=ACTIVE): Block(pattern=r"^The communicator never calls `AskUserQuestion`\..*`cc-present push`.*`cc-present ask <card>`"),
        asked("Merge #48401 now?\nccx: ask=blocking", state=ACTIVE): Block(),
        asked(state=ACTIVE, env={"CAPT_HOOK_CCX_RAW": "1"}): Block(),
        asked(): Allow(),
        asked(state=ACTIVE, agent_id="a1b2c3"): Allow(),
    }),
)


class WritesRulings:
    def check(self, evt: BaseHookEvent) -> bool:
        return any(path.match(RULINGS) for path in written_paths(evt))


hook(  # capt-hook: no-escape the owner ruled that his words reach the drive only through the ledger hook
    Event.PreToolUse,
    only_if=[Tool("Bash", "Edit", "Write", "MultiEdit"), WritesRulings()],
    block=True,
    message=HAND_RULING,
    tests={
        Input(tool="Write", tool_input={"file_path": "/s/brook/root/rulings/1102-1035-bug-followups.md", "content": "## 2:45 AM Nov 3, owner, terminal\n"}, cwd=CWD): Block(pattern=r"^The `owner_ledger` hook records every owner message"),
        Input(tool="Edit", tool_input={"file_path": "/s/brook/root/rulings/1102-1035-bug-followups.md", "old_string": "a", "new_string": "b"}, cwd=CWD): Block(),
        Input(command="printf 'Verbatim: x' >> /s/brook/root/rulings/1102-1035-bug-followups.md", cwd=CWD): Block(),
        Input(command="cat /s/brook/root/rulings/1102-1035-bug-followups.md", cwd=CWD): Allow(),
        Input(tool="Write", tool_input={"file_path": "/s/brook/rulings/4ffc9a5.md", "content": "x"}, cwd=CWD): Allow(),
    },
)


@on(
    Event.PreCompact,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(state=ACTIVE, env=DRIVE_CONFIG, commands={AUDIT: "m85 Nov 3 4:50 AM: build the phone app\n"}): Warn(pattern=r"^Every order below is still owed[\s\S]*m85 Nov 3 4:50 AM: build the phone app"),
        Input(state=ACTIVE, env=DRIVE_CONFIG, commands={AUDIT: (1, "no drive")}): Warn(pattern=r"lr owner audit failed: no drive"),
        Input(env=DRIVE_CONFIG): Allow(),
    }),
)
def compaction_carries_the_open_owner_orders(evt: BaseHookEvent) -> HookResult | None:
    if not (entry := drive_entry(evt)):
        return None
    done = subprocess.run([str(LR), "owner", "audit", "--drive", entry["drive"]], capture_output=True, text=True, timeout=AUDIT_SECONDS, env=dict(reqenv.env_map()))
    orders = done.stdout.strip() if done.returncode == 0 else f"lr owner audit failed: {(done.stderr or done.stdout).strip()}"
    return evt.context(CARRY.format(orders=orders or "none"))


@on(
    Event.SessionStart,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(source="compact", state=ACTIVE, env=DRIVE_CONFIG): Warn(pattern=r"^You just compacted as the communicator\. Run `\S+ owner audit --drive brook-drive` before any other search"),
        Input(source="startup", state=ACTIVE, env=DRIVE_CONFIG): Allow(),
    }),
)
def a_compacted_communicator_reads_the_open_orders_first(evt: BaseHookEvent) -> HookResult | None:
    if evt._raw.get("source") != "compact" or not (entry := drive_entry(evt)):
        return None
    return evt.context(REREAD.format(lr=LR, drive=entry["drive"]))
