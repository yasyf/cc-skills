import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import limits, slack, state

SLACK = "mcp__plugin_cc-slack_cc-slack__"
DM_POST = SLACK + "slack_dm_status"
EDIT = SLACK + "slack_edit"
CHANNEL_POSTS = {SLACK + "slack_reply", SLACK + "slack_send", EDIT}
PERMALINK_CHANNEL = re.compile(r"/archives/([A-Z0-9]+)")
SPACE = re.compile(r"\s+")
BETWEEN_QUOTES = re.compile(r"…|\.{3}|\n|\s/\s")
QUOTE_MARKS = "\"'“”‘’"
STRAIGHT = str.maketrans("‘’“”", "''\"\"")
WHOLE = (
    " It names no message of his as asked, so it is not an answer and keeps that limit. An answer to a question or"
    " order of his goes out whole, at any length: pass as asked each message of his it answers, word for word, one per line."
)


def his_dm(session: str) -> str | None:
    """His DM with the app, when this session is a drive's communicator, whose handlers write everything he reads."""
    for name in state.session_drives(session):
        data = state.load(name)
        if state.session_role(data, session) == "communicator":
            return data["slack"]["channel"]
    return None


def posts_to(payload: dict, dm: str) -> bool:
    if payload["tool_name"] == DM_POST:
        return True
    tool_input = payload["tool_input"]
    channel = tool_input.get("channel_id") or next(iter(PERMALINK_CHANNEL.findall(tool_input.get("url") or "")), None)
    return payload["tool_name"] in CHANNEL_POSTS and channel == dm


def brief(payload: dict) -> str:
    """The prompt the communicator started this handler with, which quotes his message."""
    if not payload.get("agent_id"):
        return ""
    lane = Path(payload["transcript_path"]).with_suffix("") / "subagents" / f"agent-{payload['agent_id']}.jsonl"
    if not lane.is_file():
        return ""
    with lane.open() as transcript:
        content = json.loads(transcript.readline())["message"]["content"]
    return content if isinstance(content, str) else " ".join(part.get("text", "") for part in content)


def his_messages(payload: dict, dm: str) -> str:
    """What he wrote in the thread the post goes to, read from Slack; in his DM with the app every message not the bot's is his."""
    tool_input = payload["tool_input"]
    if tool_input.get("thread_ts"):
        where = ("--channel", dm, "--thread", tool_input["thread_ts"])
    elif tool_input.get("url"):
        where = ("--url", tool_input["url"])
    else:
        return ""
    return " ".join(message["text"] for message in slack.thread(*where, "--limit", str(slack.WHOLE_THREAD)) if not message.get("from_bot"))


def edited_words(payload: dict, dm: str) -> int:
    """How long the post an edit replaces is, read from Slack; 0 for a new post."""
    if payload["tool_name"] != EDIT:
        return 0
    tool_input = payload["tool_input"]
    if url := tool_input.get("url"):
        return limits.words(slack.message(url)["text"])
    ts = tool_input["ts"]
    return limits.words(slack.message_at(ts, "--channel", dm, "--thread", tool_input.get("thread_ts") or ts)["text"])


def squashed(text: str) -> str:
    return SPACE.sub(" ", html.unescape(text).translate(STRAIGHT)).strip().casefold()


def quotes(asked: str | list[str]) -> list[str]:
    """Each stretch of his words that asked quotes: one message of his per entry or line, cut where the quote skips some of them."""
    entries = [asked] if isinstance(asked, str) else asked
    return [part for entry in entries for piece in BETWEEN_QUOTES.split(entry) if (part := piece.strip().strip(QUOTE_MARKS).strip())]


def unsaid(parts: list[str], said: str) -> list[str]:
    said = squashed(said)
    return [part for part in parts if squashed(part) not in said]


def deny_reason(payload: dict) -> str | None:
    """Why a post to his DM goes out longer than he reads: a routine line, summary or card keeps its limit, an edit may reword its post at that post's length, and an answer quoting any message of his word for word as asked goes out whole."""
    dm = his_dm(payload.get("session_id") or "")
    if dm is None or not posts_to(payload, dm):
        return None
    tool_input = payload["tool_input"]
    if (refusal := limits.post_refusal(tool_input)) is None or tool_input.get("card"):
        return refusal
    try:
        refusal = limits.post_refusal(tool_input, edited_words(payload, dm))
    except slack.SlackError as error:
        refusal += f" The post it edits could not be read to check its length: {error}."
    if refusal is None:
        return None
    if not (asked := quotes(tool_input.get("asked") or "")):
        return refusal + WHOLE
    if len(unbriefed := unsaid(asked, brief(payload))) < len(asked):
        return None
    try:
        missing = unsaid(unbriefed, his_messages(payload, dm))
    except slack.SlackError as error:
        return refusal + f" His thread could not be read to check asked, so it is not yet an answer: {error}."
    if len(missing) < len(asked):
        return None
    named = ", ".join(f'"{part}"' for part in missing)
    return (
        refusal + " It is not an answer, so it keeps that limit: asked lifts the limit only with his own words, and these stand"
        f" in no message of his in the thread this post goes to, nor in your brief: {named}. Copy each message of his this post"
        " answers word for word, one per line, with … where you skip words."
    )


def denial(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def main() -> None:
    if reason := deny_reason(json.load(sys.stdin)):
        print(json.dumps(denial(reason)))


if __name__ == "__main__":
    main()
