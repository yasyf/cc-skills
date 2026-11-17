"""The lane files: one JSON file per lane under lanes/<drive>/, every write made under the drive's one lock and checked against the two-state invariant before it lands."""

import fcntl
import json
import os
import re
import secrets
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

RUNNING = "running"
WAITING = "waiting"
PARKED = "parked"
STATES = (RUNNING, WAITING, PARKED)
WAIT_KINDS = ("pr_merged", "check", "staging_result", "his_reply", "release", "queue_grant", "work")
REF_KINDS = ("pr_merged", "check", "staging_result", "release", "queue_grant", "work")
LANE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
DEFAULTS = {
    "active": 12,
    "slots": 12,
    "model": "opus",
    "effort": "high",
    "posting": True,
    "pass_seconds": 30,
    "max_wait_hours": 24,
    "launch_grace_seconds": 180,
    "restarts_before_backoff": 3,
    "checkout": None,
    "queue_cli": None,
    "premerge_lanes": [],
    "staging_alerts": None,
    "staging_targets": [],
    "staging_env_file": None,
    "promotion_env": None,
    "hotfix_cli": None,
}
LIVE = "live"
NOT_LANES = ("rank", "config", "supervisor", "presence", "rulings", "queue", "view", "github", "settled")
RULINGS = "rulings.shared.json"


class Refused(Exception):
    """A verb the lane model does not allow, with the reason the caller is shown."""


def root() -> Path:
    if explicit := os.environ.get("LONG_RUNNING_STATE"):
        return Path(explicit) / "lanes"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "long-running" / "lanes"


def live(drive: str) -> bool:
    """Whether the drive runs live: its lanes/<drive>/live marker, the one switch cutover writes. Without it the supervisor starts owners in shadow and lane-land merges nothing and starts no cut."""
    return (drive_dir(drive) / LIVE).exists()


def owner_sessions() -> frozenset[str]:
    """Every session owning a lane on any lane drive of this Mac, by each lane file's owner, every fix job the supervisor started for one, and every work-queue worker: the old drive's hooks judge none of them, nor their subagents, which share the session."""
    if not root().is_dir():
        return frozenset()
    drives = [drive.name for drive in os.scandir(root()) if drive.is_dir()]
    lanes = [lane for drive in drives for lane in load_all(drive).values()]
    workers = [json.loads(row.read_text()).get("session") for drive in drives for row in (drive_dir(drive) / "work" / "workers").glob("*.json")]
    return frozenset([*(owner["session"] for lane in lanes if (owner := lane.get("owner")) and owner.get("session")), *(fix["session"] for lane in lanes for fix in lane.get("fixes", []) if fix.get("session")), *(session for session in workers if session)])


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


CONFIG_KEYS = {*DEFAULTS, "approve_cards", "budgets", "capacity", "datadog_keys", "defect_to", "gh", "gold_standard", "his_login", "hotfix_cli", "incident_worktree", "models", "premerge_lanes", "prices", "promotion_env", "queue_cli", "staging_alerts", "staging_env_file", "staging_targets", "workers", "slots"}


def set_config(drive: str, key: str, value: object, by: str) -> object:
    """Write one key of the drive config, a key the code reads, in one locked write logged to the drive's config log with who set it and what it was; the value it replaced."""
    if key not in CONFIG_KEYS:
        raise Refused(f"the code reads no config key {key!r}; keys: {', '.join(sorted(CONFIG_KEYS))}")
    path = drive_dir(drive) / "config.json"
    with locked(drive):
        kept = json.loads(path.read_text()) if path.is_file() else {}
        was = kept.get(key)
        kept[key] = value
        fresh = path.with_name(f".config.json.{os.getpid()}.tmp")
        fresh.write_text(json.dumps(kept, indent=2) + "\n")
        fresh.replace(path)
    with (drive_dir(drive) / "config.log").open("a") as log:
        log.write(json.dumps({"at": stamp(), "key": key, "was": was, "now": value, "by": by}) + "\n")
    return was


def lane_ids(drive: str) -> list[str]:
    return list(load_all(drive))


def load(drive: str, lane_id: str) -> dict:
    path = lane_path(drive, lane_id)
    if not path.is_file():
        raise Refused(f"no lane {lane_id} on drive {drive}")
    return json.loads(path.read_text())


def load_all(drive: str) -> dict[str, dict]:
    """Every lane of the drive: each `<id>.json` in its folder that reads as a lane, its own id and one of the three states; any other JSON there, such as a file another tool left, is named on stderr and skipped, so no stray file stops a pass."""
    folder = drive_dir(drive)
    found, strays = {}, []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if not LANE_ID.match(path.stem) or path.stem in NOT_LANES:
            continue
        try:
            read = json.loads(path.read_text())
        except ValueError:
            read = None
        if isinstance(read, dict) and read.get("id") == path.stem and read.get("state") in STATES:
            found[path.stem] = read
        else:
            strays.append(path.name)
    if strays:
        print(f"lane store: skipped {', '.join(strays)} in {folder}, which do not read as lanes", file=sys.stderr)
    return found


