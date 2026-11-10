import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import render, state


def role_context(session: str) -> str | None:
    bound = state.session_drives(session)
    if not bound:
        return None
    data = state.load(bound[0])
    return render.context(data, state.session_role(data, session))


def respond(payload: dict) -> dict | None:
    if payload.get("agent_id"):
        return None
    session = payload.get("session_id")
    text = role_context(session) if session else None
    if text is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def main() -> None:
    try:
        output = respond(json.load(sys.stdin))
    except Exception:
        return
    if output:
        print(json.dumps(output))


if __name__ == "__main__":
    main()
