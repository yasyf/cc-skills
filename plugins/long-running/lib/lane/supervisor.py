"""The supervisor: each pass reads the outside facts the waits name, ranks the lanes, renews nothing a model holds, and starts an owner for every lane whose wait fired or whose lease lapsed, in rank order up to the slots."""

import json
import os
import plistlib
import re
import runpy
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path

from lane import cli as lane_land
from lane import ack, approve, audit, blocks, board, events, facts, incidents, page, queue, rank, say, store, verbs, voice, work
from lane import owner as owners

RESTART_WINDOW = timedelta(minutes=30)
QUIET_BEFORE_NUDGE = timedelta(seconds=60)
NUDGE_EVERY = timedelta(minutes=10)
ALERTS = "alerts.jsonl"
GITHUB = "github.json"
DEFECT_OPEN = timedelta(minutes=30)
UNPRODUCED = "nothing is producing what it waits on"
READERS = 6
STEP_SECONDS = 120
READ_SECONDS = 600
SLOW_STEP_SECONDS = 10
RUNNING_STEPS: dict[str, threading.Thread] = {}
BOOTOUT_SECONDS = 30
BOOTSTRAP_TRIES = 10
PREFLIGHT = "preflight"
PREFLIGHT_SECONDS = 300
REPLAY = timedelta(hours=1)
PREFLIGHTS: dict[str, tuple[subprocess.Popen, float]] = {}
HOME_REPO = "yasyf/cc-skills"
HOME_CI = "long-running CI"
CI_ANCESTORS = 5
KNOWN_RED = "known-red.json"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
FAILED_TEST = re.compile(r"FAILED (plugins/\S+?\.py::\w+)")


def heartbeat(drive: str, moment: datetime, errors: list[str]) -> None:
    path = store.drive_dir(drive) / "supervisor.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = path.with_name(".supervisor.json.tmp")
    fresh.write_text(json.dumps({"pid": os.getpid(), "at": store.stamp(moment), "install": Path(__file__).resolve().parents[2].name, "link": store.launcher(drive).resolve().parents[1].name, "errors": errors}) + "\n")
    fresh.replace(path)


def supervised(drive: str, moment: datetime, shape: dict) -> bool:
    beat = store.drive_dir(drive) / "supervisor.json"
    return beat.is_file() and (moment - store.parse(json.loads(beat.read_text())["at"])).total_seconds() <= 3 * shape["pass_seconds"] + 60


def fired(drive: str, lane: dict, prs: dict, moment: datetime) -> str | None:
    """Why a waiting lane's wait fired, or None: his unanswered ask first, then the outside facts it names, then its time bound."""
    wait = lane["wait"]
    if wait.get("fired"):
        return wait.get("fired_why") or "woken"
    if wait.get("backoff"):
        return f"its backoff ended at {wait['until']}" if store.parse(wait["until"]) <= moment else None
    if store.unreplied(lane):
        return "his new words"
    if why := verbs.unserved(lane):
        return f"{why}, and its wait serves none"
    for event in wait["on"]:
        if event["kind"] != "his_reply" and facts.holds(drive, lane, event, prs):
            return f"{event['kind']} {event.get('ref', '')}".strip()
    if idle := [why for event in wait["on"] if (why := facts.unproduced(drive, lane, event, prs))]:
        return f"{UNPRODUCED}: {'; '.join(idle)}"
    if granted := [resource for resource in queue.asking(drive, lane["id"]) if queue.free_for(drive, resource, lane["id"])]:
        return f"the queue grants {', '.join(granted)} to it now"
    if store.parse(wait["until"]) <= moment:
        return f"its time bound {wait['until']} came"
    return None


def reviewed(drive: str, prs: dict, login: str) -> None:
    """Write his GitHub approving review of a lane PR as his Approve, once per review: at the head it reviewed when that is the PR's head now or the row has no Approve yet, so an older review never replaces a newer tap of his on a card; a waiting lane carrying it wakes."""

    def writing(lanes: dict[str, dict]) -> None:
        for lane in lanes.values():
            for row in lane["prs"]:
                pull = prs.get((row["repo"], row["number"])) or {}
                if not (oid := (pull.get("approvals") or {}).get(login)) or row.get("reviewed_head") == oid:
                    continue
                row["reviewed_head"] = oid
                if oid == pull.get("head") or not row.get("approved_head"):
                    row["approved_head"], row["tapped_head"] = oid, oid
                    row.pop("carried", None)
                    store.note(lane, f"his GitHub Approve on {row['repo']}#{row['number']} at {oid[:12]}")
                    if lane["state"] == store.WAITING:
                        verbs.woken(lane, f"his GitHub Approve on {row['repo']}#{row['number']}")

    store.transact(drive, writing)


def backoff(lane: dict, moment: datetime, shape: dict) -> timedelta | None:
    recent = [stamp for stamp in lane.get("restarts", []) if moment - store.parse(stamp) < RESTART_WINDOW]
    over = len(recent) - shape["restarts_before_backoff"]
    return timedelta(minutes=min(60, 5 * 2**over)) if over >= 0 else None


def github_read(drive: str, whole: bool) -> None:
    """Count the passes in a row whose GitHub read raised or dropped a chunk, which `lane check` shows red; a PR GitHub does not find is no failed read."""
    path = store.drive_dir(drive) / GITHUB
    kept = json.loads(path.read_text()) if path.is_file() else {"failed": 0}
    path.write_text(json.dumps({"failed": 0 if whole else kept["failed"] + 1, "at": store.stamp()}) + "\n")


