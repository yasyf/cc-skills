from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

from livedash import Context, Entry, Feed, component, view


def permalink(thread_url: str, channel: str, ts: str, thread_ts: str) -> str:
    host = urlsplit(thread_url).netloc
    return f"https://{host}/archives/{channel}/p{ts.replace('.', '')}?thread_ts={thread_ts}&cid={channel}"


@component("slack-feed", "Slack test threads", question="Who answered in each test thread, and how long after it opened?", reads=["cc-slack thread --url"], every="1m", timeout="60s")
def slack_feed(ctx: Context, *, threads: list[str] = [], bot: str | None = None, prefix: str | None = None, limit: int = 50) -> Feed:
    """Messages from each Slack thread permalink in `threads` through `cc-slack thread --url`, newest first: sender,
    permalink and latency from the thread's opening message, never the message text. `bot` keeps messages whose sender
    name contains it; `prefix` keeps messages that start with it."""
    if not threads:
        return Feed([], note="Not run: no test thread is named yet; set `threads` to Slack permalinks.")
    entries = []
    for url in threads:
        thread = ctx.json(["cc-slack", "thread", "--url", url, "--limit", str(limit)])
        origin = float(thread["thread_ts"])
        for message in thread["messages"]:
            if bot and bot.lower() not in (message.get("user_name") or "").lower():
                continue
            if prefix and not message["text"].startswith(prefix):
                continue
            at = datetime.fromtimestamp(float(message["ts"]), timezone.utc)
            entries.append(
                Entry(
                    view.iso(at),
                    message.get("user_name") or message.get("user") or "?",
                    "opened the thread" if message["ts"] == thread["thread_ts"] else "replied",
                    permalink(url, thread["channel_id"], message["ts"], thread["thread_ts"]),
                    "muted" if message.get("from_claude") else None,
                    round((float(message["ts"]) - origin) * 1000),
                    key=f"{thread['channel_id']}:{message['ts']}",
                )
            )
    entries.sort(key=lambda entry: entry.at, reverse=True)
    return Feed(entries, note=None if entries else "No message in the named threads matches yet.")