def active(lanes: dict[str, dict]) -> list[str]:
    return [lane_id for lane_id, lane in lanes.items() if lane["state"] != PARKED]


def unreplied(lane: dict) -> list[dict]:
    return [ask for ask in lane.get("asks", []) if not ask.get("replied")]


RULING_FIELDS = ("words", "at", "binds")
ASK_FIELDS = ("words", "at")


def unshaped(rows: list[dict], fields: tuple[str, ...], what: str) -> str | None:
    """Why a list of his asks or rulings would not reach an owner whole: a row lacking one of the fields its brief prints."""
    for index, row in enumerate(rows):
        if missing := [field for field in fields if not row.get(field)]:
            return f"{what} {index} with no {', '.join(missing)}: {str(row.get('words') or row)[:80]!r}"
    return None


def invalid(lane: dict) -> str | None:
    """Why a lane breaks the invariant, or None: running holds an owner and nothing else; waiting holds a wait on at least one outside fact of the closed list and a time bound; parked holds his words; neither of the two holds an owner; and each ask and ruling carries what an owner's brief prints."""
    if why := unshaped(lane.get("asks", []), ASK_FIELDS, "an ask") or unshaped(lane.get("rulings", []), RULING_FIELDS, "a ruling"):
        return f"holding {why}"
    match lane.get("state"):
        case "running":
            if not (lane.get("owner") or {}).get("token"):
                return "running with no owner"
            if lane.get("wait") is not None or lane.get("parked") is not None:
                return "running with a wait or parked words"
            return None
        case "waiting":
            wait = lane.get("wait") or {}
            if lane.get("owner") or lane.get("parked") is not None:
                return "waiting with an owner or parked words"
            if not wait.get("on"):
                return "waiting on no outside fact"
            if not wait.get("until"):
                return "waiting with no time bound"
            for event in wait["on"]:
                if event.get("kind") not in WAIT_KINDS:
                    return f"waiting on {event.get('kind')!r}, which is not an outside fact: one of {', '.join(WAIT_KINDS)}"
                if event["kind"] in REF_KINDS and not event.get("ref"):
                    return f"waiting on {event['kind']} with no ref"
            return None
        case "parked":
            if lane.get("owner") or lane.get("wait") is not None:
                return "parked with an owner or a wait"
            if not (lane.get("parked") or {}).get("words"):
                return "parked without his words"
            return None
    return f"state {lane.get('state')!r} is not running, waiting or parked"


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
        "channel": channel,
        "thread": thread,
        "pinned": False,
        "owner": None,
        "wait": {"on": [{"kind": "his_reply"}], "until": moment, "why": "opened", "set": moment, "fired": moment, "fired_why": "opened"},
        "parked": None,
        "promise": None,
        "asks": [{"words": words, "link": link, "at": moment, "ts": ts, "replied": None, "done": None}] if words else [],
        "rulings": [],
        "prs": [],
        "next": None,
        "incident": None,
        "worktree": None,
        "rank": None,
        "restarts": [],
    }


def note(lane: dict, text: str) -> None:
    """Queue a progress line for the lane's log file, which the write that carries the lane appends; nothing reads the log to decide anything."""
    lane.setdefault("_log", []).append(f"{stamp()} {text}")


def log_path(drive: str, lane_id: str) -> Path:
    return drive_dir(drive) / f"{lane_id}.log"


def log_tail(drive: str, lane_id: str, lines: int) -> list[str]:
    path = log_path(drive, lane_id)
    return path.read_text().splitlines()[-lines:] if path.is_file() else []


def shared_rulings(drive: str) -> list[dict]:
    path = drive_dir(drive) / RULINGS
    if not path.is_file():
        return []
    kept = json.loads(path.read_text())
    return kept["shared"] if isinstance(kept, dict) else kept


@contextmanager
def locked(drive: str) -> Iterator[None]:
    folder = drive_dir(drive)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        yield