def alone(drive: str, errors: list[str], what: str, step, seconds: float = STEP_SECONDS):
    """Run one step of a pass on its own, bounded in time: what it returns, or None when it raises anything or runs past seconds, with what failed and why in the pass's errors and in the drive's alerts.jsonl, which `lane check` shows, so no step stops another again, as the Approve card's 403 stopped every pass of 1.0.1691, and no step stalls the drive, as a GitHub read per landed PR stalled 1.0.1711's. A step that ran past its bound keeps running on its own and is not started again until it ends."""
    if (still := RUNNING_STEPS.get(f"{drive}:{what}")) and still.is_alive():
        errors.append(f"{what}: still running from an earlier pass, so this pass moves on without it")
        return None
    kept: dict = {}

    def running() -> None:
        try:
            kept["result"] = step()
        except Exception as failed:
            kept["failed"], kept["trace"] = failed, traceback.format_exc()[-2000:]

    worker = threading.Thread(target=running, name=what, daemon=True)
    began = time.monotonic()
    worker.start()
    worker.join(seconds)
    if (took := time.monotonic() - began) >= SLOW_STEP_SECONDS:
        print(f"{store.stamp()} pass step took {took:.0f}s: {what}", flush=True)
    if worker.is_alive():
        RUNNING_STEPS[f"{drive}:{what}"] = worker
        line, trace = f"{what}: ran past {seconds:.0f}s, so this pass moves on without it", ""
    elif "failed" in kept:
        line, trace = f"{what}: {type(kept['failed']).__name__}: {kept['failed']}", kept["trace"]
    else:
        return kept.get("result")
    errors.append(line)
    with (store.drive_dir(drive) / ALERTS).open("a") as out:
        out.write(json.dumps({"at": store.stamp(), "step": what, "error": line, "trace": trace}) + "\n")
    return None


def run_pass(drive: str, only: set[str] | None = None, read_outside: bool = True) -> dict:
    """One pass; what it started and why, and each read that failed, which never gates a time wake or a lease. A verb's own pass (read_outside false) starts owners only while the drive's supervisor runs, so a drive being staged takes his words and starts nothing until its supervisor's first pass."""
    shape = store.config(drive)
    moment = store.now()
    if not read_outside and not supervised(drive, moment, shape):
        return {"started": [], "errors": ["no supervisor runs on this drive: its first pass starts the owner"]}
    errors: list[str] = []
    snapshot = store.load_all(drive)
    chosen = {lane_id: lane for lane_id, lane in snapshot.items() if only is None or lane_id in only}
    prs: dict = {}
    if read_outside:
        heard = {}
        heartbeat(drive, moment, errors)

        def reading(lane: dict) -> list[dict] | str:
            try:
                return facts.his_messages(lane)
            except store.Refused as failed:
                return str(failed)

        with ThreadPoolExecutor(max_workers=READERS) as pool:
            for lane_id, found in zip(chosen, pool.map(reading, chosen.values()), strict=True):
                if isinstance(found, str):
                    errors.append(found)
                elif found:
                    heard[lane_id] = found
        if heard:
            alone(drive, errors, "his words reached no lane", lambda: store.transact(drive, lambda lanes: [verbs.take_messages(drive, lanes, lane_id, asks, shape["active"]) for lane_id, asks in heard.items()]))
        posts = alone(drive, errors, "the voice listed no posts to follow", lambda: voice.followed(drive, moment)) or []
        lost: list[str] = []
        keys = facts.pr_keys(store.load_all(drive)) | voice.subject_keys(posts)
        read = alone(drive, errors, "GitHub read failed", lambda: facts.read_prs(keys, commits=1, failed=lost), READ_SECONDS)
        prs = read or {}
        errors += [f"GitHub read dropped {why}" for why in lost]
        github_read(drive, read is not None and not lost)
        if prs:
            alone(drive, errors, "his GitHub reviews were not read", lambda: reviewed(drive, prs, facts.his_login(drive)))
        if prs and posts:
            alone(drive, errors, "a post did not follow its subjects", lambda: voice.settle(drive, posts, prs))
        lanes_now = store.load_all(drive)
        monitors = dict(rank.last(drive).get("monitors", {}))
        monitors |= alone(drive, errors, "the last monitor states read stand", lambda: facts.monitor_states(drive, {str(lane["incident"]["monitor"]) for lane in lanes_now.values() if (lane.get("incident") or {}).get("monitor") and not lane["incident"].get("fix_live")})) or {}
        hurt = {lane_id for lane_id, lane in lanes_now.items() if facts.hurting(lane, monitors)}
        if paged := hurt - set(rank.last(drive).get("hurting", [])):
            alone(drive, errors, "an alerting incident lane was not woken", lambda: alerted(drive, paged))
        if prs:
            alone(drive, errors, "the page's view was not kept", lambda: page.keep_view(drive, prs, moment))
        order = alone(drive, errors, "rank kept its last order", lambda: rank.ranked(drive, facts.viewed(lanes_now, prs), moment, hurt, monitors)) or rank.last(drive)
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
                if state == "lapsed" and owners.lease(located, store.now(), shape["launch_grace_seconds"]) == "lapsed":
                    lapsed.append(lane_id)
                else:
                    held += 1
            elif lane["state"] == store.WAITING and lane_id in chosen and (why := fired(drive, lane, prs, moment)):
                lane["wait"] = {**lane["wait"], "fired": lane["wait"].get("fired") or store.stamp(moment), "fired_why": why}
                lane["unproduced"] = lane.get("unproduced", 0) + 1 if why.startswith(UNPRODUCED) else 0
                lane["defect"] = f"its wait twice had nothing producing it: {why}" if lane["unproduced"] >= 2 else None
                ready.append(lane_id)
        ready.sort(key=lambda lane_id: (position.get(lane_id, len(position)), lanes[lane_id]["wait"]["fired"]))
        for lane_id in [*lapsed, *ready[: max(0, shape["slots"] - held - len(lapsed))]]:
            lane = lanes[lane_id]
            old = lane["owner"] if lane["state"] == store.RUNNING else None
            if old:
                lane["restarts"] = [*lane.get("restarts", []), store.stamp(moment)][-20:]
                if delay := backoff(lane, moment, shape):
                    lane["state"], lane["owner"] = store.WAITING, None
                    lane["wait"] = {"on": [{"kind": "his_reply"}], "until": store.stamp(moment + delay), "why": f"its owner's lease lapsed {len(lane['restarts'])} times in 30 minutes; next start after {delay}", "set": store.stamp(moment), "backoff": True}
                    store.note(lane, lane["wait"]["why"])
                    starts.append({"lane": lane_id, "token": None, "old": old})
                    continue
            woke = f"its owner's lease lapsed ({old.get('session') or 'never started'}); you take over from the lane file" if old else lane["wait"].get("fired_why", "woken")
            token = store.new_token()
            lane["state"], lane["wait"], lane["owner"] = store.RUNNING, None, {"token": token, "started": store.stamp(moment), "woke": woke, "asks_hook": True}
            store.note(lane, f"owner {token} starts: {woke}")
            starts.append({"lane": lane_id, "token": token, "old": old, "woke": woke})
        return starts

    starts = store.transact(drive, deciding, leaving_unreplied=True)
    alone(drive, errors, "no owner was nudged", lambda: nudge(drive, store.now()))
    alone(drive, errors, "queue.json kept stale rows", lambda: queue.prune(drive))
    if store.live(drive):
        alone(drive, errors, "no late ask was redelivered", lambda: redeliver(drive, store.now()))
        alone(drive, errors, "no overdue promise was raised", lambda: overdue(drive, store.now()))
        alone(drive, errors, "no block of his was checked for reaching him", lambda: blocks.told(drive, store.now()))
    if read_outside and store.live(drive):
        alone(drive, errors, "his words got no working line", lambda: ack.working(drive))
    with ThreadPoolExecutor(max_workers=READERS) as pool:
        errors += [error for error in pool.map(lambda start: launching(drive, start, shape), starts) if error]
    if read_outside:
        alone(drive, errors, "no lander started", lambda: lane_land.spawn(drive))
        if prs:
            errors += alone(drive, errors, "an Approve card stayed live under rows that settle it", lambda: approve.settle(drive)) or []
            errors += alone(drive, errors, "no Approve card was drawn", lambda: approve.draw(drive)) or []
            alone(drive, errors, "the queue kept no item for what GitHub shows", lambda: work.adds(drive))
        alone(drive, errors, "no item of a gone worker went back to the queue", lambda: work.expire(drive))
        alone(drive, errors, "no worker started for the queue", lambda: work.staff(drive))
        alone(drive, errors, "no audit started", lambda: audit.start(drive))
        alone(drive, errors, "no alert opened an incident lane", lambda: incidents.intake(drive))
        heartbeat(drive, store.now(), errors)
    return {"started": [start["lane"] for start in starts if start["token"]], "errors": errors}


