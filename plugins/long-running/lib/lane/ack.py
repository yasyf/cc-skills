"""His words acknowledged in Slack by his rule ("it should be eyes when reaidng/respiondin, constgrutcion if doing somehting async, and none otherwise, and checkmark if handled (if there was something to handle, else none)"), by the supervisor and never by an owner: the voice puts 👀 on each message of his as a lane records it and it stays while the owner reads it; 🚧 replaces it once the owner has answered while its work goes on, and ✅ replaces either once the work is done or nothing is left running, each moved from the lane's state on every pass."""

import json
import os
import re
import urllib.error
import urllib.request

from lane import store

REACT_URL = "http://127.0.0.1:7378/voice/react"
SECONDS = 5
CHANNEL = re.compile(r"/archives/([A-Z0-9]+)/p")
THREAD = re.compile(r"[?&]thread_ts=([0-9.]+)")


def react(lane: dict, ask: dict, add: str, remove: list[str]) -> None:
    channel = found[1] if (found := CHANNEL.search(ask.get("link") or "")) else lane["channel"]
    request = urllib.request.Request(REACT_URL, json.dumps({"channel_id": channel, "ts": ask["ts"], "thread_ts": found[1] if (found := THREAD.search(ask.get("link") or "")) else "", "add": add, "remove": remove}).encode(), {"Content-Type": "application/json", "X-Claude-Pid": str(os.getpid())})
    try:
        with urllib.request.urlopen(request, timeout=SECONDS):
            return
    except urllib.error.HTTPError as refused:
        raise store.Refused(f"the voice did not react to {ask['ts']}: {refused.code} {refused.read().decode().strip()[:200]}") from refused


def his(ask: dict) -> bool:
    return bool(ask.get("ts")) and not ask["words"].startswith("tapped ")


REMOVES = {"construction": ["eyes"], "white_check_mark": ["construction", "eyes"]}


def wanted(lane: dict, ask: dict) -> str | None:
    """The reaction his message takes from its lane's state now: none while it is unanswered, since the voice's 👀 shows it is being read; ✅ once done, or once answered with nothing left running, because the owner ended its turn waiting only on his reply; 🚧 while the owner runs or waits on work, such as a PR, a check or a staging test."""
    if ask.get("done"):
        return "white_check_mark"
    if not ask.get("replied"):
        return None
    busy = lane.get("state") == store.RUNNING or any(event.get("kind") != "his_reply" for event in (lane.get("wait") or {}).get("on", []))
    return "construction" if busy else "white_check_mark"


def working(drive: str) -> None:
    """Each pass, move the reaction on each message of his to what its lane's state wants now, once per change: 🚧 comes off when the work ends, whether or not its owner ran lane done, and an answer that needed no work goes straight to ✅. One message the voice cannot react to never stops the others, and is tried once per reaction: a refusal is kept on the ask, so no pass fails on it again."""
    refused = []
    for lane_id, lane in store.load_all(drive).items():
        moved, failed = {}, {}
        for ask in lane.get("asks", []):
            had = ask.get("reaction") or ("construction" if ask.get("picked") else "white_check_mark" if ask.get("done") else None)
            if his(ask) and (want := wanted(lane, ask)) and want != had and ask.get("reaction_refused") != want:
                try:
                    react(lane, ask, want, REMOVES[want])
                    moved[ask["ts"]] = want
                except (store.Refused, OSError) as error:
                    refused.append(f"{lane_id} his message {ask['ts']}: {type(error).__name__}: {error}")
                    failed[ask["ts"]] = want
        if moved or failed:

            def keeping(lane: dict, moved: dict = moved, failed: dict = failed) -> None:
                for ask in lane["asks"]:
                    if ask.get("ts") in moved:
                        ask["reaction"] = moved[ask["ts"]]
                    elif ask.get("ts") in failed:
                        ask["reaction_refused"] = failed[ask["ts"]]
                        store.note(lane, f"the voice refused {failed[ask['ts']]} on his message {ask['ts']}; tried once, never again for that reaction")

            store.update(drive, lane_id, keeping, leaving_unreplied=True)
    if refused:
        raise store.Refused("; ".join(refused))


def finished(drive: str, lane_id: str, ts: str) -> None:
    """✅ in place of 🚧 on his message once its owner marks it done."""
    lane = store.load(drive, lane_id)
    ask = next(ask for ask in lane["asks"] if ask.get("ts") == ts)
    if his(ask):
        react(lane, ask, "white_check_mark", REMOVES["white_check_mark"])
        store.update(drive, lane_id, lambda lane: [each.__setitem__("reaction", "white_check_mark") for each in lane["asks"] if each.get("ts") == ts], leaving_unreplied=True)
