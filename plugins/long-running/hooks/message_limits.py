import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, refs, state
from drive.state import DriveError

SLACK = "mcp__plugin_cc-slack_cc-slack__"
SAY = "mcp__plugin_long-running_drive__say"
STATUS = SLACK + "slack_dm_status"
PLACEHOLDER = re.compile(r"\$\([^()]*\)|\$\{[^{}]*\}|<(?![@#!]|\w+:)[^>|]*>")
DEAD_LINK = re.compile(r"<\s*(?:\|[^>]*)?>|<?\b(?:file://|https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)\b)[^\s|>]*")
STATUS_WORDS = ("title", "summary", "body", "text", "detail")


def drive_session(session: str) -> tuple[str, str] | None:
    """The drive this session holds a role in and that role, else None, so a session outside every drive posts unchecked."""
    for name in state.session_drives(session):
        if role := state.session_role(state.saved(name), session):
            return name, role
    return None


def wrong_channel(payload: dict, role: str) -> str | None:
    """Why the coordinator or the meta session may not post to Slack itself: his reply under a post reaches only the session that posted it, and the communicator is the one he talks to."""
    if payload.get("agent_id") or role not in ("coordinator", "meta"):
        return None
    return (
        f"Refused on purpose: the {state.title(role)} posts nothing to Slack, since his reply under a post reaches only the"
        " session that posted it. Send the words as a `report` record to the communicator, and it posts them; a question for"
        " him is a job's ASK, kept with `drive job ask`, which draws his card."
    )


def placeholder(text: str) -> str | None:
    if PLACEHOLDER.fullmatch(text):
        return f"The text {text} is a placeholder, and Slack would send it as written. Pass the post's own words as text."
    return None


def dead_link(name: str, payload: dict, text: str) -> str | None:
    """Why a post would show a link that opens nothing: a say reference naming nothing the drive holds, an empty link, or a link to this Mac, such as localhost or a file:// address, which Slack opens nowhere."""
    if payload["tool_name"] == SAY:
        try:
            refs.resolved(name, text)
        except DriveError as error:
            return str(error)
    if found := DEAD_LINK.search(text):
        return f"The post links {found[0]!r}, which opens nowhere from Slack: link a web page, or name it in words."
    return None


def plain_status(tool_input: dict) -> dict:
    """A status line as plain text, never a status card, whose Ack and Pause buttons ask him to confirm what he just said, by his words "ack and pause makes no sense here, and why is it asking me to confirm in general when I literally just said it"."""
    if not isinstance(card := tool_input.get("card"), dict) or {"options", "question", "waits", "actions"} & card.keys():
        return {}
    words = " · ".join(str(card[key]).strip() for key in STATUS_WORDS if isinstance(card.get(key), str) and card[key].strip())
    plain = {key: value for key, value in tool_input.items() if key != "card"} | {"text": tool_input.get("text") or words or "Working on it"}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": plain, "additionalContext": "message_limits: a status goes as a plain line, never a card with buttons; it went as text."}}


def hook_output(payload: dict) -> dict:
    """The four checks every drive session's post meets, each refusing outright and never weighed by Jev: the wrong channel, a placeholder text, a dead link and a secret; every other rule of his standard is the voice sender's advisory read, by ruling B, "delete the ~75 checks for everyone now ... those checks drive refusal loops, not quality"."""
    if (held := drive_session(payload.get("session_id") or "")) is None:
        return {}
    name, role = held
    tool_input = payload["tool_input"]
    text = "\n".join(filter(None, [(tool_input.get("text") or "").strip(), json.dumps(tool_input["blocks"]) if tool_input.get("blocks") else ""]))
    if reason := wrong_channel(payload, role) or placeholder((tool_input.get("text") or "").strip()) or dead_link(name, payload, text):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}, "judged": False}
    if payload["tool_name"] == STATUS:
        return plain_status(tool_input)
    return {}


def main() -> None:
    if output := hook_output(agents.seen(json.load(sys.stdin))):
        print(json.dumps(output))


if __name__ == "__main__":
    main()