REDELIVER_AFTER = timedelta(minutes=2)


def alerted(drive: str, paged: set[str]) -> None:
    """Wake each incident lane whose monitor began hurting users this pass, so a page starts its fix at once with no one waking it; a running owner gets it as a note."""

    def waking(lanes: dict[str, dict]) -> None:
        for lane_id in paged:
            lane = lanes[lane_id]
            why = f"its incident's monitor {(lane.get('incident') or {}).get('monitor')} is alerting: users are hurt now; start the fix and post in the lane's channel"
            store.note(lane, why)
            if lane["state"] == store.RUNNING:
                with store.note_path(drive, lane_id).open("a") as note:
                    note.write(why + "\n\n")
            else:
                verbs.woken(lane, why)

    store.transact(drive, waking)
    if incidents.away(drive):
        for lane_id in paged:
            if not (store.load(drive, lane_id).get("incident") or {}).get("paged"):
                incidents.page(drive, lane_id, f"its monitor {(store.load(drive, lane_id).get('incident') or {}).get('monitor')} is alerting and he has been away; its owner is on the fix")


def overdue(drive: str, moment: datetime) -> None:
    """Each promise a lane's post stated, past its bound with no newer post in its thread, once: the owner is told it is overdue, woken if it waits, the promised work is queued as an item, and a defect is filed for lane-core, so a promise like "PR by 3:45 PM" never sits unkept and unseen."""
    from lane import audit

    for lane_id, lane in store.load_all(drive).items():
        late = [kept for kept in lane.get("promises", []) if not kept.get("followed") and not kept.get("overdue") and store.parse(kept["due"]) <= moment]
        if not late:
            continue
        stamps = {kept["at"] for kept in late}
        why = "; ".join(f"its post at {kept['at']} promised \"{kept['text'][:160]}\" by {kept['due']}, and nothing newer went out in that thread" for kept in late)

        def marking(lane: dict) -> None:
            for kept in lane.get("promises", []):
                if kept["at"] in stamps:
                    kept["overdue"] = store.stamp(moment)
            store.note(lane, f"overdue: {why}")
            if lane["state"] == store.RUNNING:
                with store.note_path(drive, lane_id).open("a") as note:
                    note.write(f"Overdue: {why}. Keep it or tell him the new time now.\n\n")
            elif lane["state"] == store.WAITING:
                verbs.woken(lane, f"overdue: {why}")

        store.update(drive, lane_id, marking, leaving_unreplied=True)
        for kept in late:
            work.add(drive, "ask", lane_id, f"promise {kept['at']}", f"The lane's post at {kept['at']} promised \"{kept['text'][:300]}\" by {kept['due']}, and nothing newer went out in that thread. Do what it promised, then finish this item with what you did and its link; the lane's owner tells him.", by="supervisor")
        audit.defect(drive, f"lane {lane_id} broke a promise: {why}", lane.get("channel") or lane_id, "a stated bound with nothing tracking it until the overdue check", "lane-core")


