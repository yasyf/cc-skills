import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, state

REFUSAL = (
    "refused: no drive session asks in its terminal, since nobody reads it and every record for {drive} queues behind"
    " the question. Keep the block with `drive job ask <job> <file>` and send it to communicator@{drive} as an `ask`"
    " record, or return it as your `ASK` or `DESIGN`."
)


def deny_reason(payload: dict) -> str | None:
    """Why AskUserQuestion does not run in a session bound to a drive, the agents it starts and its handlers' own sessions included: the session's settings deny the tool only when it started from the drive's start command, and a question in a terminal nobody reads holds the session until something types an answer."""
    if not (names := state.session_drives(payload.get("session_id") or "")):
        return None
    return agents.Unjudged(REFUSAL.format(drive=names[0]))


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
                | agents.ruled(reason)
            )
        )


if __name__ == "__main__":
    main()
