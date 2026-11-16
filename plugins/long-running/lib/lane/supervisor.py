"""The supervisor: each pass reads the outside facts the waits name, ranks the lanes, renews nothing a model holds, and starts an owner for every lane whose wait fired or whose lease lapsed, in rank order up to the slots."""

import json
import os
import plistlib
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from lane import facts, rank, store, verbs
from lane import owner as owners

RESTART_WINDOW = timedelta(minutes=30)


def heartbeat(drive: str, moment: datetime, errors: list[str]) -> None:
    path = store.drive_dir(drive) / "supervisor.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = path.with_name(".supervisor.json.tmp")
    fresh.write_text(json.dumps({"pid": os.getpid(), "at": store.stamp(moment), "errors": errors}) + "\n")
    fresh.replace(path)


def fired(drive: str, lane: dict, prs: dict, moment: datetime) -> str | None:
    """Why a waiting lane's wait fired, or None."""
    wait = lane["wait"]
    if wait.get("fired"):
        return wait.get("fired_why") or "woken"
    if facts.unseen(lane):
        return "his new words"
    if lane.get("parked"):
        return None
    for event in wait.get("on", []):
        if event["kind"] != "reply" and facts.holds(drive, lane, event, prs):
            return f"{event['kind']} {event.get('fact') or event.get('id')}" + (f" on {event['repo']}#{event['number']}" if event["kind"] == "pr" else "")
    if wait.get("until") and store.parse(wait["until"]) <= moment:
        return f"its time bound {wait['until']} came"
    return None


def backoff(lane: dict, moment: datetime, shape: dict) -> timedelta | None:
    recent = [stamp for stamp in lane.get("restarts", []) if moment - store.parse(stamp) < RESTART_WINDOW]
    over = len(recent) - shape["restarts_before_backoff"]
    return timedelta(minutes=min(60, 5 * 2**over)) if over >= 0 else None


def run_pass(drive: str, only: set[str] | None = None, read_outside: bool = True) -> dict:
    """One pass; what it started and why, and each read that failed, which never gates a time wake or a lease."""
    shape = store.config(drive)
    moment = store.now()
    errors: list[str] = []
    snapshot = store.load_all(drive)
    chosen = {lane_id: lane for lane_id, lane in snapshot.items() if only is None or lane_id in only}
    prs: dict = {}
    if read_outside:
        heard = {}
        for lane_id, lane in chosen.items():
            try:
                if found := facts.his_messages(lane):
                    heard[lane_id] = found
            except store.Refused as failed:
                errors.append(str(failed))
        if heard:
            store.transact(drive, lambda lanes: [verbs.take_messages(drive, lanes, lane_id, asks, shape["active"]) for lane_id, asks in heard.items()])
        keys = {facts.pr_key(event) for lane in chosen.values() if lane["state"] == store.WAITING for event in (lane["wait"] or {}).get("on", []) if event["kind"] == "pr"}
        try:
            prs = facts.read_prs(keys)
        except store.Refused as failed:
            errors.append(str(failed))
        order = rank.ranked(drive, store.load_all(drive), moment)
        if order.get("error"):
            errors.append(f"rank kept its last order: {order['error']}")
    else:
        order = rank.last(drive)
    leases = {}
    for lane_id, lane in store.load_all(drive).items():
        if lane["state"] == store.RUNNING:
            located = owners.located(lane["owner"])
            leases[lane_id] = (located, owners.lease(located, moment, shape["launch_grace_seconds"]))
    position = {lane_id: index for index, lane_id in enumerate(order.get("order", []))}

    def deciding(lanes: dict[str, dict]) -> list[dict]:
        for lane_id, lane in lanes.items():
            lane["rank"] = {"score": order.get("scores", {}).get(lane_id), "position": position.get(lane_id)}
        starts, lapsed, ready = [], [], []
        held = 0
        for lane_id, lane in lanes.items():
            if lane["state"] == store.RUNNING:
                located, state = leases.get(lane_id, (lane["owner"], "held"))
                if located.get("token") != lane["owner"]["token"]:
                    held += 1
                    continue
                lane["owner"] = located
                if state == "lapsed":
                    lapsed.append(lane_id)
                else:
                    held += 1
            elif lane_id in chosen and (why := fired(drive, lane, prs, moment)):
                lane["wait"] = {**lane["wait"], "fired": lane["wait"].get("fired") or store.stamp(moment), "fired_why": why}
                if not lane.get("parked"):
                    ready.append(lane_id)
        ready.sort(key=lambda lane_id: (position.get(lane_id, len(position)), lanes[lane_id]["wait"]["fired"]))
        for lane_id in [*lapsed, *ready[: max(0, shape["slots"] - held - len(lapsed))]]:
            lane = lanes[lane_id]
            old = lane["owner"] if lane["state"] == store.RUNNING else None
            if old:
                lane["restarts"] = [*lane.get("restarts", []), store.stamp(moment)][-20:]
                if delay := backoff(lane, moment, shape):
                    lane["state"], lane["owner"] = store.WAITING, None
                    lane["wait"] = {"on": [{"kind": "reply"}], "until": store.stamp(moment + delay), "why": f"its owner's lease lapsed {len(lane['restarts'])} times in 30 minutes; next start after {delay}", "set": store.stamp(moment)}
                    store.note(lane, lane["wait"]["why"])
                    starts.append({"lane": lane_id, "token": None, "old": old})
                    continue
            woke = f"its owner's lease lapsed ({old.get('session') or 'never started'}); you take over from the lane file" if old else lane["wait"].get("fired_why", "woken")
            token = store.new_token()
            lane["state"], lane["wait"], lane["owner"] = store.RUNNING, None, {"token": token, "started": store.stamp(moment), "woke": woke}
            store.note(lane, f"owner {token} starts: {woke}")
            starts.append({"lane": lane_id, "token": token, "old": old, "woke": woke})
        return starts

    starts = store.transact(drive, deciding)
    for start in starts:
        if start["old"]:
            owners.stop(start["old"])
        if start["token"] is None:
            continue
        lane = store.load(drive, start["lane"])
        try:
            got = owners.launch(drive, lane, start["token"], start["woke"])
        except store.Refused as failed:
            errors.append(f"{start['lane']}: {failed}")
            store.update(drive, start["lane"], lambda lane, token=start["token"], why=str(failed): failed_start(lane, token, why, shape))
            continue
        store.update(drive, start["lane"], lambda lane, token=start["token"], got=got: lane.__setitem__("owner", {**lane["owner"], **got}) if (lane.get("owner") or {}).get("token") == token else None)
    if read_outside:
        heartbeat(drive, moment, errors)
    return {"started": [start["lane"] for start in starts if start["token"]], "errors": errors}


