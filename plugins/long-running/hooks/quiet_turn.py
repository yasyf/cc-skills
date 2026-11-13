import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, state

CLOSING = "Sent."
TAIL = 1_000_000


def block_reason(payload: dict) -> str | None:
    """Why a drive's communicator may not end its turn on the text it wrote: he reads Slack, never its terminal, so a turn is tool calls and one closing word."""
    if payload.get("stop_hook_active") or payload.get("agent_id"):
        return None
    session = payload.get("session_id") or ""
    names = state.session_drives(session)
    if not names or state.session_role(state.saved(names[0]), session) != "communicator":
        return None
    words = len((payload.get("last_assistant_message") or "").split())
    if words <= 1:
        return between_calls(payload)
    return (
        f"Your turn ended on {words} words in this terminal, which he never reads: everything for him goes to Slack,"
        f" and your turn is tool calls alone. End this turn, and every turn, with the one word `{CLOSING}` and nothing"
        ' else. His words, Nov 10 9:53 PM: "you doint ned to kjeep sending the response here as wel", and 10:20 PM:'
        ' "update your skoll; to tro minimuize temrinal outpoujt as well".'
    )


def between_calls(payload: dict) -> str | None:
    """Why a communicator's turn that ends on its one word may not end yet: it wrote text between its tool calls, which he never reads."""
    spoken = [text for text in turn_text(Path(path)) if text.strip() not in ("", CLOSING)] if (path := payload.get("transcript_path")) else []
    if not spoken:
        return None
    return (
        f"Refused on purpose: this turn wrote {len(spoken)} {'message' if len(spoken) == 1 else 'messages'} of text"
        f" between its tool calls in this terminal, which he never reads, the first: \"{' '.join(spoken[0].split())[:80]}\"."
        f" Put nothing between tool calls: every message of a turn is tool calls alone, its words for him go to Slack"
        f" through a handler or `say`, and the turn ends on the one word `{CLOSING}`. End this turn with `{CLOSING}` alone."
    )


def turn_text(transcript: Path) -> list[str]:
    """The text the session wrote since the last message it was sent, read from the transcript's tail, since a drive's transcript runs to hundreds of megabytes."""
    with transcript.open("rb") as handle:
        start = max(0, handle.seek(0, os.SEEK_END) - TAIL)
        handle.seek(start)
        lines = handle.read().decode(errors="replace").splitlines()[1 if start else 0 :]
    texts: list[str] = []
    for line in lines:
        entry = json.loads(line)
        content = (entry.get("message") or {}).get("content")
        if entry.get("type") == "user" and (isinstance(content, str) or any(block.get("type") == "text" for block in content)):
            texts = []
        elif entry.get("type") == "assistant":
            texts += [block["text"] for block in content if block.get("type") == "text"]
    return texts


def main() -> None:
    if reason := block_reason(agents.seen(json.load(sys.stdin))):
        print(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    main()
