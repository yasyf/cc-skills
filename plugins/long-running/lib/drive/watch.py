import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from drive import github, state
from drive.render import cut, when
from drive.state import DriveError

POLL_SECONDS = 5
READ_EVERY = timedelta(seconds=60)
FAILURE_EVERY = timedelta(minutes=10)
TICK_EVERY = timedelta(minutes=10)
LINE_LIMIT = 300
OWING_STATES = ("working", "waiting")
LINE_BREAKS = re.compile(r"[\r\n]+")


@dataclass
class Watcher:
    name: str
    offset: int
    ticked_at: datetime
    read_at: datetime | None = None
    failure_said: datetime | None = None


def ids(pr: dict) -> str:
    return f"[{pr['order']} {pr['job']}]"


def pr_changes(pr: dict, read: dict) -> list[str]:
    fresh = github.live(read)
    changes = []
    if fresh["head"] != pr["head"] and pr["head"]:
        changes.append(f"new head {fresh['head'][:7]}")
    if fresh["checks"] != pr["checks"] and fresh["checks"] in ("pass", "fail"):
        changes.append("checks passed" if fresh["checks"] == "pass" else "checks failed")
    if fresh["review"] != pr["review"] and fresh["review"] != "none":
        changes.append("approved" if fresh["review"] == "approved" else "changes requested")
    if fresh["state"] != pr["state"]:
        changes.append(fresh["state"])
    pr.update(fresh, draft=read["isDraft"], merged_sha=read["mergeCommit"])
    return changes


def github_lines(watcher: Watcher, moment: datetime) -> list[str]:
    keys = state.open_pr_keys(state.load(watcher.name))
    if not keys:
        return []
    try:
        reads = github.fetch(keys)
    except github.GithubError as error:
        if watcher.failure_said and moment - watcher.failure_said < FAILURE_EVERY:
            return []
        watcher.failure_said = moment
        return [f"WATCH github read failed: {error}"]
    lines = []
    with state.edit(watcher.name) as data:
        for key, read in reads.items():
            pr = data["prs"][key]
            changes = pr_changes(pr, read)
            pr["updated"] = state.stamp(moment)
            title = cut(pr["title"], 120)
            lines.extend(f'PR {key} "{title}": {change} {ids(pr)}' for change in changes)
    return lines


def event_line(event: dict, prs: dict) -> str:
    if event["kind"] == "tap":
        key = f"{event['repo']}#{event['number']}"
        suffix = f" {ids(prs[key])}" if key in prs else ""
        return f"TAP {event['label']} {key}{suffix}"
    details = " ".join(f"{name}={value}" for name, value in event.items() if name not in ("kind", "at"))
    return f"EVENT {event['kind']} {details}".rstrip()


def event_lines(watcher: Watcher) -> list[str]:
    events = list(state.read_events(watcher.name, watcher.offset))
    if not events:
        return []
    watcher.offset = events[-1][1]
    prs = state.load(watcher.name)["prs"]
    return [event_line(event, prs) for event, _ in events]


def overdue(order: dict, moment: datetime) -> bool:
    if order["state"] in state.CLOSED_ORDER_STATES or not order["due"]:
        return False
    due = state.parse_time(order["due"])
    said = order["due_said"]
    return due <= moment and (said is None or state.parse_time(said) < due)


def due_lines(watcher: Watcher, moment: datetime) -> list[str]:
    if not any(overdue(order, moment) for order in state.load(watcher.name)["orders"].values()):
        return []
    lines = []
    with state.edit(watcher.name) as data:
        for order_id, order in data["orders"].items():
            if overdue(order, moment):
                order["due_said"] = state.stamp(moment)
                lines.append(f'DUE {order_id} "{cut(order["text"], 80)}" was due {when(order["due"])}')
    return lines


def changed_since(order: dict, jobs: dict, last: datetime) -> bool:
    stamps = [order["updated"], *(jobs[job_id]["updated"] for job_id in order["jobs"])]
    return any(state.parse_time(stamp) > last for stamp in stamps)


def due_soon(order: dict, moment: datetime) -> bool:
    return bool(order["due"]) and moment <= state.parse_time(order["due"]) < moment + TICK_EVERY


def owes_line(order: dict, jobs: dict, moment: datetime) -> bool:
    last = state.parse_time(order["last_line"] or order["created"])
    if order["state"] not in OWING_STATES or moment - last < TICK_EVERY:
        return False
    return changed_since(order, jobs, last) or due_soon(order, moment)


def tick_lines(watcher: Watcher, moment: datetime) -> list[str]:
    data = state.load(watcher.name)
    owing = [key for key, order in data["orders"].items() if owes_line(order, data["jobs"], moment)]
    return [f"TICK {' '.join(owing)}"] if owing else []


def run_pass(watcher: Watcher, moment: datetime) -> list[str]:
    """One loop of the watch: GitHub every 60 s, new events, passed due times, a tick every 10 minutes."""
    lines = []
    if watcher.read_at is None or moment - watcher.read_at >= READ_EVERY:
        watcher.read_at = moment
        lines += github_lines(watcher, moment)
    lines += event_lines(watcher)
    lines += due_lines(watcher, moment)
    if moment - watcher.ticked_at >= TICK_EVERY:
        watcher.ticked_at = moment
        lines += tick_lines(watcher, moment)
    return [LINE_BREAKS.sub(" ", line)[:LINE_LIMIT] for line in lines]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def claim(name: str) -> bool:
    path = state.pid_path(name)
    with state.locked(name):
        if path.exists() and (held := path.read_text().strip()).isdigit() and alive(int(held)):
            return False
        path.write_text(f"{os.getpid()}\n")
    return True


def release(name: str) -> None:
    path = state.pid_path(name)
    with state.locked(name):
        if path.exists() and path.read_text().strip() == str(os.getpid()):
            path.unlink()


def main(drive_name: str | None = None) -> int:
    try:
        name = state.resolve(drive_name)
    except DriveError:
        return 0
    if not claim(name):
        return 0
    watcher = Watcher(name, offset=state.events_end(name), ticked_at=state.now())
    try:
        while True:
            for line in run_pass(watcher, state.now()):
                print(line, flush=True)
            time.sleep(POLL_SECONDS)
    finally:
        release(name)
