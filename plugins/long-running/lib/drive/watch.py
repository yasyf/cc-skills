import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from drive import github, orca, page, reads, state, view
from drive.render import cut, when
from drive.state import DriveError

POLL_SECONDS = 5
READ_EVERY = timedelta(seconds=60)
FAILURES_BEFORE_SAYING = 3
FAILURE_EVERY = timedelta(minutes=10)
TICK_EVERY = timedelta(minutes=30)
LINE_LIMIT = 300
OWING_STATES = ("working", "waiting")
LIVE_JOB_STATES = ("running", "waiting")
LINE_BREAKS = re.compile(r"[\r\n]+")


@dataclass
class Watcher:
    name: str
    offset: int
    ticked_at: datetime
    read_at: datetime | None = None
    failures: dict[str, int] = field(default_factory=dict)
    failure_said: dict[str, datetime] = field(default_factory=dict)
    page_shape: str | None = None


def read_failed(watcher: Watcher, source: str, error: Exception, moment: datetime) -> list[str]:
    """Retry a failed read on the next poll; say so once it fails three times running, then at most every ten minutes."""
    failures = watcher.failures[source] = watcher.failures.get(source, 0) + 1
    if failures < FAILURES_BEFORE_SAYING:
        watcher.read_at = None
        return []
    said = watcher.failure_said.get(source)
    if said and moment - said < FAILURE_EVERY:
        return []
    watcher.failure_said[source] = moment
    return [f"WATCH {source} read failed {failures} times in a row: {error}"]


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
        return read_failed(watcher, "github", error, moment)
    watcher.failures.pop("github", None)
    lines = []
    with state.edit(watcher.name) as data:
        for key, read in reads.items():
            pr = data["prs"][key]
            changes = pr_changes(pr, read)
            pr["updated"] = state.stamp(moment)
            title = cut(pr["title"], 120)
            lines.extend(f'PR {key} "{title}": {change} {ids(pr)}' for change in changes)
    return lines


def first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def mail_line(job_id: str, job: dict, message: dict, moment: datetime) -> str | None:
    """Record one message from a remote lane on its job; the event line it wakes the coordinator with, if any."""
    kind = message["type"]
    if kind == "heartbeat":
        return None
    body = message["body"] or ""
    job["updated"] = state.stamp(moment)
    if kind == "escalation":
        last = first_line(body.split("Last output:")[-1])
        return f"JOB {job_id} {job['order']} STOPPED without a report, last output: {cut(last, 160)}"
    job["said"] = first_line(body) or message["subject"]
    if kind == "status":
        return None
    job["report"] = body
    if kind == "question":
        job["ask"] = message["id"]
        job["state"] = "waiting"
    return f"JOB {job_id} {job['order']} {job['said']}"


def lane_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each live remote lane's mail, read from its own Run: reports become JOB lines, read questions are answered here."""
    data = state.load(watcher.name)
    lanes: dict[str, dict[str, str]] = {}
    for job_id, job in data["jobs"].items():
        if job.get("where") == "remote" and job.get("dispatch") and job["state"] in LIVE_JOB_STATES:
            lanes.setdefault(job["run"], {})[f"dispatch:{job['dispatch']}"] = job_id
    lines, messages = [], []
    for run, handles in lanes.items():
        source = f"lane mail of {run}"
        try:
            messages += [(run, handles, message) for message in orca.mail(run)]
        except orca.OrcaError as error:
            lines += read_failed(watcher, source, error, moment)
            continue
        watcher.failures.pop(source, None)
    if not messages:
        return lines
    reads_asked = []
    with state.edit(watcher.name) as data:
        for run, handles, message in messages:
            job_id = handles.get(message["from_handle"])
            if not job_id:
                continue
            if message["type"] == "question" and (question := reads.request(message["body"] or "")):
                reads_asked.append((job_id, run, message["id"], question))
                continue
            lines.append(mail_line(job_id, data["jobs"][job_id], message, moment))
    checkout = Path(data["roles"]["coordinator"]["cwd"])
    lines += [answer_read(*asked, checkout) for asked in reads_asked]
    return [line for line in lines if line]


def answer_read(job_id: str, run: str, message_id: str, question: str, checkout: Path) -> str | None:
    """Answer a remote lane's `LOG` or `READ` question here, since a Sprite holds no CI credentials or drive records."""
    try:
        orca.call("reply", "--id", message_id, "--run", run, "--body", reads.answer(question, checkout))
    except orca.OrcaError as error:
        return f"WATCH read answer to {job_id} failed: {error}; its lane asks again when its wait ends"
    return None


def page_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Redraw the drive's Slack status page when its model changed since the last drawing."""
    model = view.read(watcher.name, moment)
    shape = json.dumps(model | {"at": None}, sort_keys=True)
    if shape == watcher.page_shape:
        return []
    try:
        page.draw(watcher.name, page.render(model))
    except page.PageError as error:
        said = watcher.failure_said.get("status page")
        if said and moment - said < FAILURE_EVERY:
            return []
        watcher.failure_said["status page"] = moment
        return [f"WATCH status page draw failed: {error}"]
    watcher.page_shape = shape
    return []


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
    if moment - last < TICK_EVERY:
        return False
    if order["state"] == "done":
        return changed_since(order, jobs, max(last, moment - TICK_EVERY))
    if order["state"] not in OWING_STATES:
        return False
    return changed_since(order, jobs, last) or due_soon(order, moment)


def tick_lines(watcher: Watcher, moment: datetime) -> list[str]:
    data = state.load(watcher.name)
    if state.owner_away(data, moment):
        return []
    owing = [key for key, order in data["orders"].items() if owes_line(order, data["jobs"], moment)]
    return [f"TICK {' '.join(owing)}"] if owing else []


def run_pass(watcher: Watcher, moment: datetime) -> list[str]:
    """One loop of the watch: GitHub and the status page every 60 s, GitHub again next poll after a failure, new events, passed due times, a tick every 30 minutes while he is here."""
    lines = []
    if watcher.read_at is None or moment - watcher.read_at >= READ_EVERY:
        watcher.read_at = moment
        lines += github_lines(watcher, moment)
        lines += lane_lines(watcher, moment)
        lines += page_lines(watcher, moment)
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
    if state.session_role(state.load(name), os.environ.get("CLAUDE_CODE_SESSION_ID")) != "coordinator":
        return 0
    if not claim(name):
        return 0
    watcher = Watcher(name, offset=state.watch_cursor(name), ticked_at=state.now())
    state.save_watch_cursor(name, watcher.offset)
    try:
        while True:
            read_from = watcher.offset
            for line in run_pass(watcher, state.now()):
                print(line, flush=True)
            if watcher.offset != read_from:
                state.save_watch_cursor(name, watcher.offset)
            time.sleep(POLL_SECONDS)
    finally:
        release(name)
