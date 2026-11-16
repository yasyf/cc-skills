"""The one gate every drive check refuses through, by his rule of Nov 16 10:22: "its not acceptable for tooling to get in the way like this … letting the drives be as agentic as possible". The first try of an action by a session hears every finding at once and is refused; any repeat of that action by that session goes through with the findings warned and logged; a store that cannot be read or written lets the action through."""

import hashlib
import json
import os
import sys
from pathlib import Path

from drive import state

BLOCKED = "blocked"
REPEAT = "run it again as it stands and it goes through, with these findings logged"


def refusal(findings: list[str]) -> str:
    """The words a first try hears: every finding, each without its own leading `refused: `, and how a repeat goes through."""
    return f"refused once: {'; '.join(finding.removeprefix('refused: ') for finding in findings)}. {REPEAT}"


def store(name: str | None) -> Path:
    return (state.drive_dir(name) if name else state.root()) / "gates"


def gate(name: str | None, action: str, findings: list[str], session: str | None = None, order: tuple[str, dict] | None = None) -> str | None:
    """The refusal of the first try of action by session, naming every finding, else None: a repeat goes through with the findings warned on stderr; each session and action is one file in the store, made exclusively, so two sessions gated at once never undo each other, and a store that cannot be written lets every try through, warned. A refusal or warned repeat on an incident or asked order, order naming its id and row, appends a blocked event naming the order, the action and the findings, by his rule "silence is never acceptable; it tells me in the thread when it is blocked", which the owed queue turns into a step on the order's holder; the gate posts nothing."""
    if not findings:
        return None
    path, who = store(name), session or os.environ.get("CLAUDE_CODE_SESSION_ID") or "terminal"
    key, said = f"{who} {action}", "; ".join(findings)
    kept = path / hashlib.sha256(key.encode()).hexdigest()[:32]
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as unread:
        print(f"warning: {action}: {said}; going through, since the gate store {path} could not be made: {unread}", file=sys.stderr)
        return None
    try:
        with open(kept, "x") as first:
            first.write(json.dumps({"key": key, "at": state.stamp(), "findings": findings}))
        repeat = False
    except FileExistsError:
        repeat = True
    except OSError as unread:
        print(f"warning: {action}: {said}; going through, since the gate store {path} could not be read or written: {unread}", file=sys.stderr)
        return None
    if name and order and state.order_class(order[1]) != state.CLASSES[2]:
        state.append_event(name, BLOCKED, order=order[0], action=action, findings=findings, repeat=repeat, session=who)
    if repeat:
        print(f"warning: {action}: {said}; going through, as a repeat of a try this gate refused once", file=sys.stderr)
        return None
    return refusal(findings)
