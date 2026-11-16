import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, state

CLOSING = "Sent."
TAIL = 1_000_000
ANSWER_WINDOW = timedelta(minutes=20)
SLACK_TAG = re.compile(r'<channel source="plugin:cc-slack:channel"[^>]*type="slack\.message">\n(\{.*?\})\n</channel>', re.DOTALL)


def block_reason(payload: dict) -> str | None:
    """Why a drive's communicator may not end its turn on the text it wrote: he reads Slack, never its terminal, so a turn is tool calls and one closing word."""
    if payload.get("stop_hook_active") or payload.get("agent_id"):
        return None
    session = payload.get("session_id") or ""
    names = state.session_drives(session)
    if not names or state.session_role(state.saved(names[0]), session) != "communicator":
        return None
    if (path := payload.get("transcript_path")) and (unanswered := unanswered_threads(Path(path))):
        return (
            f"Refused on purpose: his message in thread {', '.join(unanswered)} has no post of yours in its thread yet, and a turn"
            " never ends leaving him with none: post the answer now with say in that thread, what it is, what you found and"
            " what happens next and who does it, or a +1 react for chatter, then end on Sent."
        )
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


def unanswered_threads(transcript: Path) -> list[str]:
    """The thread of each message of his cc-slack delivered in the last ANSWER_WINDOW that no say post or react of this session went to after it, read from the transcript's tail, so a turn that only handed his message on cannot end silent."""
    since = datetime.now(UTC) - ANSWER_WINDOW
    his: dict[str, str] = {}
    for entry in tail_entries(transcript):
        if entry.get("type") == "api-request-blob" or not (stamp := entry.get("timestamp")) or datetime.fromisoformat(stamp) < since:
            continue
        for text in strings(entry.get("attachment"), (entry.get("message") or {}).get("content")):
            for found in SLACK_TAG.finditer(text):
                event = json.loads(found[1])
                if not event.get("from_claude") and (event.get("kind") == "dm" or event.get("operator")):
                    his.setdefault(event.get("thread_ts") or event["ts"], stamp)
        content = (entry.get("message") or {}).get("content")
        for block in content if entry.get("type") == "assistant" and isinstance(content, list) else []:
            if block.get("type") == "tool_use" and block["name"].endswith(("__say", "__slack_react")):
                answered = block["input"].get("thread_ts") or block["input"].get("ts")
                if answered in his:
                    del his[answered]
    return list(his)


def strings(*values: object) -> list[str]:
    """Every string a transcript entry's attachment or message content holds, at any depth."""
    found: list[str] = []
    for value in values:
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, dict):
            found += strings(*value.values())
        elif isinstance(value, list):
            found += strings(*value)
    return found


def tail_entries(transcript: Path) -> list[dict]:
    """The entries of the transcript's tail, since a drive's transcript runs to hundreds of megabytes."""
    with transcript.open("rb") as handle:
        start = max(0, handle.seek(0, os.SEEK_END) - TAIL)
        handle.seek(start)
        lines = handle.read().decode(errors="replace").splitlines()[1 if start else 0 :]
    return [json.loads(line) for line in lines if line.strip()]


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
