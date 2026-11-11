import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import state

CLOSING = "Sent."


def block_reason(payload: dict) -> str | None:
    """Why a drive's communicator may not end its turn on the text it wrote: he reads Slack, never its terminal, so a turn is tool calls and one closing word."""
    if payload.get("stop_hook_active") or payload.get("agent_id"):
        return None
    session = payload.get("session_id") or ""
    names = state.session_drives(session)
    if not names or state.session_role(state.load(names[0]), session) != "communicator":
        return None
    words = len((payload.get("last_assistant_message") or "").split())
    if words <= 1:
        return None
    return (
        f"Your turn ended on {words} words in this terminal, which he never reads: everything for him goes to Slack,"
        f" and your turn is tool calls alone. End this turn, and every turn, with the one word `{CLOSING}` and nothing"
        ' else. His words, Nov 10 9:53 PM: "you doint ned to kjeep sending the response here as wel", and 10:20 PM:'
        ' "update your skoll; to tro minimuize temrinal outpoujt as well".'
    )


def main() -> None:
    if reason := block_reason(json.load(sys.stdin)):
        print(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    main()
