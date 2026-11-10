import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from drive import state
from drive.install import config_dir

PLUGIN = "cc-slack@cc-slack"
MESSAGE_LINK = re.compile(r"/archives/[A-Z0-9]+/p(\d{10})(\d{6})")
RECENT = 50
WHOLE_THREAD = 1000


class SlackError(Exception):
    pass


def launcher() -> str:
    """The newest installed cc-slack plugin's launcher, which runs on any PATH, a hook's included, where a bare cc-slack may not."""
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    if not (installs := plugins.get(PLUGIN)):
        raise FileNotFoundError(f"the {PLUGIN} plugin is not installed")
    newest = max(installs, key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")
    return str(Path(newest["installPath"]) / "bin" / "cc-slack")


def cc_slack(*args: str) -> dict:
    try:
        result = subprocess.run([launcher(), *args], check=False, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SlackError(str(error)) from None
    if result.returncode:
        raise SlackError(" ".join(result.stderr.split()) or f"cc-slack exited {result.returncode}")
    return json.loads(result.stdout)


def thread(*where: str) -> list[dict]:
    return cc_slack("thread", *where)["messages"]


def withdraw_pr_card(url: str, reason: str) -> list[str]:
    return cc_slack("withdraw-pr-card", "--pr", url, "--reason", reason)["withdrawn"]


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
    return message_at(f"{match[1]}.{match[2]}", "--url", link)


def message_at(ts: str, *where: str) -> dict:
    """The message at ts in the thread where names."""
    found = next((message for message in thread(*where, "--limit", str(WHOLE_THREAD)) if message["ts"] == ts), None)
    if found is None:
        raise SlackError(f"no message {ts} in the thread at {' '.join(where)}")
    return found
