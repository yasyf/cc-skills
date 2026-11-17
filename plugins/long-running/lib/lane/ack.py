"""His words acknowledged in Slack the way the old communicator did, by the supervisor and never by an owner: the voice puts 👀 on each message of his as a lane records it; 🚧 replaces it once the lane's owner is on it, and ✅ replaces that once the owner marks it done."""

import json
import os
import re
import urllib.error
import urllib.request

from lane import store

REACT_URL = "http://127.0.0.1:7378/voice/react"
SECONDS = 5
CHANNEL = re.compile(r"/archives/([A-Z0-9]+)/p")


def react(lane: dict, ask: dict, add: str, remove: list[str]) -> None:
    channel = found[1] if (found := CHANNEL.search(ask.get("link") or "")) else lane["channel"]
    request = urllib.request.Request(REACT_URL, json.dumps({"channel_id": channel, "ts": ask["ts"], "add": add, "remove": remove}).encode(), {"Content-Type": "application/json", "X-Claude-Pid": str(os.getpid())})
    try:
        with urllib.request.urlopen(request, timeout=SECONDS):
            return
    except urllib.error.HTTPError as refused:
        raise store.Refused(f"the voice did not react to {ask['ts']}: {refused.code} {refused.read().decode().strip()[:200]}") from refused


def his(ask: dict) -> bool:
    return bool(ask.get("ts")) and not ask["words"].startswith("tapped ")


def working(drive: str) -> None:
    """🚧 in place of 👀 on each message of his not yet answered whose lane's owner is running, once each."""
    for lane_id, lane in store.load_all(drive).items():
        if lane["state"] != store.RUNNING:
            continue
        fresh = [ask for ask in store.unreplied(lane) if his(ask) and not ask.get("picked")]
        for ask in fresh:
            react(lane, ask, "construction", ["eyes"])
        if fresh:
            picked = {ask["ts"] for ask in fresh}

            def marking(lane: dict) -> None:
                for ask in lane["asks"]:
                    if ask.get("ts") in picked:
                        ask["picked"] = store.stamp()

            store.update(drive, lane_id, marking, leaving_unreplied=True)


def finished(drive: str, lane_id: str, ts: str) -> None:
    """✅ in place of 🚧 on his message once its owner marks it done."""
    lane = store.load(drive, lane_id)
    ask = next(ask for ask in lane["asks"] if ask.get("ts") == ts)
    if his(ask):
        react(lane, ask, "white_check_mark", ["construction", "eyes"])
