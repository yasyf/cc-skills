import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, render, state


def main() -> None:
    payload = json.load(sys.stdin)
    session = payload.get("session_id") or ""
    if payload.get("agent_id") or not (names := state.session_drives(session)):
        return
    if role := agents.hold_rules(names[0], session):
        changed = f"Your {state.title(role)} rules changed since this session's block drew them: long-running {state.VERSION}'s replace them whole, and win where any older text disagrees."
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n".join([changed, *render.rules(state.saved(names[0]), role)])}}))


if __name__ == "__main__":
    main()
