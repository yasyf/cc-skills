"""The one gate every drive check refuses through, by his rule of Nov 16 10:22: "its not acceptable for tooling to get in the way like this … letting the drives be as agentic as possible". The first try of an action by a session hears every finding at once and is refused; a repeat of that action by that session whose every finding it was refused for goes through with the findings warned and logged, while a new finding is refused once on its own; a store that cannot be read or written lets the action through."""

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


def gate(name: str | None, action: str, findings: list[str], session: str | None = None, order: tuple[str, dict] | None = None, dry_run: bool = False) -> str | None:
    """The refusal of a try of action by session naming a finding this gate has not refused it once for, naming every finding, else None: a repeat whose every finding was refused once goes through with the findings warned on stderr, so a different finding is refused once on its own; each session, action and finding is one file in the store, made exclusively, and a dry run, such as drive job ask --check, says the refusal without keeping it, so it never uses up the one refusal; a store it cannot make, read or write lets the action go with a warning, and on a drive's order of a class that blocks, each refusal and repeat is a blocked event."""
    if not findings:
        return None
    path, who = store(name), session or os.environ.get("CLAUDE_CODE_SESSION_ID") or "terminal"
    said = "; ".join(findings)
    kept = {finding: path / hashlib.sha256(f"{who} {action} {finding}".encode()).hexdigest()[:32] for finding in findings}
    if dry_run:
        return refusal(findings) if not all(each.exists() for each in kept.values()) else None
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as unread:
        print(f"warning: {action}: {said}; going through, since the gate store {path} could not be made: {unread}", file=sys.stderr)
        return None
    fresh = []
    for finding, each in kept.items():
        try:
            with open(each, "x") as first:
                first.write(json.dumps({"key": f"{who} {action}", "at": state.stamp(), "finding": finding}))
            fresh.append(finding)
        except FileExistsError:
            continue
        except OSError as unread:
            print(f"warning: {action}: {said}; going through, since the gate store {path} could not be read or written: {unread}", file=sys.stderr)
            return None
    repeat = not fresh
    if name and order and state.order_class(order[1]) != state.CLASSES[2]:
        state.append_event(name, BLOCKED, order=order[0], action=action, findings=findings, repeat=repeat, session=who)
    if repeat:
        print(f"warning: {action}: {said}; going through, as a repeat of a try this gate refused once", file=sys.stderr)
        return None
    return refusal(findings)
