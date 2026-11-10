import json
import re
import subprocess
from datetime import datetime

from drive import state

MESSAGE_LINK = re.compile(r"/archives/[A-Z0-9]+/p(\d{10})(\d{6})")
RECENT = 50
WHOLE_THREAD = 1000


class SlackError(Exception):
    pass


def thread(*where: str) -> list[dict]:
    try:
        result = subprocess.run(["cc-slack", "thread", *where], check=False, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SlackError(str(error)) from None
    if result.returncode:
        raise SlackError(" ".join(result.stderr.split()) or f"cc-slack exited {result.returncode}")
    return json.loads(result.stdout)["messages"]


def order_posts(data: dict, order: dict) -> tuple[list[datetime], str | None]:
    """When the drive posted in the thread an order's promised line goes in, oldest first among its most recent messages, and why the thread could not be read."""
    if not (where := state.promise_thread(data, order)):
        return [], "it has no thread to read; give it one with drive order set --thread"
    try:
        messages = thread("--channel", where[0], "--thread", where[1], "--limit", str(RECENT))
    except SlackError as error:
        return [], f"its thread could not be read: {error}"
    return sorted(datetime.fromtimestamp(float(message["ts"])).astimezone() for message in messages if message.get("from_claude")), None


def message(link: str) -> dict:
    """The message a Slack permalink names, read from its thread."""
    match = MESSAGE_LINK.search(link)
    if not match:
        raise SlackError(f"not a Slack message link: {link}")
    ts = f"{match[1]}.{match[2]}"
    found = next((message for message in thread("--url", link, "--limit", str(WHOLE_THREAD)) if message["ts"] == ts), None)
    if found is None:
        raise SlackError(f"no message at {link}")
    return found
