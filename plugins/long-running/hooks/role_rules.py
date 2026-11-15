import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, render, state


def main() -> None:
    payload = json.load(sys.stdin)
    session = payload.get("session_id") or ""
    if not (names := state.session_drives(session)):
        return
    agent = payload.get("agent_id")
    shown, overdue = render.overdue(state.held_clocks(names[0], session, agent), state.now())
    lines = overdue if state.show_overdue(names[0], agent or session, shown) else []
    if not agent and (role := agents.hold_rules(names[0], session)):
        changed = f"Your {state.title(role)} rules or held channels changed since this session's block drew them: long-running {state.VERSION}'s replace them whole, and win where any older text disagrees."
        data = state.load(names[0])
        lines += [changed, render.hold_line(data, role, state.session_project(data, session)), *render.rules(data, role)]
    if lines:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n".join(lines)}}))


if __name__ == "__main__":
    main()