def redeliver(drive: str, moment: datetime) -> None:
    """The last-resort cap on his wait: an ask of his still unreplied REDELIVER_AFTER after he sent it is put before its running owner once more as a message that starts its turn, busy or not, once per ask."""
    for lane_id, lane in store.load_all(drive).items():
        late = [ask for ask in store.unreplied(lane) if lane["state"] == store.RUNNING and ask.get("ts") and not ask.get("redelivered") and moment.timestamp() - float(ask["ts"]) >= REDELIVER_AFTER.total_seconds()]
        if not late:
            continue
        stamps = {ask["ts"] for ask in late}

        def marking(lane: dict) -> None:
            for ask in lane["asks"]:
                if ask.get("ts") in stamps:
                    ask["redelivered"] = store.stamp(moment)

        store.update(drive, lane_id, marking, leaving_unreplied=True)
        owners.deliver(lane["owner"], "".join(f"His words to lane {lane_id}, sent {int((moment.timestamp() - float(ask['ts'])) // 60)} minutes ago and not answered yet{', ' + ask['link'] if ask.get('link') else ''}; answer them with `lane say --re {ask['ts']}` before anything else:\n{ask['words']}\n\n" for ask in late))


def nudge(drive: str, moment: datetime) -> None:
    """Put his unanswered words and a note set while it runs before each running owner that has sat between turns for QUIET_BEFORE_NUDGE, at most once a NUDGE_EVERY each, as a message that starts its turn: its hooks show them only at a tool call, which an owner waiting on its builders is not making."""

    def nudging(lane: dict) -> str | None:
        owner = lane["owner"]
        waiting = store.unreplied(lane)
        note = store.note_path(drive, lane["id"])
        if not (waiting or note.is_file() and note.read_text().strip()):
            return None
        if not (quiet := owners.own_quiet_since(owner)) or moment - quiet < QUIET_BEFORE_NUDGE:
            return None
        if (last := owner.get("nudged")) and moment - store.parse(last) < NUDGE_EVERY:
            return None
        owner["nudged"] = store.stamp(moment)
        said = "".join(f"His words to lane {lane['id']} at {ask['at']}{', ' + ask['link'] if ask.get('link') else ''}, not answered yet; answer them with `lane say` before anything else:\n{ask['words']}\n\n" for ask in waiting)
        return said + (f"A note for lane {lane['id']}, set while you run; any words of his in it are owed an answer with `lane say`:\n{note.read_text()}" if note.is_file() else "")

    for lane_id, lane in store.load_all(drive).items():
        if lane["state"] != store.RUNNING or not nudging(dict(lane, owner=dict(lane["owner"]))):
            continue
        if text := store.update(drive, lane_id, lambda lane: nudging(lane) if lane["state"] == store.RUNNING else None, leaving_unreplied=True):
            owners.deliver(store.load(drive, lane_id)["owner"], text)


def launching(drive: str, start: dict, shape: dict) -> str | None:
    """Start one owner the pass chose, beside the others it chose; why it failed, or None."""
    if start["old"]:
        owners.stop(start["old"])
    if start["token"] is None:
        return None
    lane = store.load(drive, start["lane"])
    try:
        got = owners.launch(drive, lane, start["token"], start["woke"])
    except store.Refused as failed:
        store.update(drive, start["lane"], lambda lane, token=start["token"], why=str(failed): failed_start(lane, token, why, shape), leaving_unreplied=True)
        return f"{start['lane']}: {failed}"
    store.update(drive, start["lane"], lambda lane, token=start["token"], got=got: lane.__setitem__("owner", {**lane["owner"], **got}) if (lane.get("owner") or {}).get("token") == token else None)
    return None


