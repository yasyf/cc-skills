"""The lane files: one JSON file per lane under lanes/<drive>/, every write made under the drive's one lock and checked against the two-state invariant before it lands."""

import fcntl
import json
import os
import re
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

RUNNING = "running"
WAITING = "waiting"
STATES = (RUNNING, WAITING)
PR_FACTS = ("merged", "closed", "checks_done", "approved", "head_moved")
EVENT_KINDS = ("pr", "cut", "reply")
LANE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
DEFAULTS = {
    "active": 12,
    "slots": 12,
    "model": "opus",
    "effort": "high",
    "posting": True,
    "shadow": False,
    "pass_seconds": 30,
    "max_wait_hours": 24,
    "launch_grace_seconds": 180,
    "restarts_before_backoff": 3,
    "checkout": None,
}
LOG_KEEP = 200
PRESENCE = "presence.json"
NOT_LANES = ("rank", "config", "supervisor", "staging-env", "presence")


class Refused(Exception):
    """A verb the lane model does not allow, with the reason the caller is shown."""


def root() -> Path:
    if explicit := os.environ.get("LONG_RUNNING_STATE"):
        return Path(explicit) / "lanes"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "long-running" / "lanes"


def drive_dir(drive: str) -> Path:
    return root() / drive


def lane_path(drive: str, lane_id: str) -> Path:
    return drive_dir(drive) / f"{lane_id}.json"


def token_path(drive: str, lane_id: str) -> Path:
    return drive_dir(drive) / f"{lane_id}.owner"


def now() -> datetime:
    return datetime.now(UTC)


def stamp(moment: datetime | None = None) -> str:
    return (moment or now()).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def when(text: str, moment: datetime | None = None) -> datetime:
    """A time bound typed as a duration from now (45m, 2h, 1d) or an ISO time."""
    base = moment or now()
    if found := re.fullmatch(r"(\d+)([mhd])", text.strip()):
        return base + timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[found[2]]: int(found[1])})
    moment_typed = datetime.fromisoformat(text.strip())
    if moment_typed.tzinfo is None:
        moment_typed = moment_typed.astimezone()
    return moment_typed.astimezone(UTC)


def config(drive: str) -> dict:
    path = drive_dir(drive) / "config.json"
    return DEFAULTS | (json.loads(path.read_text()) if path.is_file() else {})


def lane_ids(drive: str) -> list[str]:
    folder = drive_dir(drive)
    return sorted(path.stem for path in folder.glob("*.json") if LANE_ID.match(path.stem) and path.stem not in NOT_LANES) if folder.is_dir() else []


def load(drive: str, lane_id: str) -> dict:
    path = lane_path(drive, lane_id)
    if not path.is_file():
        raise Refused(f"no lane {lane_id} on drive {drive}")
    return json.loads(path.read_text())


def load_all(drive: str) -> dict[str, dict]:
    return {lane_id: load(drive, lane_id) for lane_id in lane_ids(drive)}


def active(lanes: dict[str, dict]) -> list[str]:
    return [lane_id for lane_id, lane in lanes.items() if not lane.get("parked")]


def invalid(lane: dict) -> str | None:
    """Why a lane breaks the invariant, or None: running holds an owner and no wait; waiting holds no owner and a wait with a time bound, or is parked."""
    if lane.get("state") not in STATES:
        return f"state {lane.get('state')!r} is neither running nor waiting"
    if lane["state"] == RUNNING:
        if lane.get("wait") is not None:
            return "running with a wait"
        if not lane.get("owner"):
            return "running with no owner"
        return None
    if lane.get("owner"):
        return "waiting while it holds an owner"
    wait = lane.get("wait")
    if not wait:
        return "waiting on nothing"
    for event in wait.get("on", []):
        if event.get("kind") not in EVENT_KINDS:
            return f"waits on an unknown event {event}"
    if lane.get("parked"):
        return None if any(event["kind"] == "reply" for event in wait.get("on", [])) else "parked without a wake on his word"
    if not wait.get("until"):
        return "waiting with no time bound"
    return None