def failed_start(lane: dict, token: str, why: str, shape: dict) -> None:
    if (lane.get("owner") or {}).get("token") != token:
        return
    moment = store.now()
    lane["restarts"] = [*lane.get("restarts", []), store.stamp(moment)][-20:]
    delay = backoff(lane, moment, shape) or timedelta(minutes=1)
    lane["state"], lane["owner"] = store.WAITING, None
    lane["wait"] = {"on": [{"kind": "reply"}], "until": store.stamp(moment + delay), "why": f"owner start failed, next try after {delay}: {why}", "set": store.stamp(moment)}
    store.note(lane, lane["wait"]["why"])


def check(drive: str) -> tuple[bool, list[str]]:
    """Read the lane files and Claude Code's records: each lane running with a held lease, or waiting with a wake; and a fresh supervisor."""
    shape = store.config(drive)
    moment = store.now()
    lines, good = [], True
    beat = store.drive_dir(drive) / "supervisor.json"
    if not beat.is_file():
        good = False
        lines.append("supervisor: no heartbeat")
    else:
        at = store.parse(json.loads(beat.read_text())["at"])
        stale = (moment - at).total_seconds() > 3 * shape["pass_seconds"] + 60
        good &= not stale
        lines.append(f"supervisor: last pass {store.stamp(at)}{' STALE' if stale else ''}")
    lanes = store.load_all(drive)
    running = sum(1 for lane in lanes.values() if lane["state"] == store.RUNNING)
    for lane_id, lane in lanes.items():
        if why := store.invalid(lane):
            good = False
            lines.append(f"{lane_id}: BROKEN {why}")
        elif lane["state"] == store.RUNNING:
            state = owners.lease(owners.located(lane["owner"]), moment, shape["launch_grace_seconds"])
            good &= state != "lapsed"
            lines.append(f"{lane_id}: running, owner {lane['owner'].get('session') or 'starting'}, lease {state}")
        elif lane.get("parked"):
            lines.append(f"{lane_id}: parked ({lane['parked']}), wakes on his word")
        else:
            wait = lane["wait"]
            events = ", ".join(describe(event) for event in wait.get("on", [])) or "time only"
            late = store.parse(wait["until"]) < moment - timedelta(seconds=3 * shape["pass_seconds"] + 60)
            queued = late and running >= shape["slots"]
            good &= not late or queued
            lines.append(f"{lane_id}: waiting on {events} until {wait['until']}{' (fired, queued for a slot)' if queued else ' OVERDUE' if late else ''}: {wait.get('why')}")
    return good, lines


def describe(event: dict) -> str:
    match event["kind"]:
        case "pr":
            return f"{event['repo']}#{event['number']} {event['fact']}"
        case "cut":
            return f"cut {event['id']}"
    return "his reply"


def code_stamp() -> float:
    return max(path.stat().st_mtime for path in Path(__file__).parent.iterdir() if path.suffix in (".py", ".md"))


def supervise(drive: str) -> None:
    """Pass after pass, each pass_seconds apart or sooner when a verb kicks it; it exits when its own code changes, and launchd starts it again on the new code, while every owner runs on."""
    kick = store.drive_dir(drive) / "kick"
    loaded = code_stamp()
    while code_stamp() == loaded:
        result = run_pass(drive)
        good, lines = check(drive)
        with (store.drive_dir(drive) / "checks.jsonl").open("a") as out:
            out.write(json.dumps({"at": store.stamp(), "good": good, "started": result["started"], "errors": result["errors"], "lines": lines}) + "\n")
        seen = kick.stat().st_mtime if kick.exists() else 0.0
        deadline = time.monotonic() + store.config(drive)["pass_seconds"]
        while time.monotonic() < deadline and (kick.stat().st_mtime if kick.exists() else 0.0) == seen:
            time.sleep(1)


def install(drive: str) -> str:
    """Keep the drive's supervisor running as a launchd agent that launchd restarts whenever it exits, on this process's PATH; the plist's path."""
    label = f"dev.long-running.lane.{drive}"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    log = store.drive_dir(drive) / "supervisor.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LONG_RUNNING_STATE", "XDG_STATE_HOME", "CLAUDE_CONFIG_DIR") if key in os.environ}
    plist.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": [str(owners.BIN), "--drive", drive, "supervise"], "KeepAlive": True, "RunAtLoad": True, "ThrottleInterval": 10, "EnvironmentVariables": env, "StandardOutPath": str(log), "StandardErrorPath": str(log), "ProcessType": "Background"}))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True, check=False)
    subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, check=True)
    return str(plist)