def failed_start(lane: dict, token: str, why: str, shape: dict) -> None:
    if (lane.get("owner") or {}).get("token") != token:
        return
    moment = store.now()
    lane["restarts"] = [*lane.get("restarts", []), store.stamp(moment)][-20:]
    delay = backoff(lane, moment, shape) or timedelta(minutes=1)
    lane["state"], lane["owner"] = store.WAITING, None
    lane["wait"] = {"on": [{"kind": "his_reply"}], "until": store.stamp(moment + delay), "why": f"owner start failed, next try after {delay}: {why}", "set": store.stamp(moment), "backoff": True}
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
    if len(store.active(lanes)) > shape["active"]:
        lines.append(f"queue: {len(store.active(lanes))} lanes active, over the cap of {shape['active']}; the lowest ranked is {next((lane_id for lane_id in reversed(rank.last(drive).get('order', [])) if lane_id in store.active(lanes)), '?')}, for him to park")
    running = sum(1 for lane in lanes.values() if lane["state"] == store.RUNNING)
    for lane_id, lane in lanes.items():
        if why := store.invalid(lane):
            good = False
            lines.append(f"{lane_id}: BROKEN {why}")
        elif lane["state"] == store.RUNNING:
            state = owners.lease(owners.located(lane["owner"]), moment, shape["launch_grace_seconds"])
            good &= state != "lapsed"
            lines.append(f"{lane_id}: running, owner {lane['owner'].get('session') or 'starting'}, lease {state}")
        elif lane["state"] == store.PARKED:
            lines.append(f"{lane_id}: parked ({lane['parked']['words']}), wakes on his word")
        else:
            wait = lane["wait"]
            events = ", ".join(describe(event) for event in wait["on"])
            late = store.parse(wait["until"]) < moment - timedelta(seconds=3 * shape["pass_seconds"] + 60)
            queued = late and running >= shape["slots"]
            good &= not late or queued
            lines.append(f"{lane_id}: waiting on {events} until {wait['until']}{' (fired, queued for a slot)' if queued else ' OVERDUE' if late else ''}: {wait.get('why')}")
        if lane.get("defect"):
            good = False
            lines.append(f"{lane_id}: DEFECT {lane['defect']}")
        blocked = [entry for entry in board.entries(drive, lane_id) if entry["words"].startswith("blocked, from ")]
        if blocked and moment - store.parse(blocked[-1]["at"]) < timedelta(hours=1):
            lines.append(f"{lane_id}: BLOCKED {blocked[-1]['words'].removeprefix('blocked, from ')}")
    for found in audit.open_defects(drive):
        age = moment - store.parse(found["at"])
        late = age > DEFECT_OPEN
        good &= not late
        lines.append(f"defect {found['id']} for {found['owner']}, open {int(age.total_seconds() // 60)}m{' OVERDUE' if late else ''}: {found['what']}")
    read = store.drive_dir(drive) / GITHUB
    if read.is_file() and (failed := json.loads(read.read_text())["failed"]):
        good = False
        lines.append(f"GitHub read failed {failed} pass{'es' * (failed != 1)} in a row")
    alerts = store.drive_dir(drive) / ALERTS
    recent = [row for row in (json.loads(line) for line in alerts.read_text().splitlines()[-500:]) if moment - store.parse(row["at"]) < timedelta(hours=1)] if alerts.is_file() else []
    checked = store.drive_dir(drive) / "checks.jsonl"
    last = json.loads(checked.read_text().splitlines()[-1]) if checked.is_file() and checked.stat().st_size else {"errors": []}
    if last["errors"]:
        good = False
        lines += [f"last pass error: {error[:300]}" for error in last["errors"]]
    for step, count in sorted({row["step"]: sum(1 for kept in recent if kept["step"] == row["step"]) for row in recent}.items()):
        lines.append(f"pass step failed {count}x in the last hour: {step}")
    return good, lines


def preflighted(drive: str, install: Path) -> bool:
    """Whether the install rendered every card and post it would send now through the voice in dry run with none refused or held. While the install would bring a red test the running one does not carry, not marked known-red, no preflight starts, so it is not taken, unless `lane take --p0` named it a fix for live harm. The first time a pass sees the install with main green, its preflight starts beside the supervisor, which keeps passing on its own code; a later pass reads the verdict. The supervisor and every owner's `lane` move to an install only once it passed, so a refusal keeps the drive on the code it runs."""
    folder = store.drive_dir(drive) / PREFLIGHT
    folder.mkdir(exist_ok=True)
    passed, refused, output = folder / f"{install.name}.passed", folder / f"{install.name}.refused", folder / f"{install.name}.out"
    if passed.exists() or refused.exists():
        return passed.exists()
    running = PREFLIGHTS.get(install.name)
    if running is None:
        if not store.parses(drive, install / "bin" / "lane"):
            refused.write_text(f"{install.name}'s lane command does not parse: {(store.drive_dir(drive) / 'parses' / install.name).read_text()}")
            return False
        if not (folder / f"{install.name}.p0").exists() and (red := regressed(drive, install)):
            output.write_text(f"waiting: {red}; mark one known-red with `lane --drive {drive} known-red <test> --lane <owner>`, or take a fix for live harm is taken anyway with `lane --drive {drive} take {install.name} --p0 \"<why>\"`\n")
            return False
        with output.open("w") as out:
            PREFLIGHTS[install.name] = (subprocess.Popen([str(install / "bin" / "lane"), "--drive", drive, "preflight"], stdout=out, stderr=subprocess.STDOUT), time.monotonic())
        return False
    process, started = running
    if process.poll() is None and time.monotonic() - started < PREFLIGHT_SECONDS:
        return False
    if process.poll() is None:
        process.kill()
        process.wait()
    verdict = passed if process.returncode == 0 else refused
    verdict.write_text(output.read_text() if process.returncode >= 0 else f"preflight ran past {PREFLIGHT_SECONDS}s\n" + output.read_text())
    del PREFLIGHTS[install.name]
    if verdict is refused:
        with (store.drive_dir(drive) / ALERTS).open("a") as out:
            out.write(json.dumps({"at": store.stamp(), "step": f"install {install.name} refused by its preflight", "error": refused.read_text().strip()[-2000:]}) + "\n")
    return verdict is passed


