"""His words acknowledged in Slack the way the old communicator did, by the supervisor and never by an owner: 👀 on each message of his the moment a lane takes it, Slack's working line under it once its owner is on it, and ✅ once the owner marks it done."""

import os
import subprocess

from lane import store

SECONDS = 20


def slack(args: list[str]) -> None:
    env = {name: value for name, value in os.environ.items() if name != "CLAUDE_CODE_SESSION_ID"}
    done = subprocess.run(["cc-slack", *args, "--cwd", "/"], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False, timeout=SECONDS)
    if done.returncode:
        raise store.Refused(f"cc-slack {args[0]} exited {done.returncode}: {(done.stderr or done.stdout).strip()[:300]}")


def where(lane: dict, ask: dict) -> list[str]:
    return ["--url", ask["link"]] if ask.get("link") else ["--channel", lane["channel"], "--ts", ask["ts"]]


def his(ask: dict) -> bool:
    return bool(ask.get("ts")) and not ask["words"].startswith("tapped ")


def seen(drive: str, heard: dict[str, list[dict]]) -> None:
    """👀 on each new message of his a lane took this pass."""
    for lane_id, asks in heard.items():
        lane = store.load(drive, lane_id)
        for ask in asks:
            if his(ask):
                slack(["react", *where(lane, ask), "--name", "eyes"])


def working(drive: str) -> None:
    """Slack's working line under each message of his not yet answered whose lane's owner is running, once each."""
    for lane_id, lane in store.load_all(drive).items():
        if lane["state"] != store.RUNNING:
            continue
        fresh = [ask for ask in store.unreplied(lane) if his(ask) and not ask.get("picked")]
        for ask in fresh:
            slack(["pickup", *where(lane, ask), "--state", f"Lane {lane_id} is on it"])
        if fresh:
            picked = {ask["ts"] for ask in fresh}

            def marking(lane: dict) -> None:
                for ask in lane["asks"]:
                    if ask.get("ts") in picked:
                        ask["picked"] = store.stamp()

            store.update(drive, lane_id, marking, leaving_unreplied=True)


def finished(drive: str, lane_id: str, ts: str) -> None:
    """✅ on his message once its owner marks it done, the working line taken away."""
    lane = store.load(drive, lane_id)
    ask = next(ask for ask in lane["asks"] if ask.get("ts") == ts)
    if his(ask):
        slack(["pickup", *where(lane, ask), "--done"])
