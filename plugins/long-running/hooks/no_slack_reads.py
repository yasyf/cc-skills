import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, state


def note(payload: dict) -> str | None:
    """What a drive's coordinator, communicator or meta session hears as it reads a Slack thread or channel itself: the read goes, and its whole result stays in the session's own context."""
    if payload.get("agent_id"):
        return None
    session = payload.get("session_id") or ""
    names = state.session_drives(session)
    if not names:
        return None
    role = state.session_role(state.saved(names[0]), session)
    return (
        f"the {role} reads Slack itself with {payload['tool_name']}: the read goes, and its whole result stays in this"
        " session's context; a long thread read by a handler or a job comes back as one line."
    )


def main() -> None:
    if told := note(agents.seen(json.load(sys.stdin))):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": told}}))


if __name__ == "__main__":
    main()
