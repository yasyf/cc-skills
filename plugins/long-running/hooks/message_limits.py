import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import limits, state

SLACK = "mcp__plugin_cc-slack_cc-slack__"
DM_POST = SLACK + "slack_dm_status"
CHANNEL_POSTS = {SLACK + "slack_reply", SLACK + "slack_send", SLACK + "slack_edit"}
PERMALINK_CHANNEL = re.compile(r"/archives/([A-Z0-9]+)")
SPACE = re.compile(r"\s+")
LIFT = " Only his own message asking for more lifts it: pass his words, quoted, as asked."
NOT_HIS = (
    " The asked words lift the limit only when they ask for more and stand, word for word, in his message as your"
    " brief quotes it; these do not."
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


def squashed(text: str) -> str:
    return SPACE.sub(" ", text).strip().casefold()


def asked_for_more(payload: dict) -> bool:
    asked = payload["tool_input"].get("asked") or ""
    return bool(limits.ASKS_FOR_MORE.search(asked)) and squashed(asked) in squashed(brief(payload))


def deny_reason(payload: dict) -> str | None:
    """Why a post to his DM goes out longer than he reads: none but his own words asking for more lift the limit."""
    dm = his_dm(payload.get("session_id") or "")
    if dm is None or not posts_to(payload, dm):
        return None
    refusal = limits.post_refusal(payload["tool_input"])
    if refusal is None or asked_for_more(payload):
        return None
    return refusal + (NOT_HIS if payload["tool_input"].get("asked") else LIFT)


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
