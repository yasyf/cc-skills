import fcntl
import json
import os
import re
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from drive import picture

ORDER_STATES = ("open", "working", "waiting", "done", "dropped")
ORDER_KINDS = ("answer", "build")
CLOSED_ORDER_STATES = ("done", "dropped")
JOB_KINDS = ("builder", "reader", "design", "codex")
JOB_STATES = ("running", "waiting", "built", "done", "failed", "stopped")
FINISHED_JOB_STATES = ("done", "failed", "stopped")
INCIDENT_STOP = "stop"
RESULT_STATES = {"DONE": "done", "READY": "waiting", "WAITING": "waiting", "DESIGN": "waiting", "ASK": "waiting", "BLOCKED": "failed", "HANDOFF": "stopped"}
BUILT_LINE = re.compile(r"BUILT (?P<branch>[^\s,]+),? waits on (?P<cards>.+)")
BUILT_CARD = re.compile(r"https://[^\s,]+/archives/[^\s,]+/p\d+[^\s,]*|[\w.-]+:j\d+-\d+[\w-]*")
EXEMPT_CLASSES = ("pure port", "test-only", "ci-only", "flake fix", "incident fix")
TAP_LABELS = ("Approve", "Hold")
TOOLING = "Drive tooling:"
ROLES = ("communicator", "coordinator")
LANE_ROLES = {"communicator": "communicator", "root": "coordinator"}
DRIVE_LANES = ("root", "communicator", "coordinator", "handler", "builder", "reader", "designer", "root-tooling", "root-skill", "root-dashboard", "watch")
NUMBERED_LANE = re.compile(r"(?P<stem>.+)-\d+")
LOCAL_ROUTE = "local"
DISPATCH_PREFIX = "ctx_"