def copied(source: str, target: str) -> None:
    """Copy one file of the live drive, which a pass may replace while the copy runs: a file renamed away mid-copy is left out, as the pass that replaced it would have."""
    with suppress(FileNotFoundError):
        shutil.copy2(source, target)


def take(drive: str, version: str, why: str) -> str:
    """Let the supervisor take one install that brings new red tests, for a fix of live harm: its preflight still runs and still refuses what it would refuse."""
    if not (Path(__file__).resolve().parents[3] / version / "bin" / "lane").exists():
        raise store.Refused(f"no install {version} beside this one")
    folder = store.drive_dir(drive) / PREFLIGHT
    folder.mkdir(exist_ok=True)
    (folder / f"{version}.p0").write_text(json.dumps({"at": store.stamp(), "why": why}) + "\n")
    (folder / f"{version}.out").unlink(missing_ok=True)
    (store.drive_dir(drive) / "kick").touch()
    return f"{version} is taken past its new red tests for: {why}"


def regressed(drive: str, install: Path) -> str | None:
    """Why the install would bring a red test the running one does not carry, or None when it brings none: its CI's failing tests, at the commit its tag was cut from, must be failing at the running install's commit too or be marked known-red with an owning lane, so a main that lanes keep red with known failures never holds a fix back. A run still going is a wait."""
    here = Path(__file__).resolve().parents[2]
    candidate, running = failures(drive, install.name), failures(drive, here.name)
    if candidate is None or running is None:
        return f"{HOME_CI} has not finished at {install.name}'s or {here.name}'s commit"
    known = known_red(drive)
    if new := sorted(candidate - running - set(known)):
        return f"{install.name} fails {len(new)} test{'s' * (len(new) != 1)} {here.name} passes, none marked known-red: {', '.join(new[:8])}"
    return None


def failures(drive: str, version: str) -> set[str] | None:
    """The failing tests and checks of the plugin's CI at the commit the version's tag was cut from, the nearest one CI ran on below its autobump commit; None while that run is still going. A finished read is kept, since a commit's run never changes."""
    folder = store.drive_dir(drive) / PREFLIGHT
    kept = folder / f"ci-{version}.json"
    if kept.is_file():
        return set(json.loads(kept.read_text()))
    sha = gh_json("api", f"repos/{HOME_REPO}/commits/long-running--v{version}")
    for _ in range(CI_ANCESTORS):
        if runs := [run for run in gh_json("run", "list", "-R", HOME_REPO, "-w", HOME_CI, "--commit", sha["sha"], "--json", "databaseId,status,conclusion") if run["conclusion"] != "cancelled"]:
            break
        sha = gh_json("api", f"repos/{HOME_REPO}/commits/{sha['parents'][0]['sha']}")
    else:
        raise store.Refused(f"{HOME_CI} ran on none of the {CI_ANCESTORS} commits below long-running--v{version}")
    if runs[0]["status"] != "completed":
        return None
    failed = set()
    if runs[0]["conclusion"] != "success":
        for job in gh_json("api", f"repos/{HOME_REPO}/actions/runs/{runs[0]['databaseId']}/jobs")["jobs"]:
            if job["conclusion"] != "failure":
                continue
            log = subprocess.run(["gh", "api", "--allow-escape-sequences", f"repos/{HOME_REPO}/actions/jobs/{job['id']}/logs"], capture_output=True, text=True, check=True, timeout=120).stdout
            tests = set(FAILED_TEST.findall(ANSI.sub("", log)))
            failed |= tests or {job["name"]}
    kept.write_text(json.dumps(sorted(failed)))
    return failed


def gh_json(*args: str):
    return json.loads(subprocess.run(["gh", *args], capture_output=True, text=True, check=True, timeout=60).stdout)


def known_red(drive: str) -> dict[str, str]:
    """The tests marked known-red, each with the lane that owns its fix."""
    path = store.drive_dir(drive) / PREFLIGHT / KNOWN_RED
    return json.loads(path.read_text()) if path.is_file() else {}


def mark_red(drive: str, test: str, lane: str | None) -> str:
    """Mark a test known-red with the lane that owns its fix, or clear it with no lane."""
    path = store.drive_dir(drive) / PREFLIGHT / KNOWN_RED
    path.parent.mkdir(exist_ok=True)
    known = known_red(drive)
    if lane:
        known[test] = lane
    else:
        known.pop(test)
    path.write_text(json.dumps(known, indent=1) + "\n")
    return f"{test}: {'known-red, owned by ' + lane if lane else 'cleared'}"


def preflight(drive: str) -> tuple[bool, list[str]]:
    """Render, on a copy of the drive's lanes, each lane's Approve cards, each settle edit and ruling of the posts it follows and the last hour of owner posts, through cc-slack in dry run: good only when the voice took every one, with each refusal or hold named."""
    source = store.drive_dir(drive)
    kept = {key: os.environ.get(key) for key in ("LONG_RUNNING_STATE", voice.DRY_RUN)}
    with tempfile.TemporaryDirectory(prefix="lane-preflight-") as scratch:
        shutil.copytree(source, Path(scratch) / "lanes" / drive, symlinks=True, ignore=shutil.ignore_patterns("*.log", "*.jsonl", PREFLIGHT), copy_function=copied)
        os.environ["LONG_RUNNING_STATE"], os.environ[voice.DRY_RUN] = scratch, "1"
        try:
            failed = rendered(drive)
        finally:
            for key, value in kept.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    bad = [*failed, *(f"{each['lane']} {each['kind']}: {each.get('verdict')}: {each.get('reason') or each.get('warning') or ''} | {each['text'][:200]}" for each in voice.RENDERED if each.get("verdict") != "ok")]
    return not bad, [f"preflight: {len(voice.RENDERED)} rendered, {len(bad)} refused or held", *bad]


