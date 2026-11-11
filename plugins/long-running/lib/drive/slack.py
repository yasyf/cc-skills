import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from drive import state
from drive.install import config_dir

PLUGIN = "cc-slack@cc-slack"
MESSAGE_LINK = re.compile(r"/archives/[A-Z0-9]+/p(\d{10})(\d{6})")
CARD_LINK = re.compile(r"/archives/([A-Z0-9]+)/p(\d{10})(\d{6})(?:\?thread_ts=([\d.]+))?")
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


def check_post(text: str) -> list[str]:
    """What `cc-slack check-post` finds in a post's exact words, one finding and its fix a line."""
    try:
        result = subprocess.run([launcher(), "check-post", "-"], input=text, check=False, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SlackError(str(error)) from None
    if result.returncode > 1:
        raise SlackError(" ".join(result.stderr.split()) or f"cc-slack check-post exited {result.returncode}")
    return result.stdout.splitlines()


def thread(*where: str) -> list[dict]:
    return cc_slack("thread", *where)["messages"]


def withdraw_pr_card(url: str, reason: str) -> list[str]:
    return cc_slack("withdraw-pr-card", "--pr", url, "--reason", reason)["withdrawn"]


def card_threads(data: dict, order: dict) -> list[tuple[str, str]]:
    """The channel and thread of each card the order's jobs drew, where his answers to that card go."""
    links = (CARD_LINK.search(data["jobs"][job_id].get("card") or "") for job_id in order["jobs"])
    return list(dict.fromkeys((link[1], link[4] or f"{link[2]}.{link[3]}") for link in links if link))


def order_threads(data: dict, order: dict) -> list[tuple[str, str]]:
    """The threads a line for an order goes in: its own, then each of its cards'."""
    own = state.promise_thread(data, order)
    return list(dict.fromkeys([*([own] if own else []), *card_threads(data, order)]))


def order_posts(data: dict, order: dict) -> tuple[list[datetime], str | None]:
    """When the drive posted in an order's threads, its own and each of its cards' past the card itself, oldest first among each thread's most recent messages, and why a thread could not be read."""
    if not (threads := order_threads(data, order)):
        return [], "it has no thread to read; give it one with drive order set --thread"
    cards = set(card_threads(data, order))
    posts: list[datetime] = []
    unread = None
    for where in threads:
        try:
            messages = thread("--channel", where[0], "--thread", where[1], "--limit", str(RECENT))
        except SlackError as error:
            unread = unread or f"its thread {where[1]} could not be read: {error}"
            continue
        posts += [
            datetime.fromtimestamp(float(message["ts"])).astimezone()
            for message in messages
            if message.get("from_claude") and not (where in cards and message["ts"] == where[1])
        ]
    return sorted(posts), unread


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