def transact[T](drive: str, change: Callable[[dict[str, dict]], T], leaving_unreplied: bool = False) -> T:
    """Run change over every lane of the drive under its lock, then write each lane it changed, refusing the whole write when a changed lane breaks the invariant or leaves running while one of his asks has no reply, which only the supervisor's own failed start or backoff may do (leaving_unreplied); each state move is logged."""
    with locked(drive):
        before = load_all(drive)
        lanes = json.loads(json.dumps(before))
        result = change(lanes)
        notes = {lane_id: lane.pop("_log", []) for lane_id, lane in lanes.items()}
        changed = {lane_id: lane for lane_id, lane in lanes.items() if before.get(lane_id) != lane}
        for lane_id, lane in changed.items():
            if why := invalid(lane):
                raise Refused(f"lane {lane_id} would be left {why}")
            if (before.get(lane_id) or {}).get("state") == RUNNING and lane["state"] == WAITING and parse(lane["wait"]["until"]) <= now():
                raise Refused(f"lane {lane_id} cannot leave running on a wait whose bound {lane['wait']['until']} has passed")
            if (before.get(lane_id) or {}).get("state") == RUNNING and lane["state"] != RUNNING and not leaving_unreplied and (open_asks := unreplied(lane)):
                raise Refused(f"lane {lane_id} cannot stop running while his ask is unanswered: {open_asks[0]['words'][:120]!r}; answer it with `lane say` first")
        for lane_id, lane in changed.items():
            lane["updated"] = stamp()
            write_lane(drive, lane)
            old = before.get(lane_id) or {}
            if old.get("state") != lane["state"] or (old.get("owner") or {}).get("token") != (lane.get("owner") or {}).get("token"):
                event(drive, {"lane": lane_id, "from": old.get("state"), "to": lane["state"], "owner": (lane.get("owner") or {}).get("token"), "wait": lane.get("wait")})
        for lane_id, lines in notes.items():
            if lines:
                with log_path(drive, lane_id).open("a") as out:
                    out.write("".join(f"{line}\n" for line in lines))
        return result


def write_lane(drive: str, lane: dict) -> None:
    path = lane_path(drive, lane["id"])
    fresh = path.with_name(f".{lane['id']}.{os.getpid()}.tmp")
    fresh.write_text(json.dumps(lane, indent=1, ensure_ascii=False) + "\n")
    fresh.replace(path)
    asks = asks_path(drive, lane["id"])
    if lane["state"] == RUNNING and (waiting := unreplied(lane)):
        asks.write_text(f"{len(waiting)}\n")
    else:
        asks.unlink(missing_ok=True)
    token = token_path(drive, lane["id"])
    if owner := lane.get("owner"):
        if not token.is_file() or token.read_text().strip() != owner["token"]:
            token.write_text(owner["token"] + "\n")
    else:
        token.unlink(missing_ok=True)


def event(drive: str, row: dict) -> None:
    with (drive_dir(drive) / "events.jsonl").open("a") as out:
        out.write(json.dumps({"at": stamp(), **row}, ensure_ascii=False) + "\n")


def update[T](drive: str, lane_id: str, change: Callable[[dict], T], leaving_unreplied: bool = False) -> T:
    def one(lanes: dict[str, dict]) -> T:
        if lane_id not in lanes:
            raise Refused(f"no lane {lane_id} on drive {drive}")
        return change(lanes[lane_id])

    return transact(drive, one, leaving_unreplied)


def fenced(lane: dict, token: str | None) -> None:
    """Refuse an owner's write once the lane has another owner, or none."""
    if token is None:
        return
    held = (lane.get("owner") or {}).get("token")
    if held != token:
        raise Refused(f"lane {lane['id']} is no longer yours: its owner is {held or 'none'}; end now")


def new_token() -> str:
    return secrets.token_hex(8)


def install_version(path: Path) -> tuple[int, ...]:
    """The version of the plugin install a path sits in, as numbers, () for a checkout."""
    found = next((part for part in reversed(path.parts) if part.count(".") == 2 and part.replace(".", "").isdigit()), "")
    return tuple(int(piece) for piece in found.split(".")) if found else ()


def launcher(drive: str) -> Path:
    """The drive's stable path to the lane command, a symlink in its state folder pointing at an install's bin/lane, so launchd's plist and every owner's hooks outlive the versioned plugin folder they started from. A process moves it only forward, to its own install when that is newer than the link's, so an owner, worker or lander still running an older install never points the drive back at its own, as 1.0.1706's processes did to 1.0.1711's link."""
    path = drive_dir(drive) / "lane"
    target = Path(__file__).resolve().parents[2] / "bin" / "lane"
    if not path.is_symlink() or path.resolve() != target and install_version(target) > install_version(path.resolve()):
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = path.with_name(f".lane.{os.getpid()}")
        fresh.unlink(missing_ok=True)
        fresh.symlink_to(target)
        fresh.replace(path)
    return path


def asks_path(drive: str, lane_id: str) -> Path:
    """The mark a running owner's hook reads to show his unanswered words at its next tool call, present exactly while its lane runs with an ask of his unreplied: every lane write keeps it."""
    return drive_dir(drive) / f"{lane_id}.asks"


def note_path(drive: str, lane_id: str) -> Path:
    """The note a running owner is shown at its next tool call, once: a handover set while it runs."""
    return drive_dir(drive) / f"{lane_id}.note"


def kick(drive: str) -> None:
    """Wake the supervisor's loop before its next pass is due."""
    path = drive_dir(drive) / "kick"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