def rendered(drive: str) -> list[str]:
    lanes = store.load_all(drive)
    posts = voice.followed(drive, store.now())
    if posts:
        voice.settle(drive, posts, facts.read_prs(voice.subject_keys(posts), commits=1))
    return [*carded(drive, lanes), *replayed(drive, lanes)]


def carded(drive: str, lanes: dict[str, dict]) -> list[str]:
    """Each lane's Approve cards for every ready stack, laid out by cc-slack in dry run as the draw would post them: why each lane's cards were refused."""
    from drive import slack

    seen = page.view(drive)["prs"]
    refused = []
    for lane_id, lane in lanes.items():
        if (lane.get("channel") or "").startswith(say.DIRECT) or not (ready := approve.ready_stacks(lane, seen)):
            continue
        argv = ["dm-status", "--dry-run", "--agent", "claude", "--drive", drive, "--as-holder", "--channel", lane["channel"], *(["--thread", lane["thread"]] if lane.get("thread") else [])]
        argv += [flag for heads in ready for key in heads for flag in ("--tap-pr", approve.pr_url(key))]
        try:
            laid = slack.cc_slack(*argv, timeout=approve.DRAW_SECONDS)
        except slack.SlackError as failed:
            refused.append(f"{lane_id} cards: refused: {failed}")
            continue
        voice.RENDERED.append({"lane": lane_id, "kind": "cards", "text": " ".join(card["text"][:80] for card in laid.get("cards") or []), **laid})
    return refused


def replayed(drive: str, lanes: dict[str, dict]) -> list[str]:
    """Each owner post the voice sent in the last REPLAY window, sent again in dry run as an edit of itself in its owner's own words, so the new code draws it anew: what the new code refuses or holds that went out, and what Jev scores worse than when it went out."""
    since = store.now() - REPLAY
    worse = []
    for sent in voice.sent_since(since):
        if sent["lane"] not in lanes:
            continue
        lane = lanes[sent["lane"]]
        try:
            voice.send(drive, lane, sent["words"], sent.get("thread_ts") or None, sent.get("options") or None, edit=sent["ts"])
        except store.Refused as refused:
            worse.append(f"{sent['lane']} post {sent['permalink']}: went out, now refused: {refused}")
            continue
        now = voice.RENDERED[-1]
        if now.get("verdict") == "ok" and "score" in sent and now.get("score", sent["score"]) < sent["score"]:
            worse.append(f"{sent['lane']} post {sent['permalink']}: Jev scores it {now['score']}, worse than {sent['score']} when it went out")
    return worse


def settled(drive: str, seconds: int) -> tuple[bool, list[str]]:
    """Wait for the supervisor's preflight of this install, failing on its refusal, then read the supervisor for seconds: crash-looping when its log gains two tracebacks or no pass finishes, otherwise `lane check` and the last pass's errors at the end."""
    folder = store.drive_dir(drive)
    install = Path(__file__).resolve().parents[2]
    verdict = folder / PREFLIGHT / f"{install.name}.passed", folder / PREFLIGHT / f"{install.name}.refused"
    deadline = time.monotonic() + PREFLIGHT_SECONDS + 3 * store.config(drive)["pass_seconds"]
    while time.monotonic() < deadline and not any(path.exists() for path in verdict):
        time.sleep(2)
    if verdict[1].exists():
        return False, [f"supervisor stays on its code: {install.name}'s preflight refused it", *verdict[1].read_text().splitlines()]
    if not verdict[0].exists():
        waiting = folder / PREFLIGHT / f"{install.name}.out"
        return False, [f"supervisor has not taken {install.name} after {PREFLIGHT_SECONDS}s", *(waiting.read_text().splitlines() if waiting.is_file() else ["its preflight never started"])]
    log, checks = folder / "supervisor.log", folder / "checks.jsonl"
    logged, passes = log.stat().st_size, checks.stat().st_size
    time.sleep(seconds)
    with log.open("rb") as read:
        read.seek(logged)
        fresh = read.read().decode(errors="replace")
    with checks.open("rb") as read:
        read.seek(passes)
        finished = [json.loads(line) for line in read.read().decode().splitlines()]
    crashes = fresh.count("Traceback (most recent call last)")
    if crashes >= 2 or not finished:
        last = next((line for line in reversed(fresh.splitlines()) if line and not line.startswith(" ")), "no output")
        return False, [f"supervisor CRASH-LOOPS: {crashes} tracebacks and {len(finished)} finished passes in {seconds}s after the install, the last line {last}"]
    good, lines = check(drive)
    errors = finished[-1]["errors"]
    return good and not errors, [f"supervisor: {len(finished)} passes in {seconds}s after the install, {crashes} tracebacks", *(f"pass error: {error}" for error in errors), *lines]


def describe(event: dict) -> str:
    return "his reply" if event["kind"] == "his_reply" else f"{event['kind']} {event['ref']}"


def code_stamp() -> float:
    return max(path.stat().st_mtime for path in Path(__file__).parent.iterdir() if path.suffix in (".py", ".md"))


