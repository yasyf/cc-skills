from __future__ import annotations

import fcntl
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, Tool, WorkflowState, hook, on, workflow_state
from captain_hook.util import reqenv

from .compaction_handoff import CCN_TIMEOUT_SECONDS, COMMUNICATOR_LANE, Communicator, CompactionState, bus_name, communicator_tests, drive_entry
from .owner_facing import REPLY_WINDOW
from .register_capture import CREDENTIAL, REDACTED, STAMP, slack_dms
from .register_gates import written_paths
from .root_context import PACIFIC, OwnerPrompt, answers, message_label, ruling_body
from .tests.register_fixtures import CWD, DRIVE_CONFIG

ACTIVE = [CompactionState(active=True)]
LEDGER_DIR = "owner-relay"
LEDGER = "owner-ledger.md"
LOCK = ".owner-ledger.lock"
HEADER = "| id | time | message | routed | state |\n|---|---|---|---|---|\n"
ROW = re.compile(r"^\| m(\d+) \| ([^|]*) \| (.*) \| [^|]* \| (open|responded|addressed) \|$", re.MULTILINE)
ROW_TIME = "%b %-d %-I:%M %p"
SHOWN = 120
OWNER_ANSWER = "from:owner"
ANSWER_TITLE = "What did the owner say at {at}?"
RULINGS = "root/rulings/*.md"
LR = Path(__file__).parents[2] / "bin" / "lr"
MARK = "`{lr} owner mark {row} responded|addressed [--routed <seq>] --drive {drive}`"
NEW_ROW = f"Owner message `{{row}}` is open in the communicator's ledger. Once it is answered or routed, run {MARK}."
OPEN_ROWS = f"A communicator turn ends only when every owner message is marked responded or addressed. Run {MARK} for each row below.\n\n```\n{{rows}}\n```"
HAND_RULING = "The `owner_ledger` hook records every owner message in the owner ledger and a cc-notes answer, so nothing writes to `root/rulings/` by hand. Drop this write and post anything else for the coordinator with `cci post`."
NO_ASK = "The communicator never calls `AskUserQuestion`. Put the question on the owner's board with `cc-present push`, or send an urgent yes/no to the owner's Slack with `cc-present ask <card>`."


@workflow_state("long_running_owner_ledger")
class LedgeredDms(WorkflowState):
    digests: list[str] = []


def ledger_of(entry: dict) -> Path:
    return Path(entry["state_dir"]) / LEDGER_DIR / LEDGER


def add_row(path: Path, at: datetime, text: str) -> str | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / LOCK).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = path.read_text() if path.exists() else ""
        rows = ROW.findall(ledger)
        cell = "<br>".join(CREDENTIAL.sub(REDACTED, text).replace("|", r"\|").splitlines())
        if rows and rows[-1][2:] == (cell, "open"):
            return None
        row = f"m{max((int(found[0]) for found in rows), default=0) + 1}"
        heading = "" if HEADER in ledger else f"\n{HEADER}"
        with path.open("a") as file:
            file.write(f"{heading}| {row} | {at.astimezone(PACIFIC).strftime(ROW_TIME)} | {cell} |  | open |\n")
    return row


def post_owner_row(entry: dict, row: str, text: str) -> None:
    argv = ["cci", "post", "--drive", bus_name(entry), "--lane", COMMUNICATOR_LANE, "--kind", "owner", "--id", f"owner:{row}", "--attr", f"row={row}", "--text", CREDENTIAL.sub(REDACTED, text)]
    subprocess.run(argv, capture_output=True, text=True, timeout=CCN_TIMEOUT_SECONDS, env=dict(reqenv.env_map()))


def add_answer(evt: BaseHookEvent, at: datetime) -> None:
    labels = (OWNER_ANSWER, f"program:{CompactionState.load(evt).slug}", message_label(evt))
    title = ANSWER_TITLE.format(at=at.astimezone(PACIFIC).strftime(ROW_TIME))
    answers(evt, "add", *(f"--label={label}" for label in labels), f"--body={CREDENTIAL.sub(REDACTED, ruling_body(evt))}", "--", title)


def open_rows(path: Path) -> list[str]:
    ledger = path.read_text() if path.exists() else ""
    return [f"m{row} {at}: {text[:SHOWN]}" for row, at, text, state in ROW.findall(ledger) if state == "open"]


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
    if not (row := add_row(ledger_of(entry), at, text)):
        return None
    post_owner_row(entry, row, text)
    add_answer(evt, at)
    return evt.context(NEW_ROW.format(lr=LR, row=row, drive=entry["drive"]))


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
            if row := add_row(ledger_of(entry), datetime.strptime(dm["at"], STAMP).replace(tzinfo=UTC), dm["text"]):
                post_owner_row(entry, row, dm["text"])
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
