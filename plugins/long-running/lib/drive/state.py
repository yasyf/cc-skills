import fcntl
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

ORDER_STATES = ("open", "working", "waiting", "done", "dropped")
CLOSED_ORDER_STATES = ("done", "dropped")
JOB_KINDS = ("builder", "reader", "codex")
JOB_STATES = ("running", "waiting", "done", "failed", "stopped")
EXEMPT_CLASSES = ("pure port", "test-only", "ci-only", "flake fix", "incident fix")
TAP_LABELS = ("Approve", "Hold")
ROLES = ("communicator", "coordinator")

PR_URL = re.compile(r"(?:https?://)?github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
PR_KEY = re.compile(r"([^/\s#]+)/([^/\s#]+)#(\d+)")
OFFSET = re.compile(r"\+(\d+)([mhd])")
CLOCK = re.compile(r"(\d{1,2}):(\d{2})")
OFFSET_UNITS = {"m": "minutes", "h": "hours", "d": "days"}
AWAY_AFTER = timedelta(minutes=60)
PART_CHARS = 80


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


def pid_path(name: str) -> Path:
    return drive_dir(name) / "watch.pid"


def cursor_path(name: str) -> Path:
    return drive_dir(name) / "watch.cursor"


def cards_path(name: str) -> Path:
    return drive_dir(name) / "cards.json"


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
        "slack": {"channel": channel, "page": None, "watch": []},
        "owner": {"heard": None, "away": False},
        "orders": {},
        "jobs": {},
        "prs": {},
    }


def load(name: str) -> dict:
    """Read a drive; one saved with a single driver session reads as that session coordinating."""
    try:
        data = json.loads(state_path(name).read_text())
    except FileNotFoundError:
        raise DriveError(f"no drive named {name}") from None
    if "driver" in data:
        data["roles"] = {"coordinator": data.pop("driver")}
    for order in data["orders"].values():
        if "parts" not in order:
            whole = whole_order(order["text"])
            order["parts"] = {whole: new_part()}
            for job_id in order["jobs"]:
                data["jobs"][job_id].setdefault("parts", [whole])
        if "promised" not in order:
            order.update(promised=order.get("due") and order["updated"], next_due=None, kept=None, withdrawn=None)
        order.setdefault("next_set", None)
    return data


def whole_order(text: str) -> str:
    return " ".join(text.split())[:PART_CHARS]


def new_part() -> dict:
    return {"posted": None, "orphan_said": None}


def save(name: str, data: dict) -> None:
    path = state_path(name)
    tmp = path.with_name("drive.json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


@contextmanager
def locked(name: str) -> Iterator[None]:
    directory = drive_dir(name)
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / ".lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


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
    return next((role for role, held in data["roles"].items() if session and held["session"] == session), None)


def last_started(data: dict) -> str:
    return max(held["started"] for held in data["roles"].values())


def session_drives(session: str) -> list[str]:
    bound = []
    for name in names():
        data = load(name)
        if role := session_role(data, session):
            bound.append((data["roles"][role]["started"], name))
    return [name for _, name in sorted(bound, reverse=True)]


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


def watch_cursor(name: str) -> int:
    """The events offset the drive's watch has read up to; the log's end for a drive whose watch never ran."""
    path = cursor_path(name)
    return int(path.read_text()) if path.exists() else events_end(name)


def save_watch_cursor(name: str, offset: int) -> None:
    path = cursor_path(name)
    tmp = path.with_name("watch.cursor.tmp")
    tmp.write_text(f"{offset}\n")
    os.replace(tmp, path)


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


def promise_thread(data: dict, order: dict) -> tuple[str, str] | None:
    """The channel and thread an order's promised line goes in: its thread, else the Slack message it came from."""
    source = order["from"].removeprefix("slack:").split("/") if order["from"].startswith("slack:") else None
    channel = source[0] if source else data["slack"]["channel"]
    thread = order["thread"] or (source[1] if source else None)
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


def open_pr_keys(data: dict) -> list[str]:
    return [key for key, pr in data["prs"].items() if pr["state"] == "open"]


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
        "review": "none",
        "draft": False,
        "merged_sha": None,
        "stuck": None,
        "updated": stamp(),
    }