def newest_install() -> Path:
    """The newest installed copy of this plugin, as its hooks resolve it, or this copy when it runs from a checkout."""
    return Path(runpy.run_path(str(Path(__file__).resolve().parents[2] / "hooks" / "installed.py"))["root"]()).resolve()


def supervise(drive: str) -> None:
    """Pass after pass, each pass_seconds apart or sooner when a verb kicks it. It exits when its own code changes or a newer install lands, pointing the drive's launcher at that install first, and launchd starts it again on the new code through the launcher, while every owner runs on."""
    kick = store.drive_dir(drive) / "kick"
    here = Path(__file__).resolve().parents[2]
    store.launcher(drive)
    loaded = code_stamp()
    if gh := store.config(drive).get("gh"):
        os.environ[facts.GH_ENV] = gh
    heartbeat(drive, store.now(), [])
    delivered_at = -1
    if store.live(drive):
        for line in alone(drive, [], "GitHub is not delivering to this Mac", lambda: events.receiving(drive)) or []:
            print(line, flush=True)
        try:
            for line in dashboard(drive):
                print(line, flush=True)
        except store.Refused as failed:
            print(f"his dashboard was not reloaded: {failed}", flush=True)
    while code_stamp() == loaded:
        run_pass(drive, read_outside=False)
        if (newest := newest_install()) != here and preflighted(drive, newest):
            link = store.drive_dir(drive) / "lane"
            fresh = link.with_name(f".lane.{os.getpid()}")
            fresh.unlink(missing_ok=True)
            fresh.symlink_to(newest / "bin" / "lane")
            fresh.replace(link)
            return
        result = run_pass(drive)
        good, lines = check(drive)
        with (store.drive_dir(drive) / "checks.jsonl").open("a") as out:
            out.write(json.dumps({"at": store.stamp(), "good": good, "started": result["started"], "errors": result["errors"], "lines": lines}) + "\n")
        seen = kick.stat().st_mtime if kick.exists() else 0.0
        deadline = time.monotonic() + store.config(drive)["pass_seconds"]
        delivered = events.deliveries_path()
        while time.monotonic() < deadline and (kick.stat().st_mtime if kick.exists() else 0.0) == seen:
            if delivered.is_file() and delivered.stat().st_size != delivered_at:
                delivered_at = delivered.stat().st_size
                events.handle_github(drive)
            time.sleep(1)


DASHBOARD_LABEL = "com.yasyf.long-running.dashboard."


def dashboard(drive: str) -> list[str]:
    """Keep his dashboard's launch agent on the lane drive with no hand launchctl: its log beside the lanes, reloaded when that changes its plist or a newer install than the one it last loaded on has landed, and each old drive's dashboard, one whose drive no live lane drive holds, booted out with its plist set aside so login loads it no more. What it did."""
    agents = Path.home() / "Library" / "LaunchAgents"
    done = []
    for plist in sorted(agents.glob(f"{DASHBOARD_LABEL}*.plist")):
        name = plist.stem.removeprefix(DASHBOARD_LABEL)
        if not store.live(name):
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{plist.stem}"], capture_output=True, check=False)
            plist.replace(plist.with_name(f"{plist.name}.archived"))
            done.append(f"booted out {plist.stem}")
    plist = agents / f"{DASHBOARD_LABEL}{drive}.plist"
    if not plist.is_file():
        return done
    shape = plistlib.loads(plist.read_bytes())
    log = str(store.drive_dir(drive) / "dashboard.log")
    stamp = store.drive_dir(drive) / "dashboard.install"
    newest = str(newest_install())
    if shape.get("StandardOutPath") == shape.get("StandardErrorPath") == log and stamp.is_file() and stamp.read_text().strip() == newest:
        return done
    shape["StandardOutPath"] = shape["StandardErrorPath"] = log
    plist.write_bytes(plistlib.dumps(shape))
    loaded(plist.stem, plist)
    stamp.write_text(newest + "\n")
    return [*done, f"reloaded {plist.stem} on {newest}, logging to {log}"]


def install(drive: str) -> str:
    """Keep the drive's supervisor running as a launchd agent that launchd restarts whenever it exits, on this process's PATH; the plist's path."""
    label = f"dev.long-running.lane.{drive}"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    log = store.drive_dir(drive) / "supervisor.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LONG_RUNNING_STATE", "XDG_STATE_HOME", "CLAUDE_CONFIG_DIR") if key in os.environ}
    plist.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": [str(store.launcher(drive)), "--drive", drive, "supervise"], "KeepAlive": True, "RunAtLoad": True, "ThrottleInterval": 10, "EnvironmentVariables": env, "StandardOutPath": str(log), "StandardErrorPath": str(log), "ProcessType": "Background"}))
    loaded(label, plist)
    for line in dashboard(drive):
        print(line)
    return str(plist)


def loaded(label: str, plist: Path) -> None:
    """Boot the agent out, wait until launchd has let it go, and bootstrap the plist, retrying on exit 5, launchd's refusal while the old job is still exiting."""
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True, check=False)
    deadline = time.monotonic() + BOOTOUT_SECONDS
    while time.monotonic() < deadline and subprocess.run(["launchctl", "print", f"{domain}/{label}"], capture_output=True, check=False).returncode == 0:
        time.sleep(0.5)
    for _ in range(BOOTSTRAP_TRIES):
        if (tried := subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True, check=False)).returncode != 5:
            break
        time.sleep(1)
    if tried.returncode:
        raise store.Refused(f"launchctl bootstrap {label} exited {tried.returncode}: {tried.stderr.strip()}")
