import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import install, limits, slack, state

SLACK = "mcp__plugin_cc-slack_cc-slack__"
DM_POST = SLACK + "slack_dm_status"
CHANNEL_POSTS = {SLACK + "slack_reply", SLACK + "slack_send", SLACK + "slack_edit"}
PERMALINK_CHANNEL = re.compile(r"/archives/([A-Z0-9]+)")
SPACE = re.compile(r"\s+")
ELIDED = re.compile(r"…|\.{3}")
LIFT = (
    f" Two posts get {limits.QUESTION_WORDS}: an answer to his own words asking for an explanation or a comparison,"
    " with them quoted as asked; and the installed confirmation of a ruling of his, with the ruling quoted as asked and"
    ' installed naming the plugin and the version now installed, such as "long-running 1.0.76", which the post states.'
)
NOT_HIS = (
    " The asked words raise the limit only when they ask for an explanation or a comparison, or come with installed,"
    " and stand, word for word, in a message of his in the thread this post goes to or in your brief; these do not."
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


def squashed(text: str) -> str:
    return SPACE.sub(" ", html.unescape(text)).strip().casefold()


def quotes(asked: str, said: str) -> bool:
    """Whether every part of the asked words, split where a quote elides his, stands word for word in what he said."""
    said = squashed(said)
    return all(part.strip() in said for part in ELIDED.split(squashed(asked)) if part.strip())


def not_installed(post: dict) -> str | None:
    """Why the post's installed field is not a plugin version this machine has installed now and the post states, or None when it is."""
    name, _, version = post["installed"].partition(" ")
    version = version.strip()
    plugins = json.loads((install.config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    now = sorted({install.installed_version(key) for key in plugins if key.partition("@")[0] == name})
    if version not in now:
        return f" installed names {name} {version}, but this machine has {name} at {', '.join(now) or 'no version'}."
    said = (post.get("text") or "") + " " + limits.block_text(post.get("blocks") or [])
    if not re.search(rf"(?<![\w.]){re.escape(version)}(?!\.?\w)", said):
        return f" The post never says the version it confirms, {version}."
    return None


def deny_reason(payload: dict) -> str | None:
    """Why a post to his DM goes out longer than he reads: his own words asking for an explanation or a comparison, or his ruling confirmed as installed, raise it to a card's length, and nothing else does."""
    dm = his_dm(payload.get("session_id") or "")
    if dm is None or not posts_to(payload, dm):
        return None
    tool_input = payload["tool_input"]
    if (refusal := limits.post_refusal(tool_input)) is None:
        return None
    if not (asked := tool_input.get("asked") or ""):
        return refusal + LIFT
    lift = "installed" if tool_input.get("installed") else "asked"
    if lift == "installed" and (wrong := not_installed(tool_input)):
        return refusal + wrong
    if lift == "asked" and not limits.ASKS_FOR_MORE.search(asked):
        return refusal + NOT_HIS
    try:
        his = quotes(asked, brief(payload)) or quotes(asked, his_messages(payload, dm))
    except slack.SlackError as error:
        return refusal + f" His thread could not be read to check the asked words: {error}."
    return limits.post_refusal(tool_input, lift) if his else refusal + NOT_HIS


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
