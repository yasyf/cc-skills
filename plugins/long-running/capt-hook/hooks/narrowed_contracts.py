from __future__ import annotations

import re
import subprocess
import sys

from captain_hook import Allow, Annotated, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on

from .compaction_handoff import CompactionState, rulings, script
from .lane_rotation import DriveActive
from .owner_facing import CCI_POST, posted_text
from .register_gates import fenced
from .register_injection import drive_args
from .tests.register_fixtures import CWD, DRIVE_CONFIG

ACCEPTS = re.compile(r"^\s*(?:readback: ok\b|read-back \S+: ok\b)")
ROOT_KINDS = frozenset({"decide", "decision", "answer"})
WARNING = (
    "Warning, nothing was blocked: This post accepts contract #{seq}, which scopes an owner row narrower than his words."
    " Grade it missing quoting his words, or name each key below with his words that carry that scope."
)
CHECK = f"{sys.executable} {script('rulings.py')} narrowed --contract 8"
FLAGGED = 'NARROWED rule/0f31b56: says "per destination", "per key"; the owner\'s words say none of them\n'
ACTIVE = [CompactionState(active=True, slug="brook")]


def post(command: str, said: str = FLAGGED) -> Input:
    return Input(command=command, state=ACTIVE, cwd=CWD, env=DRIVE_CONFIG, commands={CHECK: said})


def accepting(values: dict[str, tuple], text: str) -> bool:
    kinds = set(values.get("kind", ()))
    return bool(ACCEPTS.match(text)) or ("root" in values.get("lane", ()) and bool(kinds & ROOT_KINDS))


def unanswered(evt: BaseHookEvent, seq: str, drive: list[str], text: str) -> str:
    try:
        return rulings(str(evt.cwd), "narrowed", "--contract", seq, *drive, stdin=text).strip()
    except subprocess.CalledProcessError as found:
        if found.returncode != 1:
            raise
        return (found.stdout or "").strip()


@on(
    Event.PreToolUse,
    only_if=[DriveActive(), Tool("Bash")],
    skip_if=[Annotated("raw")],
    tests={
        post("cci post --lane fidelity-desk --kind review --re 8 --text 'readback: ok lane-a 0123456789ab'"): Warn(
            pattern=r"^Warning, nothing was blocked: This post accepts contract #8, which scopes[\s\S]*NARROWED rule/0f31b56: says \"per destination\""
        ),
        post("cci post --lane root --kind decide --re 8 --text 'Accepted; build it.'"): Warn(pattern=r"accepts contract #8"),
        post("cci post --lane fidelity-desk --kind review --re 8 --text 'readback: ok lane-a 0123456789ab; rule/0f31b56 carries it'", said=""): Allow(),
        post("cci post --lane fidelity-desk --kind review --re 8 --text 'readback: missing lane-a rule/0f31b56 per destination'"): Allow(),
        post("cci post --lane lane-a --kind state --re 8 --text 'Accepted; building.'"): Allow(),
        post("cci post --lane root --kind decide --text 'Accepted; build it.'"): Allow(),
        post("cci post --lane root --kind decide --re 8 --text 'Accepted; build it.'  # ccx:raw"): Allow(),
    },
)
def narrowed_acceptance(evt: BaseHookEvent) -> HookResult | None:
    if not (drive := drive_args(evt)):
        return None
    for call in evt.command.calls("cci"):
        values = CCI_POST.bind(call).values
        if values.get("words", ())[:1] != ("post",) or not (seq := next(iter(values.get("re", ())), None)) or not accepting(values, text := posted_text(values)):
            continue
        if lines := unanswered(evt, seq, drive, text):
            return evt.context("\n".join([WARNING.format(seq=seq), fenced(lines)]))
    return None
