import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, state

REFUSAL = (
    "refused: no drive session asks in its terminal, by his words \"we aare supposed to launch the coordinator etc with no"
    " askuserquestion tool\": he reads no terminal of {drive}'s, so AskUserQuestion would wait unanswered while every record"
    " for this session queues behind it, as the coordinator's did for 33 and then 88 minutes on Nov 12. A question for him"
    " reaches him only as a question card: keep the block with `drive job ask <job> <file>` and send it to"
    " communicator@{drive} as an `ask` record naming that file, or, as a job's agent, return it as your `ASK` or `DESIGN`."
    " Anything that is not his to choose, decide yourself."
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
