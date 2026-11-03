from __future__ import annotations

import fcntl
import re
from datetime import UTC, datetime
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, Tool, WorkflowState, hook, on, workflow_state

from .compaction_handoff import Communicator, CompactionState, communicator_tests, drive_entry
from .owner_facing import REPLY_WINDOW
from .register_capture import CREDENTIAL, REDACTED, STAMP, slack_dms
from .root_context import PACIFIC, OwnerPrompt
from .tests.register_fixtures import DRIVE_CONFIG

ACTIVE = [CompactionState(active=True)]
LEDGER_DIR = "owner-relay"
LEDGER = "owner-ledger.md"
LOCK = ".owner-ledger.lock"
HEADER = "| id | time | message | routed | state |\n|---|---|---|---|---|\n"
ROW = re.compile(r"^\| m(\d+) \| ([^|]*) \| (.*) \| [^|]* \| (open|responded|addressed) \|$", re.MULTILINE)
ROW_TIME = "%b %-d %-I:%M %p"
SHOWN = 120
LR = Path(__file__).parents[2] / "bin" / "lr"
MARK = "`{lr} owner mark {row} responded|addressed [--routed <seq>] --drive {drive}`"
NEW_ROW = f"Owner message `{{row}}` is open in the communicator's ledger. Once it is answered or routed, run {MARK}."
OPEN_ROWS = f"A communicator turn ends only when every owner message is marked responded or addressed. Run {MARK} for each row below.\n\n```\n{{rows}}\n```"
NO_ASK = "The communicator never calls `AskUserQuestion`. Put the question on the owner's board with `cc-present push`, or send an urgent yes/no to the owner's Slack with `cc-present ask <card>`."


@workflow_state("long_running_owner_ledger")
class LedgeredDms(WorkflowState):
    digests: list[str] = []


def ledger_of(entry: dict) -> Path:
    return Path(entry["state_dir"]) / LEDGER_DIR / LEDGER


def add_row(path: Path, at: datetime, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / LOCK).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = path.read_text() if path.exists() else ""
        row = f"m{max((int(found[0]) for found in ROW.findall(ledger)), default=0) + 1}"
        cell = "<br>".join(CREDENTIAL.sub(REDACTED, text).replace("|", r"\|").splitlines())
        heading = "" if HEADER in ledger else f"\n{HEADER}"
        with path.open("a") as file:
            file.write(f"{heading}| {row} | {at.astimezone(PACIFIC).strftime(ROW_TIME)} | {cell} |  | open |\n")
    return row


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
    row = add_row(ledger_of(entry), datetime.now(UTC), evt.user_prompt or "")
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
            add_row(ledger_of(entry), datetime.strptime(dm["at"], STAMP).replace(tzinfo=UTC), dm["text"])
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
