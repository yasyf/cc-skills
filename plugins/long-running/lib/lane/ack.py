"""His words acknowledged in Slack by his rule ("it should be eyes when reading/responding, construction if doing something async, and none otherwise, and checkmark if handled (if there was something to handle, else none)"), by the supervisor and never by an owner: the voice puts 👀 on each message of his as a lane records it and it stays while the owner reads it; 🚧 replaces it once the owner has answered and the work goes on, and ✅ replaces either once the owner marks it done."""

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
    """🚧 in place of 👀 on each message of his its owner answered and is still working on, once each: a message still being read keeps its 👀."""
    for lane_id, lane in store.load_all(drive).items():
        fresh = [ask for ask in lane.get("asks", []) if his(ask) and ask.get("replied") and not ask.get("done") and not ask.get("picked")]
        for ask in fresh:
            react(lane, ask, "construction", ["eyes"])
        if fresh:
            picked = {ask["ts"] for ask in fresh}

            def marking(lane: dict, picked: set[str] = picked) -> None:
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
