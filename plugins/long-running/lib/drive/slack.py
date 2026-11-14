import functools
import html
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from drive import state
from drive.install import config_dir

PLUGIN = "cc-slack@cc-slack"
MESSAGE_LINK = re.compile(r"/archives/([A-Z0-9]+)/p(\d{10})(\d{6})")
PERMALINK = re.compile(r"https://[\w.-]+\.slack\.com/archives/[A-Z0-9]+/p\d{16}(?:\?[\w.=&%-]*\w)?")
WAITS_WITHOUT_CARD = re.compile(r"^\d+:\d+ waits-without-card\b")
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


def cc_slack(*args: str, timeout: int = 90, stdin: str | None = None) -> dict:
    try:
        result = subprocess.run([launcher(), *args], input=stdin, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SlackError(str(error)) from None
    if result.returncode:
        raise SlackError(" ".join(result.stderr.split()) or f"cc-slack {args[0]} exited {result.returncode}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        first = result.stdout.lstrip().partition("\n")[0][:80]
        raise SlackError(f"cc-slack {args[0]} exited 0 with {len(result.stdout)} bytes that are not JSON, starting {first!r}") from None


@functools.cache
def whoami() -> dict:
    return cc_slack("whoami")


def dm() -> str:
    """His DM: cc-slack's DM with him, as `cc-slack whoami` names it, where dm-status draws every card and post to him."""
    return whoami()["dm_channel_id"]


def workspace() -> str:
    """His Slack workspace's host, from the team_url `cc-slack whoami` names, such as poetic-technology.slack.com."""
    return urlparse(whoami()["team_url"]).hostname


def check_post(text: str, asked: list[str]) -> list[str]:
    """What `cc-slack check-post` finds in a post's exact words, one finding and its fix a line, each message of his the post answers passed as --asked; an exit 1 that prints no finding failed."""
    try:
        result = subprocess.run(
            [launcher(), "check-post", "-", *(f"--asked={message}" for message in asked)], input=text, check=False, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SlackError(str(error)) from None
    if result.returncode > 1 or result.returncode and not result.stdout.strip():
        raise SlackError(" ".join(result.stderr.split()) or f"cc-slack check-post exited {result.returncode}")
    return result.stdout.splitlines()


def post_findings(text: str, draws_card: bool, asked: str | list[str] = "") -> list[str]:
    """What `cc-slack check-post` finds in the words a post sends, a drive word his asked messages use passing; a post that draws a question card or a PR's tap card carries the card its words say waits on him."""
    return [finding for finding in check_post(text, messages(asked)) if not (draws_card and WAITS_WITHOUT_CARD.match(finding))]


def messages(asked: str | list[str]) -> list[str]:
    """The messages of his asked holds: a list's entries, a string that is a JSON list of strings, as a list arrives through a tool whose schema declares no asked, or the one string; none for an empty asked."""
    if isinstance(asked, list):
        return asked
    if not asked:
        return []
    try:
        listed = json.loads(asked)
    except json.JSONDecodeError:
        return [asked]
    return listed if isinstance(listed, list) and all(isinstance(entry, str) for entry in listed) else [asked]


def draw_refusal(card: dict) -> str | None:
    """Why cc-slack refuses to draw a status card, in its own words, or None when it draws; `cc-slack card render` runs the checks the post runs, reaching neither Slack nor the daemon."""
    try:
        result = subprocess.run(
            [launcher(), "card", "render", "--card", "/dev/stdin"], input=json.dumps(card), check=False, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SlackError(str(error)) from None
    if not result.returncode:
        return None
    return " ".join(result.stderr.split()) or f"cc-slack card render exited {result.returncode}"


def thread(*where: str) -> list[dict]:
    return cc_slack("thread", *where, "--json")["messages"]


def posted_question(url: str) -> tuple[str, str, str, list[str]] | None:
    """The question, summary, recommended option and options his question card at url shows, read from its message in Slack, which keeps the card after cc-slack's store forgets it: the question and options each Pick button carries, the recommended option the primary one picks, and the summary its first section draws above *Show me*; None when that message holds no question card."""
    link = MESSAGE_LINK.search(url)
    blocks = next((message["raw"]["blocks"] for message in thread("--url", url, "--raw") if message["ts"] == f"{link[2]}.{link[3]}"), [])
    picks = [(json.loads(block["accessory"]["value"]), block["accessory"].get("style")) for block in blocks if block.get("block_id", "").startswith("card-option-")]
    if not picks:
        return None
    summary = next((block["text"]["text"] for block in blocks if block["type"] == "section" and "accessory" not in block), "")
    return picks[0][0]["q"], html.unescape(summary).partition("\n*Show me*")[0], next((pick["label"] for pick, style in picks if style == "primary"), ""), picks[0][0]["o"]


def group_dm(person: str) -> str:
    """The channel of the group DM of a person, him and the bot, which cc-slack opens with conversations.open for a channel naming the person."""
    return cc_slack("history", "--channel", person, "--limit", "1", "--json")["channel_id"]


def withdraw_pr_card(url: str, reason: str) -> list[str]:
    return cc_slack("withdraw-pr-card", "--pr", url, "--reason", reason)["withdrawn"]


def redraw_pr_cards(url: str) -> int:
    """Redraw in place, from `drive card`, every open card carrying the PR, keeping its buttons; how many cc-slack queued, with no new post."""
    return cc_slack("redraw-pr-cards", "--pr", url)["redrawing"]


def order_threads(data: dict, order_id: str, order: dict) -> list[tuple[str, str]]:
    """The threads of an order: its own, then on an incident the own thread of each other order of it, the incident an alert's order joined and every alert joined to it, both ways, then each thread one of them keeps: each a post naming it went in, each card its jobs drew and each PR card drawn at his DM root for a PR it serves."""
    orders = [order, *(data["orders"][other] for other in state.incident_orders(data, order_id) if other != order_id)]
    own = filter(None, map(state.own_thread, orders))
    kept = ((channel, ts) for each in orders for channel, ts, _, _ in each["threads"])
    return list(dict.fromkeys([*own, *kept]))


def order_posts(data: dict, order_id: str, order: dict) -> tuple[list[datetime], str | None]:
    """When the drive posted in an order's threads, its own, its incident's and each of their cards' past the card itself, oldest first among each thread's messages since its promised time, and why a thread could not be read."""
    if not (threads := order_threads(data, order_id, order)):
        return [], "it has no thread to read; give it one with drive order set --thread"
    cards = {(channel, ts) for other in state.incident_orders(data, order_id) for channel, ts, _, kind in data["orders"][other]["threads"] if kind != "posted"}
    since = str(int(state.parse_time(order["promised"]).timestamp()))
    posts: list[datetime] = []
    unread = None
    for where in threads:
        try:
            messages = thread("--channel", where[0], "--thread", where[1], "--since-ts", since, "--limit", str(WHOLE_THREAD))
        except SlackError as error:
            unread = unread or f"its thread {where[1]} could not be read: {error}"
            continue
        posts += [
            sent_at(message)
            for message in messages
            if message.get("from_claude") and not (where in cards and message["ts"] == where[1])
        ]
    return sorted(posts), unread


def sent_at(message: dict) -> datetime:
    return datetime.fromtimestamp(float(message["ts"])).astimezone()


def message(link: str) -> dict:
    """The message a Slack permalink names, read from its thread."""
    return message_and_root(link)[0]


def root(channel: str, ts: str) -> dict | None:
    """The message a thread in the channel hangs from, None when the thread holds no message at its own ts."""
    return next((message for message in thread("--channel", channel, "--thread", ts, "--limit", str(WHOLE_THREAD)) if message["ts"] == ts), None)


def thread_of(channel: str, ts: str) -> str:
    """The ts of the thread the message at ts is in, read in one call: its parent's when it is a reply, as chat.postMessage's thread_ts takes it, else its own."""
    messages = thread("--channel", channel, "--thread", ts, "--limit", "1")
    return next((message["thread_ts"] for message in messages if message["ts"] == ts and message.get("thread_ts")), ts)


def message_and_root(link: str) -> tuple[dict, dict | None]:
    """The message a Slack permalink names and the message its thread hangs from, None for a message at the channel's root, read from that thread in one call; a reply's link with no thread_ts reads the reply alone, so its parent is read from its own thread."""
    match = MESSAGE_LINK.search(link)
    if not match:
        raise SlackError(f"not a Slack message link: {link}")
    messages = thread("--url", link, "--limit", str(WHOLE_THREAD))
    ts = f"{match[2]}.{match[3]}"
    found = next((message for message in messages if message["ts"] == ts), None)
    if found is None:
        raise SlackError(f"no message {ts} in the thread at --url {link}")
    if (parent := found.get("thread_ts")) in (None, ts):
        return found, None
    return found, next((message for message in messages if message["ts"] == parent), None) or root(match[1], parent)


def posted(link: str, text: str) -> dict:
    """The drive's own post at a permalink as message reads it, from the words it was sent with: a post just made needs no read of itself."""
    match = state.CARD_LINK.search(link)
    if not match:
        raise SlackError(f"not a Slack message link: {link}")
    ts, parent = f"{match[2]}.{match[3]}", match[4]
    return {"ts": ts, "text": text, "from_claude": True} | ({"thread_ts": parent} if parent else {})


def message_at(ts: str, *where: str) -> dict:
    """The message at ts in the thread where names."""
    found = next((message for message in thread(*where, "--limit", str(WHOLE_THREAD)) if message["ts"] == ts), None)
    if found is None:
        raise SlackError(f"no message {ts} in the thread at {' '.join(where)}")
    return found
