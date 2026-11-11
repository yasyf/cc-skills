import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import state


def deny_reason(payload: dict) -> str | None:
    """Why a drive's coordinator or communicator may not read a Slack thread or channel itself: the whole read lands in its own context."""
    if payload.get("agent_id"):
        return None
    session = payload.get("session_id") or ""
    names = state.session_drives(session)
    if not names:
        return None
    role = state.session_role(state.load(names[0]), session)
    return (
        f"refused: the {role} reads no Slack thread or channel itself with {payload['tool_name']}, since the whole"
        " read lands in this session's context and stays there: three reads of his DM threads by a coordinator put"
        " 19,581 characters into it. A read is a handler's or a job's, which returns one line."
    )


def main() -> None:
    if reason := deny_reason(json.load(sys.stdin)):
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )


if __name__ == "__main__":
    main()
