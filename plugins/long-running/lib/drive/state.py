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

PR_URL = re.compile(r"(?:https?://)?github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
PR_KEY = re.compile(r"([^/\s#]+)/([^/\s#]+)#(\d+)")
OFFSET = re.compile(r"\+(\d+)([mhd])")
CLOCK = re.compile(r"(\d{1,2}):(\d{2})")
OFFSET_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


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


def new_drive(name: str, session: str | None, cwd: str, channel: str | None) -> dict:
    return {
        "name": name,
        "driver": {"session": session, "cwd": cwd, "started": stamp()},
        "slack": {"channel": channel, "status_card": None},
        "orders": {},
        "jobs": {},
        "prs": {},
    }


def load(name: str) -> dict:
    try:
        return json.loads(state_path(name).read_text())
    except FileNotFoundError:
        raise DriveError(f"no drive named {name}") from None


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


def create(data: dict) -> None:
    with locked(data["name"]):
        save(data["name"], data)


def next_id(records: dict, prefix: str) -> str:
    taken = [int(key[len(prefix) :]) for key in records if key.startswith(prefix) and key[len(prefix) :].isdigit()]
    return f"{prefix}{max(taken, default=0) + 1}"


def session_drives(session: str) -> list[str]:
    matches = [(load(name), name) for name in names()]
    bound = [
        (data["driver"].get("started") or "", name)
        for data, name in matches
        if data["driver"].get("session") == session
    ]
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
        raise DriveError("no drives; start one with: drive start <name>")
    raise DriveError(f"several drives, pass --drive: {', '.join(available)}")


def append_event(name: str, kind: str, **fields: object) -> dict:
    event = {"kind": kind, "at": stamp(), **fields}
    with open(events_path(name), "a") as handle:
        handle.write(json.dumps(event) + "\n")
    return event


def events_end(name: str) -> int:
    path = events_path(name)
    return path.stat().st_size if path.exists() else 0


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


def open_orders(data: dict) -> dict[str, dict]:
    return {key: order for key, order in data["orders"].items() if order["state"] not in CLOSED_ORDER_STATES}
