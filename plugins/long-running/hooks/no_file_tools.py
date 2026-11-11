import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import state


def deny_reason(payload: dict) -> str | None:
    """Why a drive's coordinator or communicator may not open a file itself: Claude Code puts each file opened with Read, Write or Edit back into the session after every compaction."""
    if payload.get("agent_id"):
        return None
    session = payload.get("session_id") or ""
    names = state.session_drives(session)
    if not names:
        return None
    role = state.session_role(state.load(names[0]), session)
    return (
        f"refused: the {role} opens no file with {payload['tool_name']}, since Claude Code puts each file opened with"
        " Read, Write or Edit back into this session after every compaction. Read a page of the skill with"
        " `drive manual <page>`, a job's return with `drive job report <job>` and a record with cci; a file to write"
        " goes to a job, or on stdin to the drive verb that keeps it."
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
