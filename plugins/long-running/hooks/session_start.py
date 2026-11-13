import json
import os
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "lib"))

from drive import agents, render, state, watch


def role_context(session: str) -> str | None:
    """The role's context for the session holding one; a coordinator's session, or a project's sub-coordinator's, also gets its watch started unless it runs."""
    bound = state.session_drives(session)
    if not bound:
        return None
    data = state.load(bound[0])
    role = state.session_role(data, session)
    project = state.session_project(data, session)
    if role == "coordinator":
        watch.ensure(bound[0], project)
    return render.context(data, role, project)


def respond(payload: dict) -> dict | None:
    if payload.get("agent_id"):
        return None
    session = payload.get("session_id")
    text = role_context(session) if session else None
    if text is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def main() -> None:
    payload = json.load(sys.stdin)
    if handler := os.environ.get(state.HANDLER_ENV):
        agents.bind_handler(payload, handler)
        return
    try:
        output = respond(payload)
    except Exception:
        return
    if output:
        print(json.dumps(output))


if __name__ == "__main__":
    main()