PR_URL = re.compile(r"(?:https?://)?github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
RELEASE_WAIT = re.compile(r"release:(?P<service>[\w.-]+):(?P<pr>[^/\s#]+/[^/\s#]+#\d+)")
PR_KEY = re.compile(r"([^/\s#]+)/([^/\s#]+)#(\d+)")
CARD_LINK = re.compile(r"/archives/([A-Z0-9]+)/p(\d{10})(\d{6})(?:\?thread_ts=([\d.]+))?")
OFFSET = re.compile(r"\+(\d+)([mhd])")
CLOCK = re.compile(r"(\d{1,2}):(\d{2})")
OFFSET_UNITS = {"m": "minutes", "h": "hours", "d": "days"}
AWAY_AFTER = timedelta(minutes=60)
PART_CHARS = 80
REBUILT_ASKS = ".rebuilt"
NOT_LANE = re.compile(r"[^a-z0-9]+")


class DriveError(Exception):
    pass


def root() -> Path:
    if explicit := os.environ.get("LONG_RUNNING_STATE"):
        return Path(explicit)
    if xdg := os.environ.get("XDG_STATE_HOME"):
        return Path(xdg) / "long-running"
    return Path.home() / ".local" / "state" / "long-running"


def drive_dir(name: str) -> Path:
    return root() / name


def state_path(name: str) -> Path:
    return drive_dir(name) / "drive.json"


def events_path(name: str) -> Path:
    return drive_dir(name) / "events.jsonl"


def project_lane(project: str) -> str:
    """The cci lane of a project's sub-coordinator: its name lowercased, each run of other characters a dash."""
    return NOT_LANE.sub("-", project.lower()).strip("-")


def watch_suffix(project: str | None) -> str:
    return f"-{project_lane(project)}" if project else ""


def pid_path(name: str, project: str | None = None) -> Path:
    return drive_dir(name) / f"watchd{watch_suffix(project)}.pid"


def monitor_pid_path(name: str) -> Path:
    return drive_dir(name) / "watch.pid"


def watch_log_path(name: str, project: str | None = None) -> Path:
    return drive_dir(name) / f"watch{watch_suffix(project)}.log"


def cursor_path(name: str, project: str | None = None) -> Path:
    return drive_dir(name) / f"watch{watch_suffix(project)}.cursor"


def held_path(name: str) -> Path:
    return drive_dir(name) / "watch.held"


def cards_path(name: str) -> Path:
    return drive_dir(name) / "cards.json"


def replies_path(name: str, job_id: str) -> Path:
    return drive_dir(name) / "jobs" / f"{job_id}.replies.jsonl"


def report_path(name: str, job_id: str) -> Path:
    return drive_dir(name) / "reports" / f"{job_id}.md"


def report_text(name: str, job_id: str, job: dict) -> str:
    """A job's whole return as kept in the drive, or a remote lane's latest report, or nothing."""
    path = report_path(name, job_id)
    return path.read_text() if path.exists() else job.get("report") or ""


def keep_reply(name: str, job_id: str, message: dict) -> None:
    """Append one message a remote lane sent to its job's history, which a later result never replaces."""
    path = replies_path(name, job_id)
    path.parent.mkdir(exist_ok=True)
    fields = ("created_at", "id", "type", "subject", "body")
    with open(path, "a") as handle:
        handle.write(json.dumps({field: message[field] for field in fields}) + "\n")


def replies(name: str, job_id: str) -> list[dict]:
    """The job's kept messages, oldest first, each once: a watch stopped between keeping a message and saving its cursor keeps it again."""
    path = replies_path(name, job_id)
    kept = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return list({reply["id"]: reply for reply in kept}.values())


def names() -> list[str]:
    base = root()
    if not base.is_dir():
        return []
    return sorted(entry.name for entry in base.iterdir() if (entry / "drive.json").is_file())


def exists(name: str) -> bool:
    return state_path(name).is_file()


def now() -> datetime:
    return datetime.now().astimezone()


def stamp(moment: datetime | None = None) -> str:
    return (moment or now()).isoformat(timespec="seconds")


def parse_time(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.astimezone()


def parse_due(text: str, reference: datetime | None = None) -> datetime:
    """Parse a due time: "HH:MM" (today, or tomorrow once passed), "+30m"/"+2h"/"+1d", or ISO 8601."""
    base = reference or now()
    if offset := OFFSET.fullmatch(text.strip()):
        return (base + timedelta(**{OFFSET_UNITS[offset[2]]: int(offset[1])})).replace(microsecond=0)
    if clock := CLOCK.fullmatch(text.strip()):
        due = base.replace(hour=int(clock[1]), minute=int(clock[2]), second=0, microsecond=0)
        return due if due > base else due + timedelta(days=1)
    try:
        return parse_time(text.strip())
    except ValueError:
        raise DriveError(f"cannot read due time {text!r}: use HH:MM, +30m, +2h or ISO 8601") from None


def pr_key(ref: str) -> str:
    """The "owner/name#123" key of a GitHub PR url, or of a key already in that form."""
    match = PR_URL.search(ref) or PR_KEY.fullmatch(ref.strip())
    if not match:
        raise DriveError(f"not a GitHub PR url: {ref}")
    return f"{match[1]}/{match[2]}#{match[3]}"


def pr_url(key: str) -> str:
    repo, number = key.split("#")
    return f"https://github.com/{repo}/pull/{number}"


def new_drive(
    name: str, role: str, session: str | None, cwd: str, channel: str | None, terminal: str | None = None
) -> dict:
    return {
        "name": name,
        "roles": {role: {"session": session, "terminal": terminal, "cwd": cwd, "started": stamp()}},
        "subcoordinators": {},
        "slack": {"channel": channel, "page": None, "watch": []},
        "owner": {"heard": None, "away": False},
        "orders": {},
        "jobs": {},
        "prs": {},
        "answers": {},
    }


def load(name: str) -> dict:
    """Read a drive; one saved with a single driver session reads as that session coordinating."""
    try:
        data = json.loads(state_path(name).read_text())
    except FileNotFoundError:
        raise DriveError(f"no drive named {name}") from None
    if "driver" in data:
        data["roles"] = {"coordinator": data.pop("driver")}
    data.setdefault("subcoordinators", {})
    for order in data["orders"].values():
        if "parts" not in order:
            whole = whole_order(order["text"])
            order["parts"] = {whole: new_part()}
            for job_id in order["jobs"]:
                data["jobs"][job_id].setdefault("parts", [whole])
        if "promised" not in order:
            order.update(promised=order.get("due") and order["updated"], next_due=None, kept=None, withdrawn=None)
        order.setdefault("next_set", None)
        order.setdefault("incident", False)
        order.pop("milestone", None)
        order.setdefault("project", None)
        order.setdefault("workstream", None)
        order.setdefault("kind", None)
        order.setdefault("complex", False)
        order.setdefault("options_only", False)
        order.setdefault("once", False)
        order.setdefault("thread", None)
        order.setdefault("from_thread", None)
        order.setdefault("releases", {})
        order.setdefault("shipped", {})
        order.setdefault("owner", "communicator" if order["text"].startswith(TOOLING) else None)
        order["thread"] = dm_thread(data, order)
    mail = data.pop("lane_mail", {})
    for job in data["jobs"].values():
        job.setdefault("carries", {})
        job.setdefault("mail_seen", mail.get(job.get("run"), 0))
        job.setdefault("waiting_said", [])
        job.setdefault("waiting_claims", {})
        job.setdefault("waits", [])
        job.setdefault("route", None)
        job.setdefault("may_settle", None)
        job.setdefault("due", None)
        if job.get("ask") and any(reported(text.lstrip()) for text in (job["said"] or "", job["report"] or "")):
            job["ask"] = None
        if job.get("ask"):
            job.setdefault("ask_at", job["updated"])
        if (job.get("agent") or "").startswith(DISPATCH_PREFIX):
            job.update(local_lane(job["agent"]), agent=None)
        if "questions" not in job:
            asked, card = job.get("question"), job.get("card")
            job["questions"] = [new_question(asked, job["said"], job["updated"], job.get("unsettled"), card)] if asked or card else []
    for pr in data["prs"].values():
        waits = pr.get("waits")
        pr["waits"] = waits if isinstance(waits, list) else [waits] if waits else []
        pr.setdefault("conflict", False)
        pr.setdefault("failed_checks", [])
        pr.setdefault("hold_label", False)
    data.setdefault("answers", {})
    for answer in data["answers"].values():
        answer.setdefault("jobs", {})
    if (pictures := drive_dir(name) / "pictures").is_dir() and not (pictures / picture.SOURCED).exists():
        picture.fill(pictures, [*map(Path.read_text, sorted((drive_dir(name) / "asks").glob("*.md"))), *pr_bodies(name)])
    if cards_path(name).exists() and not (drive_dir(name) / "asks" / REBUILT_ASKS).exists():
        rebuild_asks(name, data)
    for job in data["jobs"].values():
        for question in job["questions"]:
            if question["waits"] and not question["file"] and (kept := rebuilt_ask(name, question["waits"])).exists():
                question["file"] = str(kept)
    return data


def rebuilt_ask(name: str, waits: str) -> Path:
    return drive_dir(name) / "asks" / f"{waits.partition(':')[2]}.md"


def rebuild_asks(name: str, data: dict) -> None:
    """Keep an ASK file, rebuilt from the card the drive stored, for each of his open question cards whose job's question keeps none, so `drive cards redraw` redraws it; marked so this runs once, from cards read live."""
    from drive import limits

    if (stored_cards := json.loads(cards_path(name).read_text()))["source"] != "live":
        return
    unkept = {question["waits"] for job in data["jobs"].values() for question in job["questions"] if question["waits"] and not question["file"]}
    (asks := drive_dir(name) / "asks").mkdir(exist_ok=True)
    for stored in stored_cards["questions"]:
        if (waits := stored["card"].get("waits")) in unkept and not (kept := rebuilt_ask(name, waits)).exists():
            kept.write_text(limits.rebuilt(stored["card"]))
    (asks / REBUILT_ASKS).touch()


def pr_bodies(name: str) -> list[str]:
    if not cards_path(name).exists():
        return []
    return [row.get("description") or "" for card in json.loads(cards_path(name).read_text())["pr_cards"] for row in card["card"].get("prs") or []]


def dm_thread(data: dict, order: dict) -> str | None:
    """The order's thread, or, when it is the ts of the order's own post in a channel that is neither his DM nor one the drive watches, the thread its posts in his DM hang from."""
    posts = [(link[1], f"{link[2]}.{link[3]}", link[4]) for part in order["parts"].values() if (link := CARD_LINK.search(part["posted"] or ""))]
    ours = {data["slack"]["channel"], *data["slack"].get("watch", [])}
    if not any(ts == order["thread"] and channel not in ours for channel, ts, _ in posts):
        return order["thread"]
    return next((thread or ts for channel, ts, thread in posts if channel == data["slack"]["channel"]), None)


def job_parts(job: dict, order_id: str) -> list[str]:
    """The parts of an order a job delivers: its own order's, or those it carries for another."""
    return job["parts"] if job["order"] == order_id else job["carries"].get(order_id, [])


def carry(data: dict, job_id: str, order_id: str, parts: list[str]) -> None:
    job, order = data["jobs"][job_id], data["orders"][order_id]
    job["carries"][order_id] = list(dict.fromkeys([*job["carries"].get(order_id, []), *parts]))
    if job_id not in order["jobs"]:
        order["jobs"].append(job_id)
    order["updated"] = stamp()


def drop_carries(data: dict, job_id: str) -> None:
    """A done job stops carrying other orders' parts, so a part it leaves undelivered reads as nobody's again."""
    for order_id in data["jobs"][job_id]["carries"]:
        data["orders"][order_id]["jobs"].remove(job_id)
    data["jobs"][job_id]["carries"] = {}


def agent_job(data: dict, agent: str | None) -> str | None:
    return next((job_id for job_id, job in data["jobs"].items() if agent and job.get("agent") == agent), None)


def result_state(job: dict, line: str) -> str | None:
    """The state a job's returned line moves it to, or None when the line is no result: a reader's answer or DESIGN to done, since a design lane takes its choice on, `BUILT <branch>, waits on <cards>` to built, and an ASK to waiting on his card."""
    opening = re.match(r"[A-Z]*", line)[0]
    reader = job["kind"] == "reader"
    if BUILT_LINE.match(line):
        return "built"
    return "done" if reader and opening == "DESIGN" else RESULT_STATES.get(opening, "done" if reader else None)


def reported(line: str) -> bool:
    """Whether a line opens with a result other than an ASK, so a lane that sent it as an Orca question returned that result and asked nothing."""
    opening = re.match(r"[A-Z]*", line)[0]
    return bool(BUILT_LINE.match(line)) or opening in RESULT_STATES and opening != "ASK"


def take_result(data: dict, job_id: str, line: str) -> None:
    """Move a running job to the state its result line opens with, with that line as its said line, built on its branch and waiting on each card a BUILT line names; an ASK keeps the said line drive job ask set, the question's title."""
    job = data["jobs"][job_id]
    taken = result_state(job, line)
    if job["state"] != "running" or not taken:
        return
    job.update(state=taken, updated=stamp())
    if not re.match(r"ASK\b", line):
        job["said"] = line
    if built := BUILT_LINE.match(line):
        job.update(branch=built["branch"], waits=list(dict.fromkeys([*job["waits"], *BUILT_CARD.findall(built["cards"])])))
    if taken == "done":
        drop_carries(data, job_id)


def local_lane(dispatch: str) -> dict:
    """The fields of a job whose Orca worker runs on this Mac: a lane read through its Dispatch like a Sprite's, with no Sprite."""
    return {"where": "remote", "route": LOCAL_ROUTE, "dispatch": dispatch, "workspace": None, "environment": None}


def on_mac(job: dict) -> bool:
    return job["route"] == LOCAL_ROUTE


def lane_died(job: dict, dispatch: str, why: str, moment: datetime) -> bool:
    """Record once that the remote lane on this dispatch died or finished, so it takes no more messages; whether this call recorded it."""
    deaths = job.setdefault("deaths", [])
    if job["dispatch"] != dispatch or any(death["dispatch"] == dispatch for death in deaths):
        return False
    deaths.append({"dispatch": dispatch, "workspace": job["workspace"], "why": why, "at": stamp(moment)})
    job["updated"] = stamp(moment)
    return True


def whole_order(text: str) -> str:
    return " ".join(text.split())[:PART_CHARS]


def new_part() -> dict:
    return {"posted": None, "orphan_said": None}


def new_job(order_id: str, kind: str, goal: str, parts: list[str], created: str) -> dict:
    return {
        "order": order_id,
        "kind": kind,
        "goal": goal,
        "agent": None,
        "session": os.environ.get("CLAUDE_CODE_SESSION_ID"),
        "parts": parts,
        "carries": {},
        "questions": [],
        "where": "local",
        "route": None,
        "repo": None,
        "branch": None,
        "worktree": None,
        "prs": [],
        "waits": [],
        "waiting_said": [],
        "waiting_claims": {},
        "mail_seen": 0,
        "state": "running",
        "said": None,
        "may_settle": None,
        "due": None,
        "created": created,
        "updated": created,
    }


def new_question(file: str | None, title: str | None, asked: str, unsettled: str | None = None, card: str | None = None) -> dict:
    return {"file": file, "title": title, "asked": asked, "unsettled": unsettled, "waits": None, "card": card}


def save(name: str, data: dict) -> None:
    path = state_path(name)
    tmp = path.with_name("drive.json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


@contextmanager
def flocked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def locked(name: str) -> AbstractContextManager[None]:
    return flocked(drive_dir(name) / ".lock")


def binding(name: str) -> AbstractContextManager[None]:
    """Hold the coordinator terminal's one Run binding for a bind and the call that needs it: a launch's worker-start, a reply."""
    return flocked(drive_dir(name) / ".binding.lock")


@contextmanager
def edit(name: str) -> Iterator[dict]:
    """Load the drive under its lock, yield it for changes, and save it atomically on exit."""
    with locked(name):
        data = load(name)
        yield data
        save(name, data)


@contextmanager
def edit_or_create(name: str, fresh: dict) -> Iterator[dict]:
    """Like edit, but start from fresh when the drive does not exist yet; the lock covers the check too."""
    with locked(name):
        data = load(name) if exists(name) else fresh
        yield data
        save(name, data)


def create(data: dict) -> None:
    with locked(data["name"]):
        save(data["name"], data)


def next_id(records: dict, prefix: str) -> str:
    taken = [int(key[len(prefix) :]) for key in records if key.startswith(prefix) and key[len(prefix) :].isdigit()]
    return f"{prefix}{max(taken, default=0) + 1}"


def session_role(data: dict, session: str | None) -> str | None:
    """The role a session holds; a sub-coordinator's is coordinator, so every rule of that role binds it too."""
    role = next((role for role, held in data["roles"].items() if session and held["session"] == session), None)
    return role or ("coordinator" if session_project(data, session) else None)


def session_project(data: dict, session: str | None) -> str | None:
    """The project a session is the sub-coordinator for, or None for any other session."""
    return next((project for project, held in data["subcoordinators"].items() if session and held["session"] == session), None)


def bound_sessions(data: dict) -> dict[str, dict]:
    """Every session bound to the drive: each role's by the role's name, each sub-coordinator's as `sub-coordinator <its project>`."""
    return data["roles"] | {f"sub-coordinator {project}": held for project, held in data["subcoordinators"].items()}


def projects(data: dict) -> list[str]:
    return sorted({order["project"] for order in data["orders"].values() if order["project"]})


def order_lane(data: dict, order: dict) -> str:
    """The cci lane of the coordinator session that hears an order: the sub-coordinator's for its project, else root."""
    return project_lane(order["project"]) if order["project"] in data["subcoordinators"] else "root"


def heard_lane(data: dict, order_id: str) -> str:
    """The cci lane of the coordinator session hearing an order now, which an event about it is written with."""
    return order_lane(data, data["orders"][order_id]) if data["subcoordinators"] else "root"


def hears(data: dict, order: dict, project: str | None) -> bool:
    """Whether the coordinator session for project hears an order: a sub-coordinator its project's, the top coordinator every other one, unfiled or filed under a project no sub-coordinator is bound for."""
    return order["project"] == project if project else order["project"] not in data["subcoordinators"]


def agent_gone(data: dict, job: dict) -> bool:
    """Whether the job's agent ended with its session, which no role holds any more."""
    return session_role(data, job.get("session")) is None


def held_elsewhere(data: dict, order_id: str, role: str | None) -> str | None:
    """Why a session's role may not act on an order the drive's other session holds, naming the holder and its session; an order from before owners is held by the first role to add a job to it."""
    holder = data["orders"][order_id]["owner"]
    if role is None or holder in (None, role):
        return None
    session = (data["roles"].get(holder) or {}).get("session") or "not started"
    return f"{order_id} is the {holder}'s order, session {session}: only the {holder} adds its jobs, starts its agents and posts its reports; send the {holder} a record instead"


def last_started(data: dict) -> str:
    return max(held["started"] for held in data["roles"].values())


def session_drives(session: str) -> list[str]:
    bound = []
    for name in names():
        bound += [(held["started"], name) for held in bound_sessions(load(name)).values() if held["session"] == session]
    return list(dict.fromkeys(name for _, name in sorted(bound, reverse=True)))


def resolve(name: str | None = None) -> str:
    """The drive a verb acts on: --drive, $LONG_RUNNING_DRIVE, this session's drive, or the only drive."""
    chosen = name or os.environ.get("LONG_RUNNING_DRIVE")
    if chosen:
        if not exists(chosen):
            raise DriveError(f"no drive named {chosen}")
        return chosen
    if (session := os.environ.get("CLAUDE_CODE_SESSION_ID")) and (bound := session_drives(session)):
        return bound[0]
    available = names()
    if len(available) == 1:
        return available[0]
    if not available:
        raise DriveError("no drives; start one with: drive start <name> --role coordinator")
    raise DriveError(f"several drives, pass --drive: {', '.join(available)}")


def append_event(name: str, kind: str, **fields: object) -> dict:
    event = {"kind": kind, "at": stamp(), **fields}
    with open(events_path(name), "a") as handle:
        handle.write(json.dumps(event) + "\n")
    return event


def events_end(name: str) -> int:
    path = events_path(name)
    return path.stat().st_size if path.exists() else 0


def watch_cursor(name: str, project: str | None = None) -> int:
    """The events offset the drive's watch, or a project's, has read up to: for a watch that never ran, the log's end, or for a project's where the log ended as its sub-coordinator was first bound."""
    path = cursor_path(name, project)
    if path.exists():
        return int(path.read_text())
    return load(name)["subcoordinators"][project]["from"] if project else events_end(name)


def save_watch_cursor(name: str, offset: int, project: str | None = None) -> None:
    path = cursor_path(name, project)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(f"{offset}\n")
    os.replace(tmp, path)


def take_held_lines(name: str) -> list[str]:
    """The lines a watch run under a Monitor held for its next batch when it stopped, once, so the watch process delivers them."""
    path = held_path(name)
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    path.unlink()
    return lines


def read_events(name: str, offset: int = 0) -> Iterator[tuple[dict, int]]:
    """Yield each complete event after byte offset, paired with the offset just past it."""
    path = events_path(name)
    if not path.exists():
        return
    with open(path, "rb") as handle:
        handle.seek(offset)
        for line in handle:
            if not line.endswith(b"\n"):
                return
            offset += len(line)
            if line.strip():
                yield json.loads(line), offset


def owner_away(data: dict, moment: datetime) -> bool:
    """Whether he is away: he said so, or has written nothing for an hour; a drive that never heard him counts him present."""
    owner = data.get("owner") or {}
    heard = owner.get("heard")
    return bool(owner.get("away")) or bool(heard) and moment - parse_time(heard) >= AWAY_AFTER


def open_orders(data: dict) -> dict[str, dict]:
    return {key: order for key, order in data["orders"].items() if order["state"] not in CLOSED_ORDER_STATES}


def open_incidents(data: dict, order_ids: list[str]) -> list[str]:
    return [order_id for order_id in order_ids if data["orders"][order_id]["incident"] and data["orders"][order_id]["state"] not in CLOSED_ORDER_STATES]


def running_stops(data: dict, order_id: str) -> list[str]:
    return [job_id for job_id in data["orders"][order_id]["jobs"] if INCIDENT_STOP in data["jobs"][job_id]["parts"] and data["jobs"][job_id]["state"] not in FINISHED_JOB_STATES]


def incident_job(data: dict, name: str, order_id: str, part: str, created: str) -> str:
    order = data["orders"][order_id]
    job_id = next_id(data["jobs"], "j")
    data["jobs"][job_id] = new_job(order_id, "codex", f"{part}: {whole_order(order['text'])}", [part], created)
    order["jobs"].append(job_id)
    return f'{job_id} on "{part}": start its agent now, its brief ending Drive: {name}, order {order_id}, job {job_id}'


def promise_thread(data: dict, order: dict) -> tuple[str, str] | None:
    """The channel and thread an order's promised line goes in: its thread, in his DM unless it is the Slack message the order came from or the thread that message is in, as an alert's is or his reply's; else that message's."""
    source = order["from"].removeprefix("slack:").split("/") if order["from"].startswith("slack:") else [None, None]
    thread = order["thread"] or source[1]
    channel = source[0] if thread in (source[1], order["from_thread"]) else data["slack"]["channel"]
    return (channel, thread) if channel and thread else None


def owed(order: dict) -> bool:
    """Whether the order owes him a line for a promised time: no drive post in its thread has kept it, and he has not withdrawn it."""
    return bool(order["due"]) and not order["kept"] and not order["withdrawn"]


def owed_at(order: dict) -> str:
    return min((due for due in (order["due"], order["next_due"]) if due), key=parse_time)


def promise(order: dict, due: str, moment: datetime) -> None:
    """Promise a line by due, owed from the next line in the order's thread, the one that tells him the time; a time already owed holds until that line lands."""
    if not owed(order):
        order.update(due=due, promised=stamp(moment), kept=None, withdrawn=None, due_said=None)
    order.update(next_due=due, next_set=stamp(moment))


def remind(order: dict, due: str, moment: datetime) -> None:
    """Set the time an order with no thread wakes the drive at: no post can keep it, so it replaces any time set before and is said once."""
    order.update(due=due, promised=stamp(moment), kept=None, withdrawn=None, due_said=None, next_due=None, next_set=None)


def keep(order: dict, posts: list[datetime], announced: bool = True) -> None:
    """Record the first drive post after the promise as keeping it; a post that lands before the next time starts that time from it."""
    kept = next((post for post in posts if post > parse_time(order["promised"])), None)
    if kept is None:
        return
    upcoming, set_at = order["next_due"], order["next_set"]
    order.update(kept=kept.isoformat(), next_due=None, next_set=None)
    if not announced or not upcoming or kept >= parse_time(upcoming):
        return
    if set_at and kept < parse_time(set_at):
        order.update(due=upcoming, promised=set_at, kept=None, due_said=None, next_due=upcoming)
        keep(order, posts)
        return
    order.update(due=upcoming, promised=kept.isoformat(), kept=None, due_said=None)
    keep(order, posts, announced=False)


def keep_closing(order: dict, posts: list[datetime], written: datetime) -> None:
    """Record the line of a report that closes the order as keeping every time it owes: the first drive post after the promise, or the post that told him the time when the report was written before it, since that post is also the order's last line."""
    promised = parse_time(order["promised"])
    kept = next((post for post in posts if post > promised or post == promised >= written), None)
    if kept is not None:
        order.update(kept=kept.isoformat(), next_due=None, next_set=None)


def open_pr_keys(data: dict) -> list[str]:
    return [key for key, pr in data["prs"].items() if pr["state"] == "open"]


def open_prs(data: dict) -> list[dict]:
    return [data["prs"][key] for key in open_pr_keys(data)]


def card_wait(data: dict, wait: str) -> bool:
    """Whether a wait names a card of his, by its Slack link or waits id, rather than a job, a PR or a release of the drive."""
    return wait not in data["jobs"] and wait not in data["prs"] and not RELEASE_WAIT.fullmatch(wait)


def owed_step(pr: dict) -> str | None:
    """What the drive owes a PR once it is green and approved: its landing when an exempt class lands it with no tap, his card when it waits on his tap, and nothing when its author or its own builder lands it."""
    if pr.get("exempt"):
        return "landing"
    return "card" if pr["wants_tap"] else None


def new_pr(url: str, job_id: str, order_id: str) -> dict:
    return {
        "url": url,
        "job": job_id,
        "order": order_id,
        "wants_tap": False,
        "tap": None,
        "exempt": None,
        "fundamental": None,
        "state": "open",
        "title": "",
        "head": "",
        "checks": "pending",
        "failed_checks": [],
        "review": "none",
        "conflict": False,
        "hold_label": False,
        "draft": False,
        "merged_sha": None,
        "stuck": None,
        "waits": [],
        "updated": stamp(),
    }
