"""The supervisor: each pass reads the outside facts the waits name, ranks the lanes, renews nothing a model holds, and starts an owner for every lane whose wait fired or whose lease lapsed, in rank order up to the slots."""

import json
import os
import plistlib
import runpy
import subprocess
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from lane import cli as lane_land
from lane import ack, approve, audit, board, fixes, facts, page, queue, rank, store, verbs, voice
from lane import owner as owners

RESTART_WINDOW = timedelta(minutes=30)
QUIET_BEFORE_NUDGE = timedelta(seconds=60)
NUDGE_EVERY = timedelta(minutes=10)
ALERTS = "alerts.jsonl"
GITHUB = "github.json"
DEFECT_OPEN = timedelta(minutes=30)
UNPRODUCED = "nothing is producing what it waits on"
READERS = 6
BOOTOUT_SECONDS = 30
BOOTSTRAP_TRIES = 10


def heartbeat(drive: str, moment: datetime, errors: list[str]) -> None:
    path = store.drive_dir(drive) / "supervisor.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = path.with_name(".supervisor.json.tmp")
    fresh.write_text(json.dumps({"pid": os.getpid(), "at": store.stamp(moment), "errors": errors}) + "\n")
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
                    row["approved_head"] = oid
                    store.note(lane, f"his GitHub Approve on {row['repo']}#{row['number']} at {oid[:12]}")
                    if lane["state"] == store.WAITING:
                        verbs.woken(lane, f"his GitHub Approve on {row['repo']}#{row['number']}")

    store.transact(drive, writing)


def backoff(lane: dict, moment: datetime, shape: dict) -> timedelta | None:
    recent = [stamp for stamp in lane.get("restarts", []) if moment - store.parse(stamp) < RESTART_WINDOW]
    over = len(recent) - shape["restarts_before_backoff"]
    return timedelta(minutes=min(60, 5 * 2**over)) if over >= 0 else None


def github_read(drive: str, whole: bool) -> None:
    """Count the passes in a row whose GitHub read failed or came back short, which `lane check` shows red."""
    path = store.drive_dir(drive) / GITHUB
    kept = json.loads(path.read_text()) if path.is_file() else {"failed": 0}
    path.write_text(json.dumps({"failed": 0 if whole else kept["failed"] + 1, "at": store.stamp()}) + "\n")


def alone(drive: str, errors: list[str], what: str, step):
    """Run one step of a pass on its own: what it returns, or, when it raises anything, None, with what failed and why in the pass's errors and in the drive's alerts.jsonl, which `lane check` shows, so no step stops another again, as the Approve card's 403 stopped every pass of 1.0.1691."""
    try:
        return step()
    except Exception as failed:
        line = f"{what}: {type(failed).__name__}: {failed}"
        errors.append(line)
        with (store.drive_dir(drive) / ALERTS).open("a") as out:
            out.write(json.dumps({"at": store.stamp(), "step": what, "error": line, "trace": traceback.format_exc()[-2000:]}) + "\n")
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
        prs = alone(drive, errors, "GitHub read failed", lambda: facts.read_prs(keys, commits=1, failed=lost)) or {}
        errors += [f"GitHub read dropped {why}" for why in lost]
        github_read(drive, not lost and (bool(prs) or not keys))
        if prs:
            alone(drive, errors, "his GitHub reviews were not read", lambda: reviewed(drive, prs, facts.his_login(drive)))
        if prs and posts:
            alone(drive, errors, "a post did not follow its subjects", lambda: voice.settle(drive, posts, prs))
        lanes_now = store.load_all(drive)
        monitors = dict(rank.last(drive).get("monitors", {}))
        monitors |= alone(drive, errors, "the last monitor states read stand", lambda: facts.monitor_states(drive, {str(lane["incident"]["monitor"]) for lane in lanes_now.values() if (lane.get("incident") or {}).get("monitor") and not lane["incident"].get("fix_live")})) or {}
        hurt = {lane_id for lane_id, lane in lanes_now.items() if facts.hurting(lane, monitors)}
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
    if read_outside and store.live(drive):
        alone(drive, errors, "his words got no working line", lambda: ack.working(drive))
    with ThreadPoolExecutor(max_workers=READERS) as pool:
        errors += [error for error in pool.map(lambda start: launching(drive, start, shape), starts) if error]
    if read_outside:
        alone(drive, errors, "no lander started", lambda: lane_land.spawn(drive))
        if prs:
            errors += alone(drive, errors, "no Approve card was drawn", lambda: approve.draw(drive)) or []
            errors += [line for line in alone(drive, errors, "no fix job started", lambda: fixes.start(drive)) or [] if ": no fix job for " in line]
        alone(drive, errors, "no audit started", lambda: audit.start(drive))
        heartbeat(drive, store.now(), errors)
    return {"started": [start["lane"] for start in starts if start["token"]], "errors": errors}


def nudge(drive: str, moment: datetime) -> None:
    """Put his unanswered words and a note set while it runs before each running owner that has sat between turns for QUIET_BEFORE_NUDGE, at most once a NUDGE_EVERY each, as a message that starts its turn: its hooks show them only at a tool call, which an owner waiting on its builders is not making."""

    def nudging(lane: dict) -> str | None:
        owner = lane["owner"]
        waiting = store.unreplied(lane)
        note = store.note_path(drive, lane["id"])
        if not (waiting or note.is_file() and note.read_text().strip()):
            return None
        if not (quiet := owners.quiet_since(owner)) or moment - quiet < QUIET_BEFORE_NUDGE:
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
    for row in [json.loads(line) for line in alerts.read_text().splitlines()[-20:]] if alerts.is_file() else []:
        if moment - store.parse(row["at"]) < timedelta(minutes=15):
            good = False
            lines.append(f"pass step failed {row['at']}: {row['error']}")
    return good, lines


def settled(drive: str, seconds: int) -> tuple[bool, list[str]]:
    """Read the supervisor for seconds after an install: crash-looping when its log gains two tracebacks or no pass finishes, otherwise `lane check` and the last pass's errors at the end."""
    folder = store.drive_dir(drive)
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
    if store.live(drive):
        try:
            for line in dashboard(drive):
                print(line, flush=True)
        except store.Refused as failed:
            print(f"his dashboard was not reloaded: {failed}", flush=True)
    while code_stamp() == loaded:
        run_pass(drive, read_outside=False)
        if (newest := newest_install()) != here:
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
        while time.monotonic() < deadline and (kick.stat().st_mtime if kick.exists() else 0.0) == seen:
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