def new_lane(lane_id: str, title: str, words: str | None, channel: str | None, thread: str | None, link: str | None, ts: str | None) -> dict:
    if not LANE_ID.match(lane_id) or lane_id in NOT_LANES:
        raise Refused(f"a lane id is lowercase letters, digits and dashes: {lane_id!r}")
    moment = stamp()
    return {
        "id": lane_id,
        "title": title,
        "created": moment,
        "updated": moment,
        "state": WAITING,
        "parked": None,
        "channel": {"id": channel, "thread": thread} if channel else None,
        "asks": [{"at": moment, "words": words, "link": link, "ts": ts, "seen": False}] if words else [],
        "prs": [],
        "next": None,
        "wait": {"on": [{"kind": "reply"}], "until": moment, "why": "opened", "set": moment, "fired": moment, "fired_why": "opened"},
        "promise": None,
        "owner": None,
        "rank": None,
        "incident": None,
        "pinned": False,
        "worktree": None,
        "restarts": [],
        "log": [],
    }


def note(lane: dict, text: str) -> None:
    lane["log"] = [*lane.get("log", []), {"at": stamp(), "text": text}][-LOG_KEEP:]


@contextmanager
def locked(drive: str) -> Iterator[None]:
    folder = drive_dir(drive)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        yield


def transact[T](drive: str, change: Callable[[dict[str, dict]], T]) -> T:
    """Run change over every lane of the drive under its lock, then write each lane it changed, refusing the whole write when any changed lane breaks the invariant, and log each state move."""
    with locked(drive):
        before = load_all(drive)
        lanes = json.loads(json.dumps(before))
        result = change(lanes)
        changed = {lane_id: lane for lane_id, lane in lanes.items() if before.get(lane_id) != lane}
        for lane_id, lane in changed.items():
            if why := invalid(lane):
                raise Refused(f"lane {lane_id} would be left {why}")
        for lane_id, lane in changed.items():
            lane["updated"] = stamp()
            write_lane(drive, lane)
            old = before.get(lane_id) or {}
            if (old.get("state"), bool(old.get("parked"))) != (lane["state"], bool(lane.get("parked"))) or (old.get("owner") or {}).get("token") != (lane.get("owner") or {}).get("token"):
                event(drive, {"lane": lane_id, "from": old.get("state"), "to": lane["state"], "parked": bool(lane.get("parked")), "owner": (lane.get("owner") or {}).get("token"), "wait": lane.get("wait")})
        return result


def write_lane(drive: str, lane: dict) -> None:
    path = lane_path(drive, lane["id"])
    fresh = path.with_name(f".{lane['id']}.{os.getpid()}.tmp")
    fresh.write_text(json.dumps(lane, indent=1, ensure_ascii=False) + "\n")
    fresh.replace(path)
    token = token_path(drive, lane["id"])
    if owner := lane.get("owner"):
        if not token.is_file() or token.read_text().strip() != owner["token"]:
            token.write_text(owner["token"] + "\n")
    else:
        token.unlink(missing_ok=True)


def heard(drive: str, asks: list[dict]) -> None:
    """Keep when he was last present: the newest Slack ts among his own messages and taps reaching a lane, only ever moving forward, so a dangerous land reads his 15 minutes from his words alone. take_messages is its one caller."""
    if not (stamped := [ask for ask in asks if ask.get("ts")]):
        return
    newest = max(stamped, key=lambda ask: float(ask["ts"]))
    path = drive_dir(drive) / PRESENCE
    if path.is_file() and float(json.loads(path.read_text())["ts"]) >= float(newest["ts"]):
        return
    fresh = path.with_name(f".presence.{os.getpid()}.tmp")
    fresh.write_text(json.dumps({"at": stamp(datetime.fromtimestamp(float(newest["ts"]), UTC)), "ts": newest["ts"], "link": newest.get("link")}) + "\n")
    fresh.replace(path)


def event(drive: str, row: dict) -> None:
    with (drive_dir(drive) / "events.jsonl").open("a") as out:
        out.write(json.dumps({"at": stamp(), **row}, ensure_ascii=False) + "\n")


def update[T](drive: str, lane_id: str, change: Callable[[dict], T]) -> T:
    def one(lanes: dict[str, dict]) -> T:
        if lane_id not in lanes:
            raise Refused(f"no lane {lane_id} on drive {drive}")
        return change(lanes[lane_id])

    return transact(drive, one)


def fenced(lane: dict, token: str | None) -> None:
    """Refuse an owner's write once the lane has another owner, or none."""
    if token is None:
        return
    held = (lane.get("owner") or {}).get("token")
    if held != token:
        raise Refused(f"lane {lane['id']} is no longer yours: its owner is {held or 'none'}; end now")


def new_token() -> str:
    return secrets.token_hex(8)


def kick(drive: str) -> None:
    """Wake the supervisor's loop before its next pass is due."""
    path = drive_dir(drive) / "kick"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
