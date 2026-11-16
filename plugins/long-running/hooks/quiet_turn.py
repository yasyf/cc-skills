import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, slack, state

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
    posted = turn_threads(Path(path)) if (path := payload.get("transcript_path")) else set()
    if unanswered := [thread for thread in owed_threads(session) if thread not in posted]:
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


def owed_threads(session: str) -> list[str]:
    """The thread of each message of his `cc-slack dm-owed` lists as still owed by this session, by cc-slack's one rule of answered: a drive post after it in its thread, a react of the bot's other than one saying work is under way, or `slack_pickup` done; none when dm-owed cannot be read, since the check fails open."""
    try:
        held = slack.cc_slack("dm-owed")
    except slack.SlackError:
        return []
    return list(dict.fromkeys(reply.get("thread_ts") or reply["ts"] for reply in held.get("replies") or [] if (reply.get("holder_session") or held.get("holder_session")) == session))


SAID = re.compile(r"\b(?:POSTED|EDITED|CARD) (https://\S+?/archives/\w+/p(\d{10})(\d{6})\S*)")
THREAD_TS = re.compile(r"[?&]thread_ts=(\d+\.\d+)")


def turn_threads(transcript: Path) -> set[str]:
    """The thread of each post say's POSTED, EDITED or CARD lines name in this turn's tool results, the drive's own record of what it posted, so a post Slack's owed list has not caught up with yet still counts, by meta's fix of 736132."""
    threads: set[str] = set()
    for line in tail(transcript):
        entry = json.loads(line)
        content = (entry.get("message") or {}).get("content")
        if entry.get("type") != "user":
            continue
        if isinstance(content, str) or any(block.get("type") == "text" for block in content):
            threads = set()
            continue
        for block in content:
            if block.get("type") != "tool_result":
                continue
            said = block.get("content")
            words = said if isinstance(said, str) else " ".join(part.get("text") or "" for part in said or [] if isinstance(part, dict))
            for found in SAID.finditer(words):
                threads.add(thread[1] if (thread := THREAD_TS.search(found[1])) else f"{found[2]}.{found[3]}")
    return threads


def tail(transcript: Path) -> list[str]:
    with transcript.open("rb") as handle:
        start = max(0, handle.seek(0, os.SEEK_END) - TAIL)
        handle.seek(start)
        return handle.read().decode(errors="replace").splitlines()[1 if start else 0 :]


def turn_text(transcript: Path) -> list[str]:
    """The text the session wrote since the last message it was sent, read from the transcript's tail, since a drive's transcript runs to hundreds of megabytes."""
    texts: list[str] = []
    for line in tail(transcript):
        entry = json.loads(line)
        content = (entry.get("message") or {}).get("content")
        if entry.get("type") == "user" and (isinstance(content, str) or any(block.get("type") == "text" for block in content)):
            texts = []
        elif entry.get("type") == "assistant":
            texts += [block["text"] for block in content if block.get("type") == "text"]
    return texts


def main() -> None:
    from drive import install

    payload = agents.seen(json.load(sys.stdin))
    if reason := block_reason(payload):
        print(json.dumps({"decision": "block", "reason": reason}))
    elif not payload.get("agent_id"):
        install.turn_ended(payload.get("session_id") or "")


if __name__ == "__main__":
    main()
