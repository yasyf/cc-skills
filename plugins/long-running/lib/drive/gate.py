"""The one gate every drive check refuses through, by his rule of Nov 16 10:22: "its not acceptable for tooling to get in the way like this … letting the drives be as agentic as possible". The first try of an action by a session hears every finding at once and is refused; any repeat of that action by that session goes through with the findings warned and logged; a store that cannot be read or written lets the action through."""

import json
import os
import sys
from pathlib import Path

from drive import state

REPEAT = "run it again as it stands and it goes through, with these findings logged"


def store(name: str | None) -> Path:
    return (state.drive_dir(name) if name else state.root()) / "gates.json"


def gate(name: str | None, action: str, findings: list[str], session: str | None = None) -> str | None:
    """The refusal of the first try of action by session, naming every finding, else None: a repeat goes through with the findings warned on stderr and logged in the store, and a store that cannot be read or written lets every try through, warned."""
    if not findings:
        return None
    path, who = store(name), session or os.environ.get("CLAUDE_CODE_SESSION_ID") or "terminal"
    key, said = f"{who} {action}", "; ".join(findings)
    try:
        seen = json.loads(path.read_text()) if path.exists() else {}
        repeat = key in seen
        seen[key] = {"at": state.stamp(), "findings": findings, "repeats": seen[key]["repeats"] + 1 if repeat else 0}
        written = path.with_suffix(".tmp")
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_text(json.dumps(seen))
        written.replace(path)
    except (OSError, ValueError) as unread:
        print(f"warning: {action}: {said}; going through, since the gate store {path} could not be read or written: {unread}", file=sys.stderr)
        return None
    if repeat:
        print(f"warning: {action}: {said}; going through, as a repeat of a try this gate refused once", file=sys.stderr)
        return None
    return f"refused once: {said}. {REPEAT}"
