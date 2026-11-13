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
ACCESS_REFUSAL = (
    "refused: {tool} waits on a person approving it in a dialog in this terminal, which nobody reads, so it holds the"
    " session until something answers it. Settle what it was for by reads that ask nobody, and return a check they"
    " cannot settle as `UNPROVEN: <the check>: <the proof that settles it>`."
)


def deny_reason(payload: dict) -> str | None:
    """Why AskUserQuestion, or a computer-use request for screen-control access, does not run in a session bound to a drive, the agents it starts and its handlers' own sessions included: the session's settings deny AskUserQuestion only when it started from the drive's start command and deny no access request, and a question or an approval dialog in a terminal nobody reads holds the session until something types an answer."""
    if not (names := state.session_drives(payload.get("session_id") or "")):
        return None
    tool = payload["tool_name"]
    return agents.Unjudged((REFUSAL if tool == "AskUserQuestion" else ACCESS_REFUSAL).format(drive=names[0], tool=tool))


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
