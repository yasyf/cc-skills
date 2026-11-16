import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, state


def main() -> None:
    payload = json.load(sys.stdin)
    session = payload.get("session_id") or ""
    if payload.get("agent_id") or not (names := state.session_drives(session)):
        return
    if due := agents.hold_rules(names[0], session):
        role = state.session_role(state.saved(names[0]), session)
        lines = [f"Your {state.title(role)} rules or held channels changed since this session's block drew them: long-running {state.VERSION}'s changed lines follow, and win where any older text disagrees.", *due]
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n".join(lines)}}))


if __name__ == "__main__":
    main()
