from __future__ import annotations

import fcntl
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, T, Tool, WorkflowState, hook, llm_gate, on, workflow_state

from .compaction_handoff import Communicator, CompactionState, bus_name, communicator_tests, drive_entry
from .owner_facing import REPLY_WINDOW
from .register_capture import CREDENTIAL, REDACTED, STAMP, slack_dms
from .root_context import PACIFIC, OwnerPrompt
from .root_replies import SYSTEM_PREFIXES, owner_text
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
REMINDER = """Judge only the owner's words in <owner_message>, what he sent the assistant this turn. His typos are his; read through them.
Block when he reminds the assistant of something it should already have done, repeats an instruction or ruling he gave before, or corrects how it works: something missed, forgotten, still happening, done wrong or not followed.
Do not block a new request, a question, an answer to a question, a pick, an approval or thanks that points at nothing the assistant failed to do."""
UNROUTED_REMINDER = (
    "Every owner reminder or correction spawns a meta tooling lane that makes the behaviour automatic."
    " Before stopping, forward his words verbatim with their time as `cci post --lane owner-relay --kind ask --to meta`, or launch the lane with `lr launch`."
)
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
    return evt.context(NEW_ROW.format(lr=LR, row=row, drive=bus_name(entry)))


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
    return evt.block(OPEN_ROWS.format(lr=LR, row="<row>", drive=bus_name(entry), rows="\n".join(rows)))


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


HIS_1_34_AM = (
    "every tjme i need to remknd you to do skemtjjng, hou need to spawn a meta tooling lane foe thay (includkng tbis)."
    " uou did tjay for the ildate after being away a long tome, so good job, but make sure thay is durable behavior not just this session."
)
FORWARDED = "cci post --lane owner-relay --kind ask --to meta-desk-1 --text '1:34 AM, verbatim: every tjme i need to remknd you'"


class OwnerWords:
    tag = "owner_message"
    required = True

    def content(self, evt: BaseHookEvent) -> str | None:
        events = list(evt.ctx.turn.events)
        typed = [text for event in events if (text := owner_text(event)) and not text.lstrip().startswith(SYSTEM_PREFIXES)]
        return "\n\n".join([*typed, *(dm["text"] for dm in slack_dms(evt, events))]) or None


class Unrouted:
    def check(self, evt: BaseHookEvent) -> bool:
        return not any(routes_to_meta(cmd.argv) for use in evt.ctx.turn.tool_calls.named("Bash").items for cmd in use.call.command_line if cmd.argv)


def routes_to_meta(argv: Sequence[str]) -> bool:
    names = [Path(word).name for word in argv]
    if "orca-launch.sh" in names or ("lr" in names and names[names.index("lr") + 1 : names.index("lr") + 2] == ["launch"]):
        return True
    return names[0] == "cci" and "post" in argv and "ask" in values(argv, "--kind") and any(to.startswith("meta") for value in values(argv, "--to") for to in value.split(","))


def values(argv: Sequence[str], flag: str) -> list[str]:
    return [after for word, after in pairwise(argv) if word == flag] + [word.removeprefix(f"{flag}=") for word in argv if word.startswith(f"{flag}=")]


def reminded(*calls: object, prompt: str = HIS_1_34_AM) -> list:
    return [T.user(prompt), T.assistant(*calls or ("Spawning it now.",))]


llm_gate(  # capt-hook: no-escape the owner ruled that every reminder or correction of his spawns a meta tooling lane before the turn ends
    REMINDER,
    message=UNROUTED_REMINDER,
    label="owner_reminders_spawn_meta_lane",
    contexts=[OwnerWords()],
    only_if=[Communicator(), Unrouted()],
    skip_if=[FromSubagent()],
    once_per_turn=False,
    max_fires=None,
    transcript=False,
    agent=False,
    backend="jev",
    events=Event.Stop,
    tests=communicator_tests({
        Input(transcript=reminded(), state=ACTIVE): Block(pattern=r"^Every owner reminder or correction spawns a meta tooling lane.*--to meta"),
        Input(transcript=reminded(T.tool("Bash", command="cci post --lane owner-relay --kind go --to meta-desk-1 --text x")), state=ACTIVE): Block(),
        Input(transcript=reminded(T.tool("Bash", command=FORWARDED)), state=ACTIVE): Allow(),
        Input(transcript=reminded(T.tool("Bash", command="cci post --lane owner-relay --kind=ask --to=root,meta --text x")), state=ACTIVE): Allow(),
        Input(transcript=reminded(T.tool("Bash", command="lr launch meta /drive/meta/brief.md")), state=ACTIVE): Allow(),
        Input(transcript=reminded(T.tool("Bash", command="bash ~/.claude/long-running/current/bin/orca-launch.sh /drive/fix-3 brief.md")), state=ACTIVE): Allow(),
        Input(transcript=reminded(prompt="add a dark mode toggle to the dashboard"), state=ACTIVE, llm={"block": False}): Allow(),
        Input(transcript=reminded(prompt="<task-notification>lane done</task-notification>"), state=ACTIVE): Allow(),
        Input(transcript=reminded(), state=ACTIVE, agent_id="a1b2c3"): Allow(),
        Input(transcript=reminded()): Allow(),
    }),
)
