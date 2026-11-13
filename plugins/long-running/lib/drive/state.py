import fcntl
import hashlib
import itertools
import json
import marshal
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, closing, contextmanager, suppress
from contextvars import ContextVar
from datetime import datetime, timedelta
from pathlib import Path
from typing import IO

from drive import picture, procs

ORDER_STATES = ("open", "working", "waiting", "done", "dropped")
ORDER_KINDS = ("answer", "build")
CLOSED_ORDER_STATES = ("done", "dropped")
JOB_KINDS = ("builder", "reader", "design", "codex")
REPORT_REPLY = "report_"
TURN_ENDS = ("worker_done", "escalation")
EARLIER_RETURN = "--- an earlier return, kept {at} ---"
JOB_ID = re.compile(r"j\d+")
JOB_AGENTS = {"builder": ("long-running:builder",), "reader": ("long-running:reader",), "design": ("long-running:designer",), "codex": ("long-running:builder",)}
JOB_STATES = ("running", "waiting", "built", "done", "failed", "stopped")
FINISHED_JOB_STATES = ("done", "failed", "stopped")
BRIEF_FLAGS = ("serves", "done_when", "where", "runs_as", "release", "mac", "handoff", "ruling", "native", "interactions", "would_he_ask", "plan", "incident", "scratch")
CHANGE_KINDS = ("builder", "codex")
WORK_KINDS = (*CHANGE_KINDS, "design")
INCIDENT_FIX = "fix"
INCIDENT_STOP = "stop"
INCIDENT_CHECK = "first check"
INCIDENT_CAUSE = "cause"
INCIDENT_ROLLBACK = "rollback"
INCIDENT_PARTS = {"release": [INCIDENT_CHECK], "rollback": [INCIDENT_ROLLBACK, INCIDENT_CAUSE], "none": [INCIDENT_STOP, INCIDENT_CAUSE]}
FIX_LINE = re.compile(r"^[*_`]*FIX[*_`]*:?[*_`]*[ \t]+(?P<fix>\S.*?)[ \t]*$", re.MULTILINE)
NO_FIX = "none"
UNTRACED = ("ASK", "BLOCKED", "HANDOFF")
FIX_RULE = (
    "your report ends with one line FIX <the order or PR that fixes the cause, by order id or PR link, comma-separated>,"
    " or FIX none when no fix of ours is owed: drive job report refuses it without one, and the order it names reads as a live bug's fix from then on"
)
UNPOSTED_WORK = (
    '"{part}" of {order} is work he ordered, which no post delivers: a builder or codex job on it returning DONE does, or a merged PR the'
    ' {holder} session records with drive order posted {order} <its link> --part "{part}"; when it asks only for an answer,'
    ' drive order set {order} --kind answer --part "{part}" sorts it as one'
)
IMMEDIATE_FIX = "immediate fix"
FOLLOW_UP = "follow-up"
INCIDENT_CHOICE = {
    "codex": (
        "a choice never stops it: write the ASK block to a file in your scratch, build its recommended option in the same run,"
        " and return your result with an ASK: <that file> line, which puts his card up as you return; never BLOCKED on a choice"
    ),
    "builder": (
        "a choice never stops it: keep the ASK block with drive job ask the moment the fork shows, which puts his card up, then build"
        " its recommended option in the same run and open its PR, which lands on that option while his card stays open; never BLOCKED on a choice"
    ),
}
INCIDENT_BRIEF = f'each brief\'s Incident: line carries, word for word: "{INCIDENT_CHOICE["codex"]}"'
RESULT_STATES = {"DONE": "done", "READY": "waiting", "PUSHED": "waiting", "WAITING": "waiting", "DESIGN": "waiting", "ASK": "waiting", "BLOCKED": "failed", "HANDOFF": "stopped"}
BUILT_LINE = re.compile(r"BUILT (?P<branch>[^\s,]+(?:, [^\s,]+)*?),? waits on (?P<cards>.+)")
WAITING_LINE = re.compile(r"WAITING (?P<prs>.+?) waits on (?P<cards>.+)")
ASK_LINE = re.compile(r"ASK (?P<files>[/~]\S*(?: [/~]\S*)*)")
FORK = re.compile(r"^Fork:[ \t]*([/~]\S*)", re.MULTILINE)
PUSHED_LINE = re.compile(r"PUSHED\b")
POSTED_LINE = re.compile(r"POSTED (?P<link>https://[^\s;]+)")
REPORTED_LINE = re.compile(r"(?:READY|PUSHED)\b")
REPORT_CHANGE = re.compile(r"^CHANGE:[ \t]*(?P<change>.+?)[ \t]*$", re.MULTILINE)
CHANGE_LINE = re.compile(r"^Change:[ \t]*(?P<change>.+?)(?:[ \t]*\(waits on (?P<waits>[^()]*)\))?[ \t]*$", re.MULTILINE)
CHANGE_WAITS = re.compile(r"\bchange (\d+)")
BUILT_CARD = re.compile(r"https://[^\s,]+/archives/[^\s,]+/p\d+[^\s,]*|[\w.-]+:j\d+-\d+[\w-]*")
FLAKE_FIX = "flake fix"
EXEMPT_CLASSES = ("pure port", "test-only", "ci-only", FLAKE_FIX, "incident fix")
FLAKE_RULING = "532cd3d"
FLAKE_WORDS = "and ffs fix these flaky tgests how many times do I have to tell yoiu"
TAP_LABELS = ("Approve", "Hold")
TOOLING = "Drive tooling:"
POST_FEEDBACK = f"{TOOLING} post feedback:"
DRIVE_TOOLING = (
    "how the drive itself works: a tool it runs on (the drive CLI and its MCP tools, a hook, the watch, the dashboard,"
    " cards and posts, cc-slack check-post, the drive's CI plumbing), this skill, its agents and role rules, how its"
    " sessions reach each other and him, and the install of a released tool"
)
ABOUT_DRIVE = "drive"
ABOUT_HIS_WORK = "his work"
ABOUTS = (ABOUT_DRIVE, ABOUT_HIS_WORK)
GUARDED_OWNER = "forge-ai"
HIS_LOGIN = "yasyf"
NO_TAP_OWNER = HIS_LOGIN
FOREIGN_RULING = '5815316 on a teammate\'s PRs: "Don\'t touch old tree or his PRs"'
MONOREPO = "Forge-AI/monorepo"
ROLES = ("communicator", "coordinator", "meta")
HOLDERS = ("communicator", "meta")
HANDLER_ENV = "LONG_RUNNING_HANDLER"
DRIVE_ENV = "CLAUDE_LONG_RUNNING_DRIVE"
OWN_JOB_ENV = "LONG_RUNNING_OWN_JOB"
REMOTE_LANE_ENV = "LONG_RUNNING_REMOTE_LANE"
LANE_ROLES = {"communicator": "communicator", "root": "coordinator", "meta": "meta"}
DRIVE_LANES = ("root", "communicator", "coordinator", "meta", "handler", "builder", "reader", "designer", "root-tooling", "root-skill", "root-dashboard", "watch")
NUMBERED_LANE = re.compile(r"(?P<stem>.+)-\d+")
LOCAL_ROUTE = "local"
DISPATCH_PREFIX = "ctx_"

PR_URL = re.compile(r"(?:https?://)?github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
MERGE_COMMIT = re.compile(r"https://github\.com/(?P<repo>[^/\s]+/[^/\s]+)/commit/[0-9a-f]{7,40}")
RELEASE_WAIT = re.compile(r"release:(?P<service>[\w.-]+):(?P<pr>[^/\s#]+/[^/\s#]+#\d+)")
SLOT_WAIT = "builder-slot"
PR_KEY = re.compile(r"([^/\s#]+)/([^/\s#]+)#(\d+)")
DATADOG_MONITOR = re.compile(r"https://(?:[\w-]+\.)*datadoghq\.(?:com|eu)/monitors/(\d+)(?:[/?#]\S*)?")
SENTRY_ISSUE = re.compile(r"https://(?:[\w-]+\.)*sentry\.io/(?:organizations/[\w-]+/)?issues/[\w-]+(?:[/?#]\S*)?")
KEPT_ASK = re.compile(r"(?P<job>[\w.-]+)-(?P<hash>[0-9a-f]{8})")
PR_REF = re.compile(rf"{PR_URL.pattern}|{PR_KEY.pattern}|#(\d+)\b")
CARD_LINK = re.compile(r"/archives/([A-Z0-9]+)/p(\d{10})(\d{6})(?:\?thread_ts=([\d.]+))?")
SLACK_SOURCE = re.compile(r"slack:([A-Z0-9]+)/(\d+\.\d+)(?:/(\d+\.\d+))?")
OFFSET = re.compile(r"\+(\d+)([mhd])")
CLOCK = re.compile(r"(\d{1,2}):(\d{2})(?:\s*([AaPp])\.?[Mm]\.?)?")
OFFSET_UNITS = {"m": "minutes", "h": "hours", "d": "days"}
AWAY_AFTER = timedelta(minutes=60)
PART_CHARS = 80
REBUILT_ASKS = ".rebuilt"
NOT_LANE = re.compile(r"[^a-z0-9]+")
WAITS_ID = re.compile(r"([\w.-]+):(j\d+)-(\d+)")
ORDER_LIST = re.compile(r"[\s,]+")
PART_ITEMS = re.compile(r"\s*;\s*(?=o\d+:)")
ORDER_PART = re.compile(r"(o\d+):\s*(.+)", re.DOTALL)
ROUTED_KINDS = ("owner", "answer", "handoff", "refused")
APPROVALS_LABELLED = ".approvals-labelled-by-question"
PR_JOB_INDEX: ContextVar[tuple[dict, dict[str, list[str]]] | None] = ContextVar("pr_job_index", default=None)
BATCHED: dict[str, bytes] = {}
WHOLE_READS: dict[tuple[Path, int], tuple[sqlite3.Connection, int, bytes]] = {}
READING = threading.Lock()
MOVED_REPORTS: set[Path] = set()
LOCK_POLL_SECONDS = 0.1
HOLDER_CHARS = 200
HOLDERS_SECONDS = 5
DRIVE_LOCK_SECONDS = 300
SLOW_HOLD_SECONDS = 2
AFTER_SAVE: dict[str, list[Callable[[], list[str]]]] = {}
WROTE: set[str] = set()
READ_ONLY = {
    "tmux": ("show-environment", "list-clients"),
    "orca": ("orchestration task-list", "orchestration worker-show", "orchestration request-show", "terminal show", "repo show", "diagnostics memory"),
    "cc-slack": ("thread", "history", "users", "check-post", "card render"),
    "cci": ("show", "grep", "find", "digest", "state"),
    "gh": ("pr view", "pr list", "pr checks", "pr diff", "run view", "run list", "repo view"),
}
GH_FIELDS = {"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"}
DATABASE = "drive.db"
BACKUP = "drive.json.before-sqlite"
MOVED = "moved_to"
MOVED_SAVER = "999999.0.0"
SCHEMA = "1"
COLLECTIONS = ("orders", "jobs", "prs", "answers", "handlers")
BINDINGS = ("driver", "roles", "subcoordinators", "handlers")
TABLES = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE rows (collection TEXT NOT NULL, key TEXT NOT NULL, place INTEGER NOT NULL, body TEXT, PRIMARY KEY (collection, key)) WITHOUT ROWID;
"""
VERSION = json.loads((Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json").read_text())["version"]


class DriveError(Exception):
    pass


class NewerDrive(Exception):
    pass


def reads_only(argv: list[str]) -> bool:
    """Whether a command the drive starts only reads, so a verb run again on the newest install after starting it changes nothing twice: git but a push, lsof, ps, a gh api call with no field or method, or a command READ_ONLY starts with, a gh one through ccx's GitHub gateway as itself."""
    program, words = Path(argv[0]).name, argv[1:]
    if program == "ccx" and words[:2] == ["vcs", "gh"]:
        program, words = "gh", words[words.index("--") + 1 :]
    if program == "git":
        return "push" not in words
    if program == "gh" and words[:1] == ["api"]:
        return not GH_FIELDS & set(words)
    return program in ("lsof", "ps") or any(words[: len(form.split())] == form.split() for form in READ_ONLY.get(program, ()))


def outside(event: str, args: tuple) -> None:
    """Keep in WROTE each command this process starts that does more than read, such as a Slack post, a cci record or an Orca send, which a run of its verb again would do twice."""
    if event == "subprocess.Popen" and not reads_only([*args[1]]):
        WROTE.add(Path(args[1][0]).name)


sys.addaudithook(outside)


def root() -> Path:
    if explicit := os.environ.get("LONG_RUNNING_STATE"):
        return Path(explicit)
    if xdg := os.environ.get("XDG_STATE_HOME"):
        return Path(xdg) / "long-running"
    return Path.home() / ".local" / "state" / "long-running"


def scratch() -> bool:
    """Whether LONG_RUNNING_STATE moves the state root, as a proof's or a test's drive does; no real drive sets it."""
    return bool(os.environ.get("LONG_RUNNING_STATE"))


def drive_dir(name: str) -> Path:
    return root() / name


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def detach(name: str, args: list[str], log: str) -> int:
    """Run the drive CLI with args on the drive as its own process, outliving the watch pass or tap that started it, its output appended to the drive's log of that name; its environment names the drive, which lets the drive's sessions end it unasked once it outlives its use."""
    env = {variable: value for variable, value in os.environ.items() if variable != "CLAUDE_CODE_SESSION_ID"} | {DRIVE_ENV: name}
    with (drive_dir(name) / log).open("a") as out:
        return subprocess.Popen(
            [str(Path(__file__).resolve().parents[2] / "bin" / "drive"), *args, "--drive", name],
            cwd=drive_dir(name),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ).pid


def state_path(name: str) -> Path:
    return drive_dir(name) / "drive.json"


def database_path(name: str) -> Path:
    return drive_dir(name) / DATABASE


def wal_path(name: str) -> Path:
    return drive_dir(name) / f"{DATABASE}-wal"


def backup_path(name: str) -> Path:
    return drive_dir(name) / BACKUP


def events_path(name: str) -> Path:
    return drive_dir(name) / "events.jsonl"


def project_lane(project: str) -> str:
    """The cci lane of a project's sub-coordinator: its name lowercased, each run of other characters a dash."""
    return NOT_LANE.sub("-", project.lower()).strip("-")


def alias(drive: str, role: str, project: str | None = None) -> str:
    """The cci alias a role's session, or a project's sub-coordinator's, is registered under, which names its tmux session too."""
    return f"{drive}-{project_lane(project) if project else role}"


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


def held_path(name: str, project: str | None = None) -> Path:
    return drive_dir(name) / f"watch{watch_suffix(project)}.held"


def cards_path(name: str) -> Path:
    return drive_dir(name) / "cards.json"


def replies_path(name: str, job_id: str) -> Path:
    return drive_dir(name) / "jobs" / f"{job_id}.replies.jsonl"


def proof_path(name: str, job_id: str) -> Path:
    return drive_dir(name) / "reports" / f"{job_id}.proof.txt"


def repro_path(name: str, job_id: str, suffix: str) -> Path:
    return drive_dir(name) / "repro" / f"{job_id}{suffix}"


def returns(name: str, job_id: str) -> list[dict]:
    """Every return the job's history keeps, newest first, an identical body once, each its row with its body, or its subject when a lane sent none: each `drive job report` took, each codex reply and proof, and each result a remote lane sent, as its worker_done or as a question opening with a result, never the message ending the turn of a return `drive job report` kept."""
    kept = replies(name, job_id)
    ends = turn_ends(kept)
    taken = [
        reply | {"body": reply["body"] or reply["subject"]}
        for reply in kept
        if reply["id"] not in ends and (reply["type"] == "worker_done" or reply["type"] == "question" and reported(opening(reply["body"] or "")))
    ]
    once: dict[str, dict] = {}
    for reply in sorted(reversed(taken), key=lambda reply: parse_time(reply["created_at"]), reverse=True):
        once.setdefault(reply["body"].strip(), reply)
    return list(once.values())


def turn_ends(kept: list[dict]) -> set[str]:
    """The id of each lane message in a job's history that ends the turn of a return `drive job report` kept, whatever its body: the first worker_done or escalation after that return, which carries it and adds none of its own."""
    ends, open_return = set(), False
    for reply in kept:
        if reply["id"].startswith(REPORT_REPLY):
            open_return = True
        elif open_return and reply["type"] in TURN_ENDS:
            ends.add(reply["id"])
            open_return = False
    return ends


def opening(text: str) -> str:
    """The text's first line that is not blank, trimmed."""
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def report_text(name: str, job_id: str) -> str:
    """A job's report, which is every return it took, newest first: the newest whole, so its first line is the newest return's, then each earlier one under a line naming when the drive kept it; nothing for a job that returned nothing."""
    taken = returns(name, job_id)
    if not taken:
        return ""
    earlier = (f"\n{EARLIER_RETURN.format(at=stamp(parse_time(reply['created_at']).astimezone()))}\n{reply['body'].strip()}\n" for reply in taken[1:])
    return "".join([taken[0]["body"].strip() + "\n", *earlier])


def keep_report(name: str, job_id: str, line: str, whole: str, at: datetime | None = None) -> None:
    """Keep a return in the job's history, beside each earlier one and each message a remote lane sent, which every reader takes as the job's report, so a later return, its own agent's or anyone else's such as a coordinator restating its result, never takes the place of an earlier one."""
    keep_reply(name, job_id, {"created_at": stamp(at), "id": f"{REPORT_REPLY}{uuid.uuid4().hex[:12]}", "type": "worker_done", "subject": line, "body": whole})


def moved_reports(name: str) -> None:
    """Move into each job's history, as the return it was, what an older install kept beside it as reports/<j>.md, its report until the history became it, and what a remote lane's job holds as its latest report when that is a result the history lacks, then delete the file, so the history is every reader's one record of a job's returns: once a process, under the drive's lock, and only while such a file is left."""
    folder = drive_dir(name) / "reports"
    if folder in MOVED_REPORTS:
        return
    MOVED_REPORTS.add(folder)
    files = [path for path in folder.glob("j*.md") if JOB_ID.fullmatch(path.stem)]
    if not files:
        return
    jobs = load(name)["jobs"]
    for path in files:
        if path.stem in jobs:
            keep_return(name, path.stem, path.read_text(), datetime.fromtimestamp(path.stat().st_mtime).astimezone())
        path.unlink()
    for job_id, job in jobs.items():
        if reported(opening(job.get("report") or "")):
            keep_return(name, job_id, job["report"], parse_time(job["returned"] or job["updated"]))


def keep_return(name: str, job_id: str, whole: str, at: datetime) -> None:
    """Keep a return an older install held outside the job's history in it, unless it is blank or a row of the history already holds it."""
    if not whole.strip() or whole.strip() in {(reply["body"] or "").strip() for reply in replies(name, job_id)}:
        return
    keep_report(name, job_id, opening(whole), whole, at)


def keep_reply(name: str, job_id: str, message: dict) -> None:
    """Append one message a remote lane sent, or one return a job on this Mac kept, to its job's history, which a later result never replaces."""
    path = replies_path(name, job_id)
    path.parent.mkdir(exist_ok=True)
    fields = ("created_at", "id", "type", "subject", "body")
    with open(path, "a") as handle:
        handle.write(json.dumps({field: message[field] for field in fields}) + "\n")


def replies(name: str, job_id: str) -> list[dict]:
    """The job's kept messages and returns, oldest first, each once: a watch stopped between keeping a message and saving its cursor keeps it again."""
    path = replies_path(name, job_id)
    kept = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return list({reply["id"]: reply for reply in kept}.values())


def names() -> list[str]:
    base = root()
    if not base.is_dir():
        return []
    return sorted(entry.name for entry in base.iterdir() if (entry / DATABASE).is_file() or (entry / "drive.json").is_file())


def exists(name: str) -> bool:
    return database_path(name).is_file() or state_path(name).is_file()


def now() -> datetime:
    return datetime.now().astimezone()


def stamp(moment: datetime | None = None) -> str:
    return (moment or now()).isoformat(timespec="seconds")


def parse_time(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.astimezone()


def parse_due(text: str, reference: datetime | None = None) -> datetime:
    """Parse a due time: "HH:MM" or the "3:30 AM" the drive prints (today, or tomorrow once passed), "+30m"/"+2h"/"+1d", or ISO 8601."""
    base = reference or now()
    if offset := OFFSET.fullmatch(text.strip()):
        return (base + timedelta(**{OFFSET_UNITS[offset[2]]: int(offset[1])})).replace(microsecond=0)
    if clock := CLOCK.fullmatch(text.strip()):
        hour = int(clock[1]) % 12 + (12 if clock[3].upper() == "P" else 0) if clock[3] else int(clock[1])
        due = base.replace(hour=hour, minute=int(clock[2]), second=0, microsecond=0)
        return due if due > base else due + timedelta(days=1)
    try:
        return parse_time(text.strip())
    except ValueError:
        raise DriveError(f"cannot read due time {text!r}: use HH:MM, 3:30 AM, +30m, +2h or ISO 8601") from None


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
        "handlers": {},
        "slack": {"channel": channel, "page": None, "watch": []},
        "owner": {"heard": None, "away": False},
        "orders": {},
        "jobs": {},
        "prs": {},
        "answers": {},
        "stand_ins": {},
        "held": {},
        "carried": {},
        "catch_up": None,
        "launched": {},
        "closed_from_cards": True,
        "designs_cited": True,
    }


def saved(name: str) -> dict:
    """The sessions bound to a drive as saved, read alone from its database: each role's, each sub-coordinator's and each handler's, one saved with a single driver session reading as that session coordinating."""
    return bound(stored(name, BINDINGS))


def saved_job(name: str, job_id: str) -> dict | None:
    """One job as the drive saved it, its row alone read from the database and not brought up to this install's fields, so a hook asking after one job reads none of the rest; None for no such job."""
    if not (path := database_path(name)).exists():
        return stored_json(name)["jobs"].get(job_id)
    with closing(connect(path)) as db:
        found = db.execute("SELECT body FROM rows WHERE collection = 'jobs' AND key = ?", (job_id,)).fetchone()
    return found and json.loads(found[0])


def stored(name: str, fields: tuple[str, ...] | None = None) -> dict:
    """The drive as saved, its saved_by the install that saved it last, or only these top-level fields of it, read in one transaction; a drive no install moved to its database yet read from its drive.json whole."""
    if not (path := database_path(name)).exists():
        return stored_json(name)
    return rows_read(path, fields)


def whole(path: Path, shape: Callable[[dict], tuple[dict, bool]]) -> dict:
    """The drive's whole saved state as shape brings it up to this install's fields, read and shaped again only once another connection committed since this process last read it, as PRAGMA data_version on a connection of this process's own that never writes says, else a fresh copy of what it shaped last, so a verb's many loads parse and shape the drive's rows once; a shape that says it read something outside the database that may change with no commit is never kept. Shaping runs outside the lock, since it may read the drive itself."""
    key = (path, path.stat().st_ino)
    with READING:
        watcher, version, kept = WHOLE_READS.get(key) or (sqlite3.connect(path, check_same_thread=False, isolation_level=None), None, b"")
        if (now := watcher.execute("PRAGMA data_version").fetchone()[0]) == version:
            return marshal.loads(kept)
    data, keep = shape(rows_read(path, None))
    if keep:
        with READING:
            WHOLE_READS[key] = (watcher, now, marshal.dumps(data))
    return data


def rows_read(path: Path, fields: tuple[str, ...] | None) -> dict:
    """The drive's rows, or those of these top-level fields, read in one transaction and built back into the drive."""
    named = "" if fields is None else f" WHERE collection = '' AND key IN ({', '.join('?' * len(fields))}) OR collection IN ({', '.join('?' * len(fields))})"
    with closing(connect(path)) as db:
        db.execute("BEGIN")
        rows = db.execute(f"SELECT collection, place, key, body FROM rows{named}", (fields or ()) * 2).fetchall()
        saver = db.execute("SELECT value FROM meta WHERE key = 'saved_by'").fetchone()
        db.execute("COMMIT")
    data: dict = {}
    for collection, _, key, body in sorted(rows, key=lambda row: row[:2]):
        if collection:
            data[collection][key] = json.loads(body)
        else:
            data[key] = {} if body is None else json.loads(body)
    return data | ({"saved_by": saver[0]} if saver else {})


def stored_json(name: str) -> dict:
    """The drive as its drive.json holds it; once moved to its database, which drive.json then only points to, the database's, or the copy of drive.json kept as it moved while the database is not in place yet."""
    try:
        data = json.loads(state_path(name).read_text())
    except FileNotFoundError:
        raise DriveError(f"no drive named {name}") from None
    if MOVED not in data:
        return data
    return stored(name) if database_path(name).exists() else json.loads(backup_path(name).read_text())


def saved_by(name: str) -> str | None:
    """The install that saved the drive last, read alone."""
    if not (path := database_path(name)).exists():
        return stored_json(name).get("saved_by")
    with closing(connect(path)) as db:
        found = db.execute("SELECT value FROM meta WHERE key = 'saved_by'").fetchone()
    return found and found[0]


def connect(path: Path) -> sqlite3.Connection:
    """A connection to a drive's database that begins each transaction itself, waiting up to DRIVE_LOCK_SECONDS for another's write."""
    db = sqlite3.connect(path, timeout=DRIVE_LOCK_SECONDS, isolation_level=None)
    db.execute("PRAGMA synchronous = NORMAL")
    return db


def bound(data: dict) -> dict:
    """The drive's bindings brought up to the fields this install reads."""
    if "driver" in data:
        data["roles"] = {"coordinator": data.pop("driver")}
    data.setdefault("subcoordinators", {})
    data.setdefault("handlers", {})
    data.setdefault("launched", {})
    data.setdefault("terminal", None)
    data.pop("run", None)
    for handler in data["handlers"].values():
        handler.setdefault("holds", None)
        handler["handed"] = [*handler.get("handed", []), *handler.pop("held_back", [])]
        handler.setdefault("took", [])
        handler.setdefault("posted", [])
    for held in data["roles"].values():
        held.setdefault("terminal", None)
    return data


def load(name: str) -> dict:
    """Read a drive, bringing a drive saved by an older install up to the fields this one reads, from what is on this Mac alone; one a newer install saved is refused before any of it is read, since this code may misread its shapes. Inside a batch, the batch's copy as its writes so far left it."""
    if name in BATCHED:
        return marshal.loads(BATCHED[name])
    if not (path := database_path(name)).exists():
        return shaped(name, stored_json(name))[0]
    return refuse_newer(name, whole(path, lambda saved: shaped(name, saved)))


def refuse_newer(name: str, data: dict) -> dict:
    if (saver := data.get("saved_by")) and version_order(saver) > version_order(VERSION):
        raise NewerDrive(f"the {name} drive was saved by long-running {saver}, newer than this {VERSION}, which may misread it: run the newest install's drive")
    return data


def shaped(name: str, saved: dict) -> tuple[dict, bool]:
    """The drive as saved brought up to the fields this install reads, refused when a newer install saved it, and whether shaping it read only the database: true once every once-only pass over files on this Mac had run before this shaping, the cards closed included."""
    data = refuse_newer(name, bound(saved))
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
        order.setdefault("title", None)
        order.setdefault("kind", None)
        order.setdefault("complex", False)
        order.setdefault("options_only", False)
        order.setdefault("once", False)
        order.pop("releases", None)
        order.pop("shipped", None)
        order.setdefault("proof", None)
        order.setdefault("close_report", None)
        order.setdefault("closed_on", None)
        order.setdefault("alerts", [])
        order.setdefault("recovered", None)
        if order.pop("fixed", None) and order["recovered"] and order["state"] not in CLOSED_ORDER_STATES:
            order.update(recovered=None, proof=None)
        order.setdefault("quiet", None)
        if order["quiet"] and "news" not in order["quiet"]:
            order["quiet"] = {"record": order["quiet"]["record"], "at": order["quiet"]["at"], "handler": order["quiet"]["handler"], "heard": None, "news": {}}
        order.setdefault("reporter", None)
        order.setdefault("watch", None)
        order["owner"] = "meta" if order["text"].startswith(TOOLING) else order.get("owner")
        if "from_thread" in order or isinstance(order.get("thread", ""), str):
            order["thread"] = saved_thread(data, order)
        order["parts"] = {part.strip(): held for part, held in order["parts"].items()}
        for part in order["parts"].values():
            part.pop("kind", None)
            part.pop("after", None)
            part.setdefault("design", None)
            part.setdefault("answer", False)
            part.setdefault("orphan_gap", None)
    mail = data.pop("lane_mail", {})
    for job in data["jobs"].values():
        job["mail_seen"] = max(job.get("mail_seen", 0), mail.get(job.get("run"), 0))
        job.setdefault("where", "remote" if job.get("dispatch") else "local")
        job.setdefault("returned", job["updated"])
        if job.pop("checks", None):
            job["parts"] = []
        if job.get("ask") and any(reported(text.lstrip()) for text in (job["said"] or "", job["report"] or "")):
            job["ask"] = None
        if job.get("ask"):
            job.setdefault("ask_at", job["updated"])
        if (job.get("agent") or "").startswith(DISPATCH_PREFIX):
            job.update(local_lane(job["agent"]), agent=None)
        if "questions" not in job:
            asked, card = job.get("question"), job.get("card")
            job["questions"] = [new_question(asked, job["said"], job["updated"], job.get("unsettled"), card)] if asked or card else []
        for field, blank in (new_job(None, None, None, [], None) | {"session": None}).items():
            job.setdefault(field, blank)
        job["parts"] = [part.strip() for part in job["parts"]]
        job["carries"] = {order_id: [part.strip() for part in parts] for order_id, parts in job["carries"].items()}
        if job["kind"] == "reader" and job["state"] in FINISHED_JOB_STATES:
            job["undelivered"] = []
        for kept in job["undelivered"]:
            kept.setdefault("request", str(uuid.uuid5(uuid.NAMESPACE_OID, f"{job['created']} {kept['seq']} {kept['at']}")))
        if flags := job.get("brief_flags"):
            if "handoff" not in flags:
                flags["handoff"] = briefed_handoff(job.get("brief"))
            job["brief_flags"] = {flag: [] if flag == "ruling" else None for flag in BRIEF_FLAGS} | flags
    for order_id, order in data["orders"].items():
        if "joins" not in order:
            carriers = (data["jobs"][job_id]["order"] for job_id in order["jobs"]) if order["incident"] else ()
            order["joins"] = next((other for other in carriers if other != order_id and data["orders"][other]["incident"]), None)
        if merge_proven(order):
            order.update(proof=None, close_report=None, closed_on=None)
            if order["reporter"] and not order["reporter"]["told"]:
                order["reporter"]["tell_said"] = None
    for pr in data["prs"].values():
        waits = pr.get("waits")
        pr["waits"] = waits if isinstance(waits, list) else [waits] if waits else []
        pr.setdefault("conflict", False)
        pr.setdefault("failed_checks", [])
        pr.setdefault("hold_label", False)
        for gone in ("ready_said", "stuck", "unchecked"):
            pr.pop(gone, None)
        pr.setdefault("stage", None)
        pr.setdefault("card", None)
        pr.setdefault("added", None)
        pr.setdefault("native", [])
        pr.setdefault("reviews_required", 1)
        pr.setdefault("base", None)
        pr.setdefault("branch", None)
        pr.setdefault("lander", None)
        pr.setdefault("checker", None)
        if pr["checker"]:
            pr["checker"].setdefault("against", "")
        pr.setdefault("ejected", False)
        pr.setdefault("flaky", None)
        pr.setdefault("review_asked", None)
        pr.setdefault("author", None)
        pr.setdefault("checked", None)
        pr.setdefault("undrawn", None)
        pr.setdefault("design", None)
        pr.setdefault("answers", None)
        if stamp := pr["checked"]:
            stamp.pop("patch", None)
            stamp.setdefault("lines", {})
            stamp.setdefault("read", stamp["head"])
        pr["parts"] = [part.strip() for part in pr.get("parts", [])]
        pr.setdefault("required", None)
        pr.setdefault("lead", None)
        if (verdict := pr.pop("verdict", None)) and verdict.get("answer") and not verdict["ready"]:
            pr["rework"] = verdict
        if (rework := pr.get("rework")) and "answer" in rework:
            pr["rework"] = {"by": rework["answer"], "words": rework["why"], "at": rework["at"]} if rework["head"] == pr["head"] else None
        pr.setdefault("rework", None)
        if pr.get("fundamental"):
            pr["tap"] = None
        if tap := pr["tap"]:
            pr["tap"] = new_tap(tap["label"], tap["head"], tap["source"], tap.get("action_ts"), tap.get("via"), tap["at"])
    data.setdefault("answers", {})
    for answer in data["answers"].values():
        answer.setdefault("jobs", {})
    data.setdefault("stand_ins", {})
    data.setdefault("held", {})
    data.setdefault("carried", {})
    if "catch_up" not in data:
        data["catch_up"] = new_catch_up(min(mark["at"] for mark in data["held"].values()), [mark["order"] for mark in data["held"].values() if mark["order"]]) if data["held"] else None
    passes = (drive_dir(name) / "pictures" / picture.SOURCED, drive_dir(name) / "asks" / REBUILT_ASKS, drive_dir(name) / APPROVALS_LABELLED)
    keep = all(mark.exists() for mark in passes) and bool(data.get("closed_from_cards")) and bool(data.get("designs_cited"))
    if (pictures := drive_dir(name) / "pictures").is_dir() and not (pictures / picture.SOURCED).exists():
        picture.fill(pictures, [*map(Path.read_text, sorted((drive_dir(name) / "asks").glob("*.md"))), *pr_bodies(name)])
    if cards_path(name).exists() and not (drive_dir(name) / "asks" / REBUILT_ASKS).exists():
        rebuild_asks(name, data)
    if "coordinator" in data["roles"] and not (drive_dir(name) / APPROVALS_LABELLED).exists():
        label_approvals(name, data["roles"]["coordinator"]["cwd"])
    for flag in ("may_settle_per_answer", "may_settle_skips_approvals", "may_settle_by_subject", "may_settle_by_jev"):
        data.pop(flag, None)
    for job in data["jobs"].values():
        job.pop("may_settle", None)
        for question in job["questions"]:
            question.setdefault("refused", None)
            if "settled" in question:
                settled = question.pop("settled")
                question["closed"] = settled and new_close("answered", None, None, seq=settled.get("seq"), answer=settled.get("answer"), ruling=settled.get("ruling"), at=settled["at"])
            question.setdefault("closed", None)
            question.setdefault("holds", "")
            question.setdefault("replaced", None)
            question.setdefault("recorded", [])
            question["waits"] = question["waits"] and question["waits"].replace("-#", "-")
            if question["waits"] and not question["file"] and (kept := rebuilt_ask(name, question["waits"])).exists():
                question["file"] = str(kept)
    if not data.get("closed_from_cards"):
        finish_asked_readers(name, data)
        close_from_cards(name, data)
    if not data.get("designs_cited"):
        cite_returned_designs(name, data)
    if unkept := any("threads" not in order for order in data["orders"].values()):
        keep_threads(name, data)
    return data, keep and not unkept


def briefed_handoff(path: str | None) -> str | None:
    """The Handoff: line of a brief drive job brief wrote before it kept a handoff in brief_flags, so the job's next re-brief keeps it, a finished job's included, since drive job resume re-briefs a job in any state."""
    from drive import brief

    if not (path and Path(path).exists()):
        return None
    return next((value.strip() for label, value in brief.fields(Path(path).read_text()) if label == "Handoff"), None)


def keep_threads(name: str, data: dict) -> None:
    """Keep on each order an older install saved the threads it read apart: each a post naming it went in, each card its jobs drew, and each PR card drawn at his DM root for a PR it serves, read once from the dashboard's said events."""
    for order in data["orders"].values():
        order.setdefault("threads", [])
        for where in order.pop("asked_in", []):
            join_thread(order, (where[0], where[1]), where[2] if len(where) == 3 else order["last_line"] or order["created"], "posted")
        for job_id in order["jobs"]:
            for question in data["jobs"][job_id]["questions"]:
                if question["card"] and (where := link_thread(question["card"])):
                    join_thread(order, where, order["created"], "question")
    for event, _ in read_events(name):
        if event["kind"] == "said" and event.get("card") and (keys := [key for key in event.get("prs") or [] if key in data["prs"]]):
            join_pr_cards(data, keys, [event["card"]], event["at"])


def cite_returned_designs(name: str, data: dict) -> None:
    """Cite on each open PR citing no design the DESIGN: lines of the newest READY or PUSHED return of its job that names it and carries any, as cite_designs does at each return, since the check reads a PR's design answers only from what the PR cites; marked so this runs once."""
    for key, pr in data["prs"].items():
        if pr["state"] != "open" or pr["design"]:
            continue
        named = (cited for reply in returns(name, pr["job"]) if REPORTED_LINE.match(said := opening(reply["body"])) and key in named_prs(data, pr["order"], said) and (cited := design_lines(reply["body"])))
        pr["design"] = next(named, None)
    data["designs_cited"] = True


def finish_asked_readers(name: str, data: dict) -> None:
    """Set back to done each reader that returned DESIGN and was set waiting when drive job ask kept its question, since a reader's question waits on his card, never the reader, and its builder's brief carries his answer."""
    for job_id, job in data["jobs"].items():
        if job["kind"] == "reader" and job["state"] == "waiting" and report_text(name, job_id).startswith("DESIGN"):
            job["state"] = "done"


def close_from_cards(name: str, data: dict) -> None:
    """Close each drawn question whose card cc-slack's stored cards no longer list open, answered or withdrawn as its card shows, since until the question decided its card the card store held whether it was open; marked so this runs once, from cards read live."""
    from drive import view

    found, source = view.stored_cards(name)
    if source not in ("live", view.UNREAD):
        return
    for job in data["jobs"].values() if source == "live" else ():
        for question in job["questions"]:
            if question["closed"] or not (drawn := question["card"] or question["waits"]) or view.card_open(drawn, found):
                continue
            answer = (view.closed_card(found, drawn) or {}).get("answer") or {}
            question["closed"] = new_close("withdrawn" if answer.get("withdrawn") else "answered", answer.get("label"), None, at=answer.get("at") or job["updated"])
    data["closed_from_cards"] = True


def rebuilt_ask(name: str, waits: str) -> Path:
    return drive_dir(name) / "asks" / f"{waits.partition(':')[2]}.md"


def ask_hash(text: str) -> str:
    """What `drive job ask` names a kept ASK file by, after its job: the hash of its words, so a file whose words no longer hash to its name changed since."""
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def label_approvals(name: str, cwd: str) -> None:
    """Label as approvals the taps on PR cards his answers in the coordinator's checkout recorded before `drive answer` labelled them, so the settled check never counts one; marked so this runs once."""
    from drive import settled

    settled.backfill(cwd)
    (drive_dir(name) / APPROVALS_LABELLED).touch()


def rebuild_asks(name: str, data: dict) -> None:
    """Keep an ASK file, rebuilt from the card the drive stored, for each of his open question cards whose job's question keeps none, so `drive cards redraw` redraws it; marked so this runs once, from cards read live."""
    from drive import limits

    if (stored_cards := json.loads(cards_path(name).read_text()))["source"] != "live":
        return
    unkept = {question["waits"] for job in data["jobs"].values() for question in job["questions"] if question["waits"] and not question["file"]}
    (asks := drive_dir(name) / "asks").mkdir(exist_ok=True)
    for stored in stored_cards["questions"]:
        if (waits := stored["card"].get("waits")) in unkept and not (kept := rebuilt_ask(name, waits)).exists():
            kept.write_text(limits.rebuilt(stored["card"], asks))
    (asks / REBUILT_ASKS).touch()


def pr_bodies(name: str) -> list[str]:
    if not cards_path(name).exists():
        return []
    return [row.get("description") or "" for card in json.loads(cards_path(name).read_text())["pr_cards"] for row in card["card"].get("prs") or []]


def saved_thread(data: dict, order: dict) -> list[str] | None:
    """The channel and thread the lines of an order an older install saved go in, read as that install read its bare thread ts: in the channel of the message the order came from when the ts is that message or the thread it is in, else in his DM, where the thread its posts in his DM hang from stands in for its own post in a channel neither his DM nor one the drive watches."""
    dm, thread, from_thread = data["slack"]["channel"], order.get("thread"), order.pop("from_thread", None)
    posts = [(link[1], f"{link[2]}.{link[3]}", link[4]) for part in order["parts"].values() if (link := CARD_LINK.search(part["posted"] or ""))]
    if any(ts == thread and channel not in {dm, *data["slack"].get("watch", [])} for channel, ts, _ in posts):
        thread = next((parent or ts for channel, ts, parent in posts if channel == dm), None)
    source = SLACK_SOURCE.fullmatch(order.get("from", ""))
    thread = thread or (source and source[2])
    return [source[1] if source and thread in (source[2], from_thread) else dm, thread] if thread else None


def job_parts(job: dict, order_id: str) -> list[str]:
    """The parts of an order a job delivers: its own order's, or those it carries for another."""
    return job["parts"] if job["order"] == order_id else job["carries"].get(order_id, [])


def carry(data: dict, job_id: str, order_id: str, parts: list[str]) -> None:
    job, order = data["jobs"][job_id], data["orders"][order_id]
    job["carries"][order_id] = list(dict.fromkeys([*job["carries"].get(order_id, []), *parts]))
    if job_id not in order["jobs"]:
        order["jobs"].append(job_id)
    for question in job["questions"]:
        if question["card"]:
            join_card(data, job_id, question["card"])
    order["updated"] = stamp()


def agent_job(data: dict, agent: str | None) -> str | None:
    return next((job_id for job_id, job in data["jobs"].items() if agent and job.get("agent") == agent), None)


def resumed(job: dict) -> str | None:
    """When a prompt, such as a SendMessage, woke the agent of a job still running on after its last kept return; None once its next return is kept, or for a job no prompt woke since it returned."""
    woke = job.get("resumed")
    return woke if woke and job["state"] == "running" and parse_time(woke) > parse_time(job["returned"]) else None


def start(job: dict) -> None:
    """Run the job a worker started on, a stopped one included, its turn for a builder slot on this Mac over, so the drive keeps the return of each worker it lets start."""
    job.update(state="running", waits=[wait for wait in job["waits"] if wait != SLOT_WAIT], updated=stamp())


def hold_unstarted(job: dict) -> None:
    """A job no agent or lane has started waits, rather than runs, while anything it waits on holds it, so the watch reads it waiting, never as not started, and says FREE once the last wait ends."""
    if job["state"] == "running" and job["waits"] and not job.get("agent") and not job.get("dispatch"):
        job.update(state="waiting", said=f"waits on {', '.join(job['waits'])}")


def unstarted_builder(data: dict, job: dict) -> bool:
    """Whether a job is a builder no agent or lane started that waits only on readers, as drive job add records beside a build order's reader, so nothing of its work is under way yet."""
    return (
        job["kind"] == "builder"
        and job["state"] == "waiting"
        and not job.get("agent")
        and not job.get("dispatch")
        and bool(job["waits"])
        and all(data["jobs"].get(wait, {}).get("kind") == "reader" for wait in job["waits"])
    )


def result_state(job: dict, line: str) -> str | None:
    """The state a job's returned line moves it to, or None when the line is no result: a reader's answer or its DESIGN to done, since a design lane takes its choice on; `BUILT <branch>, waits on <cards>` to built, and an ASK to waiting on his card. A codex job's line, which its run's state field opens, maps as every other job's does, so its HANDOFF stops it and its READY or PUSHED waits on its PR."""
    opening = re.match(r"[A-Z]*", line)[0]
    reader = job["kind"] == "reader"
    if BUILT_LINE.match(line):
        return "built"
    return "done" if reader and opening == "DESIGN" else RESULT_STATES.get(opening, "done" if reader else None)


def reported(line: str) -> bool:
    """Whether a line opens with a result other than an ASK, so a lane that sent it as an Orca question returned that result and asked nothing."""
    opening = re.match(r"[A-Z]*", line)[0]
    return bool(BUILT_LINE.match(line)) or opening in RESULT_STATES and opening != "ASK"


def kept_return(name: str, job_id: str, job: dict, line: str) -> bool:
    """Whether the line a job's agent returned is the result the drive keeps for it, word for word: an `ASK <path> [<path>…]` naming the blocks `drive job ask` kept for its questions, else the first line of its newest return, never its said line, which the watch rewrites as it settles a PUSHED."""
    if asked_in(job, line):
        return True
    return opening(report_text(name, job_id)) == line


def result_refusal(data: dict, job_id: str, line: str, report: str) -> str | None:
    """Why a job's returned line may not set its state, naming the fix: a stopped job's work goes on in another job, a line opening with no result sets nothing, a builder's READY or PUSHED naming a yasyf/* PR keeps the proof file the watch runs once it merges, a WAITING names something the watch frees it on, and a design lane's DESIGN names on each Fork: line a question drive job ask kept for it."""
    job = data["jobs"][job_id]
    if job["state"] == "stopped":
        return (
            f"{job_id} is stopped, so its work goes on in another job: hand this return to the job its said line names,"
            f" or start one with drive job add {job['kind']} --order {job['order']} briefed with a Handoff: line"
        )
    if not result_state(job, line):
        return "its first line sets no state: it opens DONE, READY, PUSHED, `BUILT <branch>, waits on <cards>`, WAITING, ASK, BLOCKED, DESIGN or HANDOFF"
    if job["kind"] == "builder" and REPORTED_LINE.match(line) and (merging := [pr_key(found[0]) for found in PR_URL.finditer(line) if merges_untapped(pr_key(found[0]))]):
        if not (job.get("repro") and os.access(job["repro"], os.X_OK)):
            return (
                f"it names {', '.join(merging)}, which merges once this is taken and the watch proves by running {job_id}'s proof file, and {job_id}"
                f" keeps none it can run: keep an executable script exiting 0 once the installed plugins hold the fix, printing a line for each check it passed, with drive job set {job_id} --repro <file>,"
                " then send this report again"
            )
    if re.match(r"WAITING\b", line) and not (job["waits"] or job["due"] or named_waits(data, job["order"], wait_words(line, report))):
        return (
            "its WAITING names nothing the watch frees it on: name the release as release:<service>:<owner>/<repo>#<n> or a PR the drive"
            f" tracks, or first give it what it waits on with drive job set {job_id} --waits <card or job> or a clock with --due <time>"
        )
    if job["kind"] == "design" and line.startswith("DESIGN") and (unkept := [path for path in FORK.findall(report) if not kept_question(job, Path(path).expanduser())]):
        return f"its Fork: line names {unkept[0]}, which keeps no question of {job_id} his card can draw: keep it with drive job ask {job_id} <file> and name the path it prints"
    return fix_refusal(data, job_id, line, report)


def traced(data: dict, job_id: str, line: str) -> str | None:
    """The order whose live bug a job's result traces the cause of: an incident or reported bug one of its first check or cause parts diagnoses, or a reported bug it reads as its reader; None for any other job and for a result opening ASK, BLOCKED or HANDOFF, which traces nothing yet."""
    job = data["jobs"][job_id]
    if re.match(r"[A-Z]*", line)[0] in UNTRACED:
        return None
    return next(
        (
            order_id
            for order_id in [job["order"], *job["carries"]]
            if (order := data["orders"][order_id])["incident"] or reported_bug(order)
            if {INCIDENT_CHECK, INCIDENT_CAUSE} & set(job_parts(job, order_id)) or (job["kind"] == "reader" and reported_bug(order))
        ),
        None,
    )


def fix_named(data: dict, order_id: str, fix: str) -> tuple[list[str], list[str], list[str]]:
    """The orders a FIX line's words name, each by its id or as the order a drive PR it names serves, the key of each GitHub PR it links that the drive does not track, and each word naming none of these; none for FIX none."""
    if re.match(rf"{NO_FIX}\b", fix):
        return [], [], []
    orders, untracked, unknown = [], [], []
    for word in filter(None, (word.strip(".;`*") for word in re.split(r"[\s,]+", fix))):
        if word in data["orders"]:
            orders.append(word)
        elif prs := named_prs(data, order_id, word):
            orders += [order_served(data["prs"][key]) for key in prs]
        elif PR_URL.match(word):
            untracked.append(pr_key(word))
        else:
            unknown.append(word)
    return list(dict.fromkeys(orders)), list(dict.fromkeys(untracked)), unknown


def untracked_fixes(data: dict, job_id: str, line: str, report: str) -> list[str]:
    """The key of each GitHub PR the drive does not track that the last FIX line of a result tracing a live bug's cause links, which `drive job report` registers on the job before it takes the result."""
    if not traced(data, job_id, line) or not (fixes := FIX_LINE.findall(report)):
        return []
    return fix_named(data, data["jobs"][job_id]["order"], fixes[-1])[1]


def fix_refusal(data: dict, job_id: str, line: str, report: str) -> str | None:
    """Why a result tracing a live bug's cause may not be taken: it ends with no FIX line naming the order or PR that fixes the cause, or none, which take_result marks live from, by his pick "Incident cause names the fix: live from the report that names it until it merges; costs one required line on every cause report", or a word on its last one names no order of the drive and links no GitHub PR."""
    if not (bug := traced(data, job_id, line)):
        return None
    if not (fixes := FIX_LINE.findall(report)):
        return f"{job_id} traces the cause of {bug}'s live bug, so {FIX_RULE}"
    if unknown := fix_named(data, data["jobs"][job_id]["order"], fixes[-1])[2]:
        return f"its FIX line names {', '.join(unknown)}, neither an order of the drive nor a GitHub PR link: name each fix by its order id or PR link, comma-separated, or say FIX none"
    return None


def kept_question(job: dict, file: Path) -> bool:
    return bool((question := kept_in(job, file)) and not question["refused"])


def named_waits(data: dict, order_id: str, line: str) -> list[str]:
    """The waits a result line names: each release:<service>:<owner>/<repo>#<n> of a PR the drive tracks, and each other drive PR it names."""
    releases = [found[0] for found in RELEASE_WAIT.finditer(line) if found["pr"] in data["prs"]]
    return list(dict.fromkeys([*releases, *named_prs(data, order_id, RELEASE_WAIT.sub("", line))]))


def wait_words(line: str, report: str) -> str:
    """The words a result's waits are read from: a WAITING's whole report, which may name what it waits on only below its first line, and a READY's or PUSHED's first line, which names its own PRs while its body cites others."""
    return f"{line}\n{report}" if re.match(r"WAITING\b", line) else line


def result_waits(data: dict, order_id: str, line: str, report: str = "") -> list[str]:
    """The waits a result gives its job: each PR and release a READY's or PUSHED's line names or a WAITING's whole report names, and none for any other line."""
    return named_waits(data, order_id, wait_words(line, report)) if re.match(r"(?:READY|PUSHED|WAITING)\b", line) else []


def take_result(data: dict, job_id: str, line: str, report: str = "") -> None:
    """Move a job result_refusal lets take the line to the state it opens with, with that line as its said line, a builder's DONE on an open incident's stop or rollback keeping its proof as the order's proof, which ends the incident's five-minute line, built on its branch and waiting on each card a BUILT line names and each open card of his a job on its parts asked, whatever words the line names it in, each card once under its waits id, waiting on each PR and release a READY or PUSHED line or a WAITING's whole report names, which the watch frees once the PR merges or closes or the release is out, each PR a `WAITING … waits on` line names waiting on each card it names, keep on a job its WAITING line answers as stage.waited reads it and on each red PR it answers each PR and release it names, which his card on that PR then names and links, keep each verdict a reader's answer gives on the PR it names, join each open order the FIX line of a result tracing a live bug's cause names to that bug's incident, so hurts_now reads it live, end the rework of each PR a builder's READY names, its report's CHANGE: line saying how his words are met, dropping his tap on it so a fresh card asks it for the reworked change, and deliver the parts of a job whose answer reaches him only by a post by the post its POSTED line names; an ASK waiting on his card keeps the said line drive job ask set, the question's title."""
    from drive import stage, view

    job = data["jobs"][job_id]
    taken, moment = result_state(job, line), stamp()
    job.update(state=taken, updated=moment, returned=moment)
    if taken != "waiting" or not re.match(r"ASK\b", line):
        job["said"] = line
    if built := BUILT_LINE.match(line):
        job.update(branch=built["branch"], waits=view.stored_waits(data, [*job["waits"], *BUILT_CARD.findall(built["cards"]), *part_cards(data, job_id)]))
    if waits := result_waits(data, job["order"], line, report):
        job["waits"] = list(dict.fromkeys([*job["waits"], *waits]))
    if re.match(r"WAITING\b", line):
        job["waited"] = stage.waited(data, job_id, wait_words(line, report))
        for key in job["waited"]["red"]:
            data["prs"][key]["waits"] = view.stored_waits(data, [*data["prs"][key]["waits"], *job["waited"]["waits"]])
    if waiting := WAITING_LINE.match(line):
        for key in named_prs(data, job["order"], waiting["prs"]):
            data["prs"][key]["waits"] = view.stored_waits(data, [*data["prs"][key]["waits"], *BUILT_CARD.findall(waiting["cards"])])
    if taken == "done" and job["kind"] == "builder":
        for order_id in open_incidents(data, [job["order"], *job["carries"]]):
            if {INCIDENT_STOP, INCIDENT_ROLLBACK} & set(job_parts(job, order_id)):
                data["orders"][order_id]["proof"] = data["orders"][order_id]["proof"] or line.removeprefix("DONE").strip()
    if (bug := traced(data, job_id, line)) and (fixes := FIX_LINE.findall(report)):
        head = data["orders"][bug]["joins"] or bug
        for order_id in fix_named(data, job["order"], fixes[-1])[0]:
            if not (hurts_now(data, order := data["orders"][order_id]) or order["state"] in CLOSED_ORDER_STATES):
                order["joins"] = head
    if (posted := POSTED_LINE.match(line)) and unposted_answer(data, job_id):
        deliver_posted(data, job_id, posted["link"], moment)
    if job["kind"] == "builder" and re.match(r"READY\b", line):
        met = REPORT_CHANGE.search(report)
        for key in named_prs(data, job["order"], line):
            if rework := data["prs"][key]["rework"]:
                changed = f'Reworked to your words "{rework["words"]}": {met["change"] if met else line}'
                data["prs"][key].update(rework=None, tap=None, fundamental=changed, updated=job["updated"])
    end_carried_designs(data, job["order"], job["updated"])


def part_cards(data: dict, job_id: str) -> list[str]:
    """Each open card of his that a job on any part this job delivers asked, by its link or else its waits id, as view.open_cards reads it: a builder built beside its reader's card waits on that card from the drive's records, never from how its line words it."""
    from drive import view

    job = data["jobs"][job_id]
    return [card for order_id in [job["order"], *job["carries"]] for part in job_parts(job, order_id) for _, other in part_jobs(data, order_id, part) for card in view.open_cards(other)]


def required_verdict(prs: dict[str, dict]) -> tuple[str, list[tuple[str, str]]]:
    """pass, fail or pending over the checks the base branch of each open PR requires, as its row reads them, with what holds it: each required check that failed, with its PR's head and its build, or each PR whose required checks still run."""
    rows = {key: pr for key, pr in prs.items() if pr["state"] == "open"}
    if failed := [(key, f"required {check} failed at {pr['head'][:7]} ({link})") for key, pr in rows.items() for check, link in pr["required"]["failed"].items()]:
        return "fail", failed
    running = [(key, f"required checks still run at {pr['head'][:7]}") for key, pr in rows.items() if pr["required"]["checks"] == "pending"]
    return ("pending" if running else "pass"), running


def pushed_verdict(data: dict, job_id: str) -> tuple[str, list[tuple[str, str]]]:
    """The required_verdict on a builder's PUSHED over each PR its line names, pending until the watch's first GitHub read after the job took it, so the head judged is the one its builder pushed; a PR the drive read merged or closed, or a line naming no PR the drive tracks, holds nothing."""
    if unread_since_take(data, job_id):
        return "pending", []
    job = data["jobs"][job_id]
    return required_verdict({key: data["prs"][key] for key in named_prs(data, job["order"], job["said"])})


def unread_since_take(data: dict, job_id: str) -> list[str]:
    """Each open PR a builder's line names that the watch has not read from GitHub since the job took that line."""
    job = data["jobs"][job_id]
    taken = parse_time(job["updated"])
    named = {key: data["prs"][key] for key in named_prs(data, job["order"], job["said"])}
    return [key for key, pr in named.items() if pr["state"] == "open" and not (pr["required"] and parse_time(pr["required"]["read"]) > taken)]


def add_changes(name: str, data: dict, job_id: str, report: str) -> list[str]:
    """Make each change a design job's DESIGN return lists on its `Change:` lines, when it lists more than one, a part of the design's order under the line's words before its `(waits on …)`, naming the design, a part already under those words included, a design of one change being its own parts; then give each change no builder or codex job is on a builder of its own, by change_builders, and return the builders added."""
    job = data["jobs"][job_id]
    found = [(whole_order(line["change"]), change_waits(line)) for line in CHANGE_LINE.finditer(report)]
    if job["kind"] != "design" or not report.lstrip().startswith("DESIGN") or not found:
        return []
    order = data["orders"][job["order"]]
    for change, _ in found if len(found) > 1 else []:
        order["parts"].setdefault(change, new_part()).update(design=job_id)
    added = change_builders(name, data, job_id, [([change], waits) for change, waits in found] if len(found) > 1 else [(job["parts"], [])])
    if len(found) > 1 or added:
        order["updated"] = stamp()
        end_carried_designs(data, job["order"], order["updated"])
    return added


def change_builders(name: str, data: dict, design_id: str, changes: list[tuple[list[str], list[int]]]) -> list[str]:
    """Add a builder for each change of a design, its parts and the number of each earlier change it waits on, that no builder or codex job is on, in its list's order, waiting on each open card of his on the design's parts and on each job building an earlier change it waits on, or that job's first PR once it opened one, by his pick "A builder for every listed change, added the moment the design returns: no change can sit unbuilt; idle jobs appear early"; none where brief.job_refusal refuses a builder, as on an order limited to options until drive order set --no-options-only records his pick."""
    from drive import brief, view

    order_id = data["jobs"][design_id]["order"]
    cards = view.stored_waits(data, part_cards(data, design_id))
    created = stamp()
    added = []
    for parts, numbers in changes:
        if not (uncarried := [part for part in parts if not any(job["kind"] in CHANGE_KINDS for _, job in part_jobs(data, order_id, part))]):
            continue
        earlier = [part for number in numbers for part in changes[number - 1][0] if not delivered(data, order_id, part)]
        building = [pr_key(job["prs"][0]) if job["prs"] else job_id for part in earlier for job_id, job in part_jobs(data, order_id, part) if job["kind"] in CHANGE_KINDS]
        waits = list(dict.fromkeys([*cards, *(wait for wait in building if view.holds(data, wait, None))]))
        if brief.job_refusal(name, data, order_id, "builder", uncarried, False, waits):
            continue
        builder_id = next_id(data["jobs"], "j")
        builder = data["jobs"][builder_id] = new_job(order_id, "builder", f"build {design_id}'s change: {'; '.join(uncarried)}", uncarried, created) | {"waits": waits}
        hold_unstarted(builder)
        data["orders"][order_id]["jobs"].append(builder_id)
        added.append(builder_id)
    return added


def cite_designs(data: dict, job_id: str, line: str, report: str) -> None:
    """Cite the DESIGN: lines of a READY or PUSHED return on each PR its first line names, replacing what each cited before, so the check reads a PR's design answers from its newest return citing any and never from an older return or another PR's; a return citing none leaves each PR's citation as it was."""
    if REPORTED_LINE.match(line) and (cited := design_lines(report)):
        for key in named_prs(data, data["jobs"][job_id]["order"], line):
            cite(data["prs"][key], cited)


def design_lines(report: str) -> str:
    return "\n".join(line for line in report.splitlines() if line.startswith("DESIGN:"))


def cite(pr: dict, design: str, answered: list[str] | None = None) -> None:
    """Keep on the PR the citation of his answers for the design it makes, the one list the check reads, with those answers as read, or None until the check reads them once, and drop its check so it runs on them; citing what it already cites, read nowhere, keeps the answers read before, which outlive cc-slack dropping a card."""
    if design == pr["design"] and answered is None:
        return
    pr.update(design=design, answers=answered, checker=None)


def change_waits(line: re.Match) -> list[int]:
    """The number of each earlier change a `Change:` line's `(waits on …)` tail names."""
    return [int(number) for number in CHANGE_WAITS.findall(line["waits"] or "")]


def end_carried_designs(data: dict, order_id: str, moment: str) -> None:
    """End each design job on the order waiting on its returned DESIGN that a builder carries, since a design ends once its builder takes it on: its cards answered or built on meanwhile; a design still waiting on its reader has designed nothing yet."""
    for design_id, design in data["jobs"].items():
        returned = design["state"] == "waiting" and (design["said"] or "").startswith("DESIGN")
        if design["order"] == order_id and returned and (builder := design_builder(data, design_id)):
            design.update(state="done", said=f"carried by builder {builder}", updated=moment)


def design_builder(data: dict, design_id: str) -> str | None:
    """The builder carrying a design job's parts, or a change its DESIGN listed, on its order, the one that returned DONE first, else one still at work, since a design ends once its builder takes it on: its cards answered or built on meanwhile."""
    design = data["jobs"][design_id]
    if design["kind"] != "design":
        return None
    changes = designed(data, design_id, design["order"])
    builders = [
        (job["state"] != "done", job_id)
        for job_id, job in data["jobs"].items()
        if job["kind"] == "builder"
        and job["order"] == design["order"]
        and job["state"] not in ("failed", "stopped")
        and (set(design["parts"]) <= set(job["parts"]) or changes & set(job["parts"]))
    ]
    return min(builders)[1] if builders else None


def designed(data: dict, job_id: str, order_id: str) -> set[str]:
    """The parts of an order a job's DESIGN listed as its changes, none for a job that designs nothing."""
    return {part for part, held in data["orders"][order_id]["parts"].items() if held["design"] == job_id}


def names_part(holds: str, parts: Iterable[str]) -> bool:
    """Whether a question's `Holds:` words name one of these parts, each by its words whatever their case and spacing."""
    said = " ".join(holds.split()).casefold()
    return bool(said) and any(part.casefold() in said for part in parts)


def names_job(holds: str, job_id: str) -> bool:
    """Whether a question's `Holds:` words name a job by its id, as a reader's names the builder its pick shapes."""
    return bool(re.search(rf"(?<![\w-]){re.escape(job_id)}\b", holds))


def held_words(data: dict, holds: str, order_ids: Iterable[str]) -> str:
    """A question's `Holds:` words with, for each PR the drive tracks on these orders that they name by its link, its owner/name#N key or a bare #N, the parts it serves appended: those `drive pr set --part` names, else the parts its job works on its order, so a lane that names its PR names its change."""
    orders = set(order_ids)
    named = [
        pr
        for key, pr in data["prs"].items()
        if pr["order"] in orders and re.search(rf"{re.escape(pr_url(key).removeprefix('https://'))}\b|{re.escape(key)}\b|(?<![\w/-])#{key.rpartition('#')[2]}\b", holds)
    ]
    served = [part for pr in named for part in pr["parts"] or sorted(worked(data, pr["job"], pr["order"])) if not names_part(holds, [part])]
    return "; ".join([holds, *dict.fromkeys(served)])


def worked(data: dict, job_id: str, order_id: str) -> set[str]:
    """The parts of an order a job works on: those it delivers and the changes of it it designed."""
    return {*job_parts(data["jobs"][job_id], order_id), *designed(data, job_id, order_id)}


def part_jobs(data: dict, order_id: str, part: str) -> list[tuple[str, dict]]:
    return [(job_id, data["jobs"][job_id]) for job_id in data["orders"][order_id]["jobs"] if part in job_parts(data["jobs"][job_id], order_id)]


def work(data: dict, order_id: str, part: str) -> bool:
    """Whether a part is work he ordered, which only a builder carries: every part of a build order or an incident, and a part of an order of no kind that a builder, codex or design job holds or held or a design lists, since a part only research ever held is his question; never a part of an answer order, of one limited to options, or one drive order set --kind answer --part sorted as an answer."""
    order = data["orders"][order_id]
    if order["kind"] == "answer" or order["options_only"] or order["parts"][part]["answer"]:
        return False
    return order["kind"] == "build" or order["incident"] or bool(order["parts"][part]["design"]) or any(job["kind"] in WORK_KINDS for _, job in part_jobs(data, order_id, part))


def carriers(data: dict, order_id: str, part: str) -> list[tuple[str, dict]]:
    """The jobs on a part that can carry it: every job on words he reads, but on work he ordered only a builder, codex or design job, a done design only while the builder design_builder names carries it."""
    jobs = part_jobs(data, order_id, part)
    if not work(data, order_id, part):
        return jobs
    return [(job_id, job) for job_id, job in jobs if job["kind"] in WORK_KINDS and (job["kind"] != "design" or job["state"] != "done" or design_builder(data, job_id))]


def delivered(data: dict, order_id: str, part: str) -> bool:
    """Whether a part is delivered: work he ordered once a builder or codex job on it returned DONE or drive order posted recorded the merged PR that delivered it, whatever a post says, and words he reads once posted to him or built by a builder that returned DONE."""
    done = {job["kind"] for _, job in part_jobs(data, order_id, part) if job["state"] == "done"}
    posted = data["orders"][order_id]["parts"][part]["posted"]
    if work(data, order_id, part):
        return bool(done & set(CHANGE_KINDS)) or bool(posted and PR_URL.search(posted))
    return "builder" in done or bool(posted)


def unposted_answer(data: dict, job_id: str) -> bool:
    """Whether a finished job's answer reaches him only by a post: it drew no card of his, which carries an answer itself, his recorded words settled none of its questions, and no builder carries its design."""
    job = data["jobs"][job_id]
    return job["state"] == "done" and not any(question["card"] or question["closed"] for question in job["questions"]) and not design_builder(data, job_id)


def deliver_posted(data: dict, job_id: str, link: str, moment: str) -> None:
    """Deliver each part a finished job's answer carries by the post its POSTED line names, the post that answered it, but work he ordered, which no post delivers, and close each answer order that post leaves nothing owed: every part posted, every job finished, no promised line owed and its reporter told or none."""
    job = data["jobs"][job_id]
    for order_id in [job["order"], *job["carries"]]:
        order = data["orders"][order_id]
        for part in job_parts(job, order_id):
            if not (order["parts"][part]["posted"] or work(data, order_id, part)):
                order["parts"][part]["posted"] = link
        finished = all(data["jobs"][other]["state"] in FINISHED_JOB_STATES for other in order["jobs"])
        if order["kind"] == "answer" and order["state"] not in CLOSED_ORDER_STATES and finished and not owed(order) and not (order["reporter"] and not order["reporter"]["told"]):
            if all(held["posted"] for held in order["parts"].values()):
                order.update(state="done", updated=moment)


def merge_proven(order: dict) -> bool:
    """Whether an open order keeps as its proof only merge commits outside yasyf/*, as the watch's DONE at a merge once kept with its close report, though a merge proves no fix live."""
    commits = [MERGE_COMMIT.fullmatch(token) for token in (order["proof"] or "").split()]
    return bool(commits) and all(commit and not merges_untapped(commit["repo"]) for commit in commits) and order["state"] not in CLOSED_ORDER_STATES


def may_tell(order: dict) -> bool:
    """Whether the thread an order's reporter asked in may hear from it now: an answer order's once every part of its answer is posted, any other's once the order keeps the proof its fix is live."""
    return all(held["posted"] for held in order["parts"].values()) if order["kind"] == "answer" else bool(order["proof"])


def named_prs(data: dict, order_id: str, words: str) -> list[str]:
    """The drive PRs the words name by link, key or bare #N; a bare #N that drive PRs in several repositories carry names the one on the order."""
    keys = []
    for found in PR_REF.finditer(words):
        if not (number := found.groups()[-1]):
            named = pr_key(found[0])
            keys.append(named if named in data["prs"] else next((key for key in data["prs"] if key.casefold() == named.casefold()), None))
            continue
        shared = [key for key in data["prs"] if key.rpartition("#")[2] == number]
        keys += shared if len(shared) == 1 else [key for key in shared if data["prs"][key]["order"] == order_id]
    return [key for key in dict.fromkeys(keys) if key]


def local_lane(dispatch: str) -> dict:
    """The fields of a job whose Orca worker runs on this Mac: a lane read through its Dispatch like a Sprite's, with no Sprite."""
    return {"where": "remote", "route": LOCAL_ROUTE, "dispatch": dispatch, "workspace": None, "environment": None}


def on_mac(job: dict) -> bool:
    return job["route"] == LOCAL_ROUTE


def lane_died(job: dict, worker: str, why: str, moment: datetime) -> bool:
    """Record once that the job's current worker died or finished, so it takes no more messages: its remote lane, by this dispatch, or its agent on this Mac, by this agent; whether this call recorded it."""
    key = worker_key(job)
    deaths = job.setdefault("deaths", [])
    if job[key] != worker or any(death.get(key) == worker for death in deaths):
        return False
    held = {"dispatch": worker, "workspace": job["workspace"]} if key == "dispatch" else {"dispatch": None, "workspace": None, "agent": worker}
    deaths.append({**held, "why": why, "at": stamp(moment)})
    job["updated"] = stamp(moment)
    return True


def lane_death(job: dict) -> dict | None:
    """The recorded death of the job's current worker, its remote lane or its agent on this Mac, which takes no message until drive job resume replaces it; None while it lives or the job has none."""
    key = worker_key(job)
    return next((death for death in job.get("deaths", []) if job.get(key) and death.get(key) == job[key]), None)


def worker_key(job: dict) -> str:
    return "dispatch" if job.get("where") == "remote" else "agent"


def lane_ended(job: dict) -> bool:
    """Whether a `drive job end` ended the job's current remote lane, which it records before it saves, stops or destroys anything, so whatever that lane sends or Orca says of it afterwards is the end's own doing."""
    return bool((death := lane_death(job)) and death.get("ended"))


def whole_order(text: str) -> str:
    return " ".join(text.split())[:PART_CHARS].rstrip()


def new_order(text: str, source: str, thread: tuple[str, str] | None, parts: dict[str, dict], created: str) -> dict:
    return {
        "text": text,
        "from": source,
        "thread": list(thread) if thread else None,
        "threads": [],
        "state": "open",
        "due": None,
        "due_said": None,
        "promised": None,
        "next_due": None,
        "next_set": None,
        "kept": None,
        "withdrawn": None,
        "jobs": [],
        "alerts": [],
        "recovered": None,
        "quiet": None,
        "reporter": None,
        "watch": None,
        "parts": parts,
        "incident": False,
        "kind": "build",
        "complex": False,
        "options_only": False,
        "once": False,
        "owner": None,
        "project": None,
        "workstream": None,
        "title": None,
        "proof": None,
        "close_report": None,
        "closed_on": None,
        "note": None,
        "joins": None,
        "created": created,
        "updated": created,
        "last_line": None,
    }


def new_part() -> dict:
    """A part of an order, undelivered."""
    return {"posted": None, "orphan_said": None, "orphan_gap": None, "design": None, "answer": False}


def reporter(link: str) -> dict:
    """The message someone else wrote that an order fixes or answers, a bug they reported or a question they asked him, as the Slack thread its one note goes in, read from its permalink, a reply's carrying its thread's ts, or from slack:<channel>/<thread ts>, with no Slack call, since the bot may not be in that channel; none told yet."""
    if not (linked := link_thread(link)):
        raise DriveError(f"--reporter {link}: refused, name the message by its Slack permalink or as slack:<channel>/<its thread's ts>")
    return {"link": link, "channel": linked[0], "thread": linked[1], "told": None, "tell_said": None}


def new_watch(link: str, moment: datetime) -> dict:
    """A teammate's Slack thread he ordered watched, from its permalink or slack:<channel>/<thread ts>, with no Slack call: the watch reads it each READ_EVERY and has a handler answer every message newer than seen there, until the order closes."""
    if not (linked := link_thread(link)):
        raise DriveError(f"--watch {link}: refused, name the thread by its Slack permalink or as slack:<channel>/<its thread's ts>")
    since = f"{moment.timestamp():.6f}"
    return {"channel": linked[0], "thread": linked[1], "since": since, "seen": since, "record": None}


def reported_bug(order: dict) -> bool:
    """Whether an order fixes a bug someone else reported: a reporter on any order but an answer order, whose reporter asked him a question."""
    return bool(order["reporter"]) and order["kind"] != "answer"


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
        "waited": None,
        "waiting_said": [],
        "waiting_claims": {},
        "mail_seen": 0,
        "turn_open": False,
        "undelivered": [],
        "state": "running",
        "said": None,
        "due": None,
        "created": created,
        "updated": created,
        "returned": None,
        "resumed": None,
    }


def new_question(
    file: str | None, title: str | None, asked: str, unsettled: str | None = None, card: str | None = None, refused: str | None = None, closed: dict | None = None, holds: str = ""
) -> dict:
    return {"file": file, "title": title, "asked": asked, "unsettled": unsettled, "waits": None, "card": card, "refused": refused, "closed": closed, "holds": holds, "replaced": None, "recorded": []}


def refused_by(question: dict) -> str:
    """The verb that refused a question: `drive job card`, drawing a card under the waits id the question holds, else `drive job ask`, whose refusal leaves the question holding none."""
    return "drive job card" if question["waits"] else "drive job ask"


def new_waits(name: str, job_id: str) -> str:
    """The waits id a job's new question draws its one card under, numbered by the millisecond it was minted, so no two questions of a job share one and none carries a character cc-slack refuses."""
    return f"{name}:{job_id}-{time.time_ns() // 1_000_000}"


def new_close(
    how: str, words: str | None, link: str | None, seq: str | None = None, answer: str | None = None, ruling: str | None = None, by: str | None = None, at: str | None = None
) -> dict:
    """How a job's question ended, the one record of whether it still asks him that its card follows: answered by his tap, words or a ruling, withdrawn for his other words or by the asker, or superseded by his answer to another question; his words and their link, the record, recorded answer and ruling carrying them, the question whose answer superseded it, and when."""
    return {"how": how, "words": words, "link": link, "seq": seq, "answer": answer, "ruling": ruling, "by": by, "at": at or stamp()}


def kept_in(job: dict, file: Path) -> dict | None:
    """The job's newest question kept in this ASK file."""
    return next((question for question in reversed(job["questions"]) if question["file"] and Path(question["file"]).resolve() == file.resolve()), None)


def ask_files(line: str) -> list[Path]:
    """The files an `ASK <path> [<path>…]` line names, none for any other line, an ASK block's question included."""
    return [Path(file).expanduser() for file in asked["files"].split()] if (asked := ASK_LINE.fullmatch(line)) else []


def asked_in(job: dict, line: str) -> list[dict]:
    """The job's questions kept in the files an `ASK <path> [<path>…]` line names, none unless each file keeps one."""
    questions = [kept_in(job, file) for file in ask_files(line)]
    return questions if all(questions) else []


def question_in(job: dict, question: dict) -> dict:
    """The job's question as this load of the drive holds it, asked at the same time from the same file under the same waits id as one an earlier load read."""
    return next(kept for kept in job["questions"] if (kept["asked"], kept["file"], kept["waits"]) == (question["asked"], question["file"], question["waits"]))


def same_question(one: str | None, other: str | None) -> bool:
    """Whether two questions ask him the same thing: the same words, whatever their case and spacing."""
    return bool(one and other) and " ".join(one.split()).casefold() == " ".join(other.split()).casefold()


def version_order(version: str) -> tuple[int, ...]:
    return tuple(map(int, version.split(".")))


def taken(handle: IO, seconds: float) -> bool:
    """Take handle's lock exclusive within seconds, polling, since a blocking flock waits forever and on macOS lets new shared holders past a waiting exclusive one; whether it was taken."""
    ends = time.monotonic() + seconds
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= ends:
                return False
            time.sleep(LOCK_POLL_SECONDS)


def holders(path: Path) -> str:
    """Each other process with path open, as lsof finds it, by pid and command, which names who holds its lock; within HOLDERS_SECONDS, so naming them never holds back what waited on them."""
    try:
        pids = [pid for pid in subprocess.run(["lsof", "-t", str(path)], capture_output=True, text=True, timeout=HOLDERS_SECONDS).stdout.split() if pid != str(os.getpid())]
        if not pids:
            return "no holder found"
    except subprocess.TimeoutExpired:
        return f"its holders did not list within {HOLDERS_SECONDS}s"
    named = {pid: procs.command(int(pid)) for pid in pids}
    return "; ".join(f"pid {f'{pid} {command}'[:HOLDER_CHARS]}" for pid, command in named.items() if command is not None)


def save(name: str, data: dict) -> None:
    """Save the drive as this install's, refused with nothing written when a newer install saved it since."""
    written(name, data | {"saved_by": VERSION}, VERSION)


def store(name: str, data: dict) -> None:
    """Store data as the drive's saved state, its saved_by the install data names."""
    written(name, data, None)


def written(name: str, data: dict, newest: str | None) -> None:
    """Write data to the drive's database in one transaction: each row whose JSON changed, each it no longer holds deleted, and saved_by when it changed, so a write that changes nothing writes nothing, and one that changed a row is kept in WROTE; refused, writing nothing, when an install newer than newest saved it last. Each write then checkpoints what the WAL holds into the database as far as no reader still needs it, so the WAL every reader searches stays short; a checkpoint that fails leaves the committed write standing, for the next write's to finish. A drive with no database yet gets one built whole beside it and moved into place, so no reader meets it half built, then the drive.json pointing to it that every older install refuses."""
    if not (path := database_path(name)).exists():
        os.replace(built(name, data), path)
        pointed(name, data)
        WROTE.add("drive")
        return
    with closing(connect(path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            rewrite(name, db, data, newest)
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")
        if db.total_changes:
            WROTE.add("drive")
        with suppress(sqlite3.OperationalError):
            db.execute("PRAGMA wal_checkpoint(PASSIVE)")


def built(name: str, data: dict) -> Path:
    """A database holding data alone, in WAL mode, built under a name of this process's beside the drive's."""
    path = database_path(name).with_name(f"{DATABASE}.{os.getpid()}.tmp")
    path.unlink(missing_ok=True)
    with closing(connect(path)) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(TABLES)
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO meta VALUES ('schema', ?)", (SCHEMA,))
        rewrite(name, db, data, None)
        db.execute("COMMIT")
    return path


def rewrite(name: str, db: sqlite3.Connection, data: dict, newest: str | None) -> None:
    """Make the database's rows data's, inside a transaction: one row for each top-level field, a collection's with no body, and one for each record of a collection, each keeping its place unless the order data holds them in moved."""
    found = db.execute("SELECT value FROM meta WHERE key = 'saved_by'").fetchone()
    if newest and found and version_order(found[0]) > version_order(newest):
        raise NewerDrive(f"the {name} drive was saved by long-running {found[0]} since this {newest} read it, which may misread it: run the newest install's drive")
    kept: dict[str, dict[str, tuple[int, str | None]]] = {}
    for collection, key, place, body in db.execute("SELECT collection, key, place, body FROM rows"):
        kept.setdefault(collection, {})[key] = (place, body)
    rows: dict[str, dict[str, str | None]] = {"": {}}
    for field, value in data.items():
        if field in COLLECTIONS:
            rows[""][field] = None
            rows[field] = {key: json.dumps(record) for key, record in value.items()}
        elif field != "saved_by":
            rows[""][field] = json.dumps(value)
    for collection in {*kept, *rows}:
        before, after = kept.get(collection, {}), rows.get(collection, {})
        places = placed(before, list(after))
        db.executemany("DELETE FROM rows WHERE collection = ? AND key = ?", [(collection, key) for key in before if key not in after])
        db.executemany(
            "INSERT OR REPLACE INTO rows VALUES (?, ?, ?, ?)",
            [(collection, key, places[key], body) for key, body in after.items() if before.get(key) != (places[key], body)],
        )
    if (saver := data.get("saved_by")) != (found and found[0]):
        db.execute("DELETE FROM meta WHERE key = 'saved_by'")
        if saver:
            db.execute("INSERT INTO meta VALUES ('saved_by', ?)", (saver,))


def placed(before: dict[str, tuple[int, str | None]], order: list[str]) -> dict[str, int]:
    """The place of each key of a collection in order: the one it had, and each new key's after every earlier one, while the keys it had stay in their order ahead of the new ones; else each key's index, so the collection reads back in order."""
    kept = [key for key in order if key in before]
    if order[: len(kept)] == kept and all(before[one][0] < before[other][0] for one, other in itertools.pairwise(kept)):
        start = max((place for place, _ in before.values()), default=-1) + 1 - len(kept)
        return {key: before[key][0] if key in before else start + index for index, key in enumerate(order)}
    return {key: index for index, key in enumerate(order)}


def moved(name: str) -> None:
    """Move a drive an older install saved to drive.json into its database, under the drive's lock, which every older install's write takes, once each older install's verb that may read it ended: drive.json kept as it was beside it, then replaced by the pointer every older install refuses, then the database moved into place; a move cut short goes on from the kept copy, and a drive a newer install saved is refused, moving nothing."""
    if database_path(name).exists() or not (path := state_path(name)).exists():
        return
    found = json.loads(path.read_text())
    data = json.loads(backup_path(name).read_text()) if MOVED in found else found
    if (saver := data.get("saved_by")) and version_order(saver) > version_order(VERSION):
        raise NewerDrive(f"the {name} drive was saved by long-running {saver}, newer than this {VERSION}, which may misread it: run the newest install's drive")
    if MOVED not in found:
        linking = backup_path(name).with_name(f"{BACKUP}.{os.getpid()}.tmp")
        linking.unlink(missing_ok=True)
        os.link(path, linking)
        os.replace(linking, backup_path(name))
    database = built(name, data)
    pointed(name, data)
    os.replace(database, database_path(name))


def pointed(name: str, data: dict) -> None:
    """Replace drive.json with a pointer to the drive's database, saved_by an install newer than any, so every older install's load, edit, batch and edit_or_create refuse the drive as a newer install's, its bindings kept so an older verb still resolves the drive and says so."""
    pointer = {MOVED: DATABASE, "backup": BACKUP, "saved_by": MOVED_SAVER, "name": name, "roles": {}, "subcoordinators": {}, "handlers": {}, "launched": {}}
    tmp = state_path(name).with_name("drive.json.tmp")
    tmp.write_text(json.dumps(pointer | {field: data[field] for field in BINDINGS if field in data}, indent=2) + "\n")
    os.replace(tmp, state_path(name))


def clone(name: str, folder: Path) -> None:
    """Copy the drive's saved state into folder, as the drive of its name there, read in one transaction, with the mark of each once-only pass its loads already ran, so a load of the copy runs only the passes a load of the drive would."""
    (folder / name).mkdir(parents=True)
    for mark in (APPROVALS_LABELLED, f"asks/{REBUILT_ASKS}", f"pictures/{picture.SOURCED}"):
        if (drive_dir(name) / mark).exists():
            (folder / name / mark).parent.mkdir(exist_ok=True)
            (folder / name / mark).touch()
    if not database_path(name).exists():
        (folder / name / "drive.json").write_text(json.dumps(stored_json(name)))
        return
    with closing(connect(database_path(name))) as source, closing(sqlite3.connect(folder / name / DATABASE)) as copy:
        source.backup(copy)


@contextmanager
def flocked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def locked(name: str) -> Iterator[None]:
    """Hold the drive's lock for one load and save, waiting up to DRIVE_LOCK_SECONDS for the process holding it, then refusing with who that is, so a process stuck under it fails this verb instead of holding every writer of the drive behind it; a process's first hold moves the reports an older install kept into their jobs' histories."""
    path = drive_dir(name) / ".lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    asked = time.monotonic()
    with open(path, "a") as handle:
        if not taken(handle, DRIVE_LOCK_SECONDS):
            raise DriveError(f"{name}'s drive lock is still held after {DRIVE_LOCK_SECONDS}s, so this changed nothing: {holders(path)}")
        got = time.monotonic()
        try:
            moved_reports(name)
            yield
        finally:
            if (held := time.monotonic() - got) >= SLOW_HOLD_SECONDS:
                kept_hold(name, held, got - asked)


def kept_hold(name: str, held: float, waited: float) -> None:
    """Log a hold of the drive's lock past SLOW_HOLD_SECONDS to locks.log: how long, how long it waited first, the drive code that held it and the verb, so the verb holding every writer behind it is named."""
    holder = next(frame for frame in reversed(traceback.extract_stack()[:-1]) if Path(frame.filename).name not in ("state.py", "contextlib.py"))
    with open(drive_dir(name) / "locks.log", "a") as log:
        log.write(f"{stamp()} pid {os.getpid()} held {held:.1f}s after waiting {waited:.1f}s in {holder.name} ({Path(holder.filename).name}:{holder.lineno}): drive {' '.join(sys.argv[1:])[:HOLDER_CHARS]}\n")


def binding(name: str) -> AbstractContextManager[None]:
    """Hold the drive terminal's one Run binding for a bind and the call that needs it: a reply, or a launch's worker-start until Orca admits it."""
    return flocked(drive_dir(name) / ".binding.lock")


@contextmanager
def job_locked(name: str, job_id: str, seconds: float) -> Iterator[None]:
    """Hold the job's own lock while one launch, resume or PR wake reads its last worker and starts or messages the next, waiting up to seconds for another doing so, so a second one acts on the worker the first started; refused naming who holds it past that. A newer install's save meanwhile refuses this one's first load."""
    path = drive_dir(name) / "jobs" / f"{job_id}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as handle:
        if not taken(handle, seconds):
            raise DriveError(f"{job_id}'s lock is still held after {seconds:g}s, so nothing was started or sent: {holders(path)}")
        yield


@contextmanager
def writing(name: str) -> Iterator[None]:
    """Hold the drive's lock for a write, moving a drive.json an older install saved into the drive's database first."""
    with locked(name):
        moved(name)
        yield


@contextmanager
def edit(name: str) -> Iterator[dict]:
    """Load the drive under its lock, yield it for changes, and save it atomically on exit; inside a batch, which holds the lock already, keep the changes for the batch's one save."""
    if name in BATCHED:
        data = load(name)
        yield data
        BATCHED[name] = marshal.dumps(data)
        return
    with writing(name):
        data = load(name)
        yield data
        save(name, data)


@contextmanager
def batch(name: str) -> Iterator[list[str]]:
    """Hold the drive's lock across a call of several writes, each load and edit inside it reading and changing one copy in memory, saved once when the call ends and never when it raises, so a refused write leaves drive.json as it was; what a write does outside the drive runs after that save, its lines added to the list it yields."""
    after: list[str] = []
    with writing(name):
        BATCHED[name] = marshal.dumps(load(name))
        AFTER_SAVE[name] = []
        try:
            yield after
            save(name, marshal.loads(BATCHED[name]))
            effects = AFTER_SAVE[name]
        finally:
            del BATCHED[name], AFTER_SAVE[name]
    for effect in effects:
        after += effect()


def after_save(name: str, effect: Callable[[], list[str]]) -> list[str]:
    """Run what a write does outside the drive, such as withdrawing a card of his: now, or inside a batch once its save lands; the lines it prints now."""
    if name in AFTER_SAVE:
        AFTER_SAVE[name].append(effect)
        return []
    return effect()


@contextmanager
def edit_or_create(name: str, fresh: dict) -> Iterator[dict]:
    """Like edit, but start from fresh when the drive does not exist yet; the lock covers the check too."""
    with writing(name):
        data = load(name) if exists(name) else fresh
        yield data
        save(name, data)


def create(data: dict) -> None:
    """Save a drive built whole in code, as a proof or a test builds one, marked as needing no label_approvals pass, since its coordinator's cwd may be no checkout at all and every tap `drive answer` records for it is labelled already; `drive start` makes its drives through edit_or_create, whose first load labels the older taps of the checkout it starts in."""
    with writing(data["name"]):
        save(data["name"], data)
        (drive_dir(data["name"]) / APPROVALS_LABELLED).touch()


def next_id(records: dict, prefix: str) -> str:
    taken = [int(key[len(prefix) :]) for key in records if key.startswith(prefix) and key[len(prefix) :].isdigit()]
    return f"{prefix}{max(taken, default=0) + 1}"


def session_role(data: dict, session: str | None) -> str | None:
    """The role a session holds; a sub-coordinator's is coordinator and a handler's the communicator's, so every rule of that role binds it too."""
    role = next((role for role, held in data["roles"].items() if session and held["session"] == session), None)
    if role:
        return role
    if session_project(data, session):
        return "coordinator"
    return "communicator" if handler_id(data, session) else None


def handler_id(data: dict, session: str | None) -> str | None:
    """The handler a `claude --bg` session runs for the communicator, as its SessionStart hook bound it."""
    return next((handler for handler, held in data["handlers"].items() if session and held["session"] == session), None)


def own_transcript(data: dict, job: dict) -> str | None:
    """The transcript of a job a handler holds, its own claude --bg session's rather than one beside the communicator's: the bound handler's while it runs, the job's own once it ended."""
    return job.get("transcript") or (data["handlers"].get(job.get("agent")) or {}).get("transcript")


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


def join_thread(order: dict, where: tuple[str, str], at: str, kind: str) -> None:
    """Keep a thread among the order's from when it joined, once, unless it is the order's own: posted for a thread a post naming the order went in, question for a card its jobs drew, pr for a card drawn for a PR it serves."""
    if where != promise_thread(order) and all((channel, ts) != where for channel, ts, _, _ in order["threads"]):
        order["threads"].append([*where, at, kind])


def join_card(data: dict, job_id: str, card: str) -> None:
    """Join the thread of a card the job asks on to each order the job works on, so his answer under the card is in each one's threads."""
    if where := link_thread(card):
        for order in data["orders"].values():
            if job_id in order["jobs"]:
                join_thread(order, where, stamp(), "question")


def join_pr_cards(data: dict, keys: list[str], cards: list[str | None], at: str) -> None:
    """Join the thread under each card drawn for these PRs at his DM root to every order whose words they are checked against, so his reply under the card is in each one's threads; a card drawn in another order's thread leaves that thread its order's. A card link names one card or several, space apart."""
    orders = dict.fromkeys(order_id for key in keys for order_id in served_orders(data, data["prs"][key]))
    for where in dict.fromkeys((found[0], found[1]) for card in filter(None, cards) for link in card.split() if (found := slack_message(link)) and found[2] == found[1]):
        for order_id in orders:
            join_thread(data["orders"][order_id], where, at, "pr")


def link_thread(link: str) -> tuple[str, str] | None:
    """The channel and thread of the Slack message a permalink or a slack: form names: its thread's ts for a reply, else its own ts, the thread under it."""
    found = slack_message(link)
    return found and (found[0], found[2] or found[1])


def slack_message(source: str) -> tuple[str, str, str | None] | None:
    """The Slack message a source names, as its channel, its ts and the ts of the thread it is in, read with no Slack call: a permalink, a reply's carrying thread_ts, and slack:<channel>/<thread ts>/<reply ts> name that thread, while slack:<channel>/<ts> names none, None in its place; None for a source that is no Slack message, such as terminal."""
    if found := SLACK_SOURCE.fullmatch(source):
        return (found[1], found[3], found[2]) if found[3] else (found[1], found[2], None)
    if found := CARD_LINK.search(source):
        return found[1], f"{found[2]}.{found[3]}", found[4] or f"{found[2]}.{found[3]}"
    return None


def incident_orders(data: dict, order_id: str) -> list[str]:
    """The orders of the order's incident, the one an alert's order joined with --joins first, then every alert joined to it; an order nothing joined and joining none alone."""
    head = data["orders"][order_id]["joins"] or order_id
    return [head, *joined_orders(data).get(head, [])]


def joined_orders(data: dict) -> dict[str, list[str]]:
    """The orders joined with --joins to each order some joined, by the order they joined, in the order they came."""
    joined: dict[str, list[str]] = {}
    for order_id, order in data["orders"].items():
        if order["joins"]:
            joined.setdefault(order["joins"], []).append(order_id)
    return joined


def thread_order(data: dict, thread: str | None, until: datetime | None = None) -> str | None:
    """The order that came last into a thread of his, by until when given: added with it as its own thread, or joined to it as a thread it keeps, the newer order winning a tie; None when no order is in it."""
    if not thread:
        return None
    came = [
        (moment, index, order_id)
        for index, (order_id, order) in enumerate(data["orders"].items())
        for at in ([order["created"]] if order["thread"] and order["thread"][1] == thread else []) + [at for _, ts, at, _ in order["threads"] if ts == thread]
        for moment in [parse_time(at)]
        if until is None or moment <= until
    ]
    return max(came)[2] if came else None


def message_order(data: dict, channel: str, thread: str, ts: str) -> str | None:
    """The order his message in a thread is about: the order it started, else the one that came last into the thread by the time he sent it, so a thread two orders share splits his words between them by when each came in."""
    started = f"slack:{channel}/{ts}"
    return next((order_id for order_id, order in data["orders"].items() if order["from"] == started), None) or thread_order(
        data, thread, datetime.fromtimestamp(float(ts)).astimezone()
    )


def report_order(data: dict, attrs: dict) -> str | None:
    """The order a report is about: the one its order attr names, else the first its close attr names, else its job's, else the newest in its thread."""
    named = [attrs.get("order"), *closed_orders(attrs), (data["jobs"].get(attrs.get("job") or "") or {}).get("order")]
    return next((order_id for order_id in named if order_id in data["orders"]), None) or thread_order(data, attrs.get("thread"))


def report_orders(data: dict, attrs: dict) -> list[str]:
    """Every order a report is about: the one report_order reads, each its close attr names, each an item of its part attr names, and its job's."""
    job = data["jobs"].get(attrs.get("job") or "")
    named = [report_order(data, attrs), *closed_orders(attrs), *(on for on, _ in part_items(attrs, None)), job and job["order"]]
    return list(dict.fromkeys(order_id for order_id in named if order_id in data["orders"]))


def served_order(data: dict, attrs: dict) -> str | None:
    """The order a record serves: the one its attr order names, else the order of the job its attr job or waits id names, else the newest order in its thread; None when it names none."""
    job = data["jobs"].get(named_job(data, attrs))
    named = attrs.get("order") if attrs.get("order") in data["orders"] else job and job["order"]
    return named or thread_order(data, attrs.get("thread"))


def serving_lane(data: dict, kind: str, attrs: dict, answered: list[str]) -> str | None:
    """The cci lane of the session a communicator's owner, answer, handoff or refused record reaches, by the order its attr order names, else the order of the job its attr job or waits id names, else the newest order in its thread: the meta session's for a drive tooling order but for his words about his work; for an answer, handoff or refused record naming no job, the top coordinator's or sub-coordinator's that wrote the record it answers; else the coordinator session hearing that order; None for another kind, or a record naming none."""
    if kind not in ROUTED_KINDS:
        return None
    order = data["orders"].get(served_order(data, attrs) or "")
    if order and order["owner"] == "meta":
        return None if kind == "owner" else "meta"
    if kind != "owner" and not named_job(data, attrs) and (writer := coordinator_writer(data, answered)):
        return writer
    return order and order_lane(data, order)


def coordinator_writer(data: dict, seqs: list[str]) -> str | None:
    """The lane of the top coordinator or sub-coordinator that wrote the first of these records one of them wrote, read from cci."""
    from drive import agents

    coordinators = ("root", *map(project_lane, data["subcoordinators"]))
    return next((lane for seq in seqs if (lane := agents.shown(seq)["lane"]) in coordinators), None)


def named_job(data: dict, attrs: dict) -> str | None:
    """The job of this drive a record's attr job or waits id names."""
    named = WAITS_ID.fullmatch(attrs.get("waits") or "")
    job_id = attrs.get("job") or (named[2] if named and named[1] == data["name"] else None)
    return job_id if job_id in data["jobs"] else None


def routed(drives: dict[str, dict], session: str | None, kind: str, attrs: dict, to: list[str], answered: list[str]) -> list[str]:
    """Where a drive session's record goes: a defect to the top coordinator, the communicator or the meta session to the meta session, which holds drive tooling, unless it posts it; the communicator's owner record about the drive to the meta session alone, his ruling on how the drive works included; and the communicator's other owner, answer, handoff or refused record for a coordinator lane to the session serving_lane reads from it and the records it answers."""
    made = []
    for entry in to:
        lane, _, name = entry.partition("@")
        if not (data := drives.get(name)):
            made.append(entry)
            continue
        role = session_role(data, session)
        communicates = role == "communicator"
        if kind == "defect" and lane in ("root", *HOLDERS) and role != "meta":
            lane = "meta"
        elif meta_alone(role, kind, attrs):
            lane = "meta"
        elif communicates and lane in ("root", *map(project_lane, data["subcoordinators"])) and (serving := serving_lane(data, kind, attrs, answered)):
            lane = serving
        made.append(f"{lane}@{name}")
    return list(dict.fromkeys(made))


def meta_alone(role: str | None, kind: str, attrs: dict) -> bool:
    """Whether a record is the communicator's owner record about the drive, his ruling or feedback on it included, which reaches the meta session alone and goes out only through the drive's record tool, which adds the "Drive tooling:" order it carries in the same call."""
    return his_words(role, kind) and attrs.get("about") == ABOUT_DRIVE


def his_words(role: str | None, kind: str) -> bool:
    """Whether a record is the communicator's owner record of his words, which goes out only through the drive's record tool, which keeps them as a part of the open order they carry on in the same call."""
    return role == "communicator" and kind == "owner"


def unrecorded(role: str | None, kind: str, attrs: dict) -> str | None:
    """Why a record cannot go out on cci post: it is the communicator's owner record of his words, which only the drive's record tool sends, adding its order or part first; one naming nothing it is about gets unabout's refusal, which says what to name."""
    if not his_words(role, kind) or attrs.get("about") not in ABOUTS:
        return None
    if attrs.get("about") != ABOUT_DRIVE:
        return (
            f'refused, nothing posted: his words go out on the drive\'s `record` tool, kind owner, about "{ABOUT_HIS_WORK}", which keeps'
            " them as a new part of the open order its attrs or thread serve in the same call, so the watch names that part until a job"
            " or card carries it"
        )
    return (
        f'refused, nothing posted: his words about the drive go out on the drive\'s `record` tool, kind owner, about "{ABOUT_DRIVE}", whose'
        f' order field adds the "{TOOLING} <what they ask for>" order, or his words as a part of the open one they carry on, in the same'
        " call, so no word of his reaches the meta session without an order"
    )


def unabout(kind: str, attrs: dict) -> str | None:
    """Why an owner record cannot go out: it names neither the drive nor his work as what it is about, which picks the session it reaches."""
    if kind != "owner" or attrs.get("about") in ABOUTS:
        return None
    return (
        f'refused, nothing posted: an owner record names what his words are about, about "{ABOUT_DRIVE}" for drive tooling, {DRIVE_TOOLING},'
        f' his ruling or feedback on any of it included, which reaches the meta session alone, or about "{ABOUT_HIS_WORK}" for'
        " everything else, his product orders and the code they change, which reaches the coordinator hearing its order. His words:"
        ' "meta tuff does not go to the coordinator but instead fo a meta root"'
    )


def bound_drives(names: list[str]) -> dict[str, dict]:
    """Each drive of names, by name, whose records a session's post is routed in."""
    return {name: load(name) for name in names}


def hears(data: dict, order: dict, project: str | None) -> bool:
    """Whether the coordinator session for project hears an order: a sub-coordinator its project's, the top coordinator every other one, unfiled or filed under a project no sub-coordinator is bound for."""
    return order["project"] == project if project else order["project"] not in data["subcoordinators"]


def agent_gone(data: dict, job: dict) -> bool:
    """Whether the job's agent ended with its session, which no role holds any more."""
    return session_role(data, job.get("session")) is None


def runs(data: dict, order: dict, project: str | None) -> bool:
    """Whether the coordinator session for project runs an order: one it hears that neither the meta session nor the communicator holds."""
    return hears(data, order, project) and holder_lane(order) is None


def holding_lane(data: dict, order: dict) -> str:
    """The cci lane of the one session an order's watch lines reach, which alone marks its parts delivered: the communicator's or the meta session's when it holds the order, else the coordinator session's that hears it."""
    return holder_lane(order) or order_lane(data, order)


def session_lane(data: dict, session: str | None) -> str | None:
    """The cci lane a session of the drive posts with: a sub-coordinator's its project's, any other role's its own; None for a session the drive does not bind."""
    if project := session_project(data, session):
        return project_lane(project)
    role = session_role(data, session)
    return next((lane for lane, held in LANE_ROLES.items() if held == role), None)


def agent_lane(data: dict, job: dict) -> str | None:
    """The cci lane of the coordinator session whose agent runs a job on this Mac while that session hears the job's order, since only its SendMessage reaches the agent and only it hears the job's lines; None for a job no such agent runs, such as a Sprite lane's, one not started yet, one whose agent ended with its session, or one whose order moved to another coordinator session, which drive job resume hands to a fresh agent of that session."""
    if job["where"] != "local" or not job["agent"]:
        return None
    lane = session_lane(data, job["session"])
    return lane if lane == order_lane(data, data["orders"][job["order"]]) else None


def holder_lane(order: dict) -> str | None:
    """The lane of the session holding an order whose watch lines, jobs, agents and reports are its own rather than a coordinator's: the meta session's for a drive tooling order or one a brief serving drive tooling gave it, the communicator's for its read-only question; None for a coordinator's."""
    return order["owner"] if order["owner"] in HOLDERS else None


def title(role: str) -> str:
    """A role as a sentence names it, the meta session by its full name."""
    return "meta session" if role == "meta" else role


def held_elsewhere(data: dict, order_id: str, session: str | None) -> str | None:
    """Why a session may not act on an order another session of the drive holds, naming the holder, its session and the handoff record that asks it for the step; compared by holding lane rather than session role, as pr_held_elsewhere is, so the top coordinator and a sub-coordinator, both of role coordinator, are told apart; a session the drive does not bind, such as a Sprite lane's, is refused nothing, and an order from before owners is held by the first role to add a job to it."""
    order = data["orders"][order_id]
    lane = holding_lane(data, order)
    if session_lane(data, session) in (None, lane) or order["owner"] is None:
        return None
    holder, held = holding_session(data, order)
    return (
        f"{order_id} is {holder}'s order, session {held}: only {holder} adds its jobs, starts its agents,"
        f" checks its PRs and posts its reports; send {holder} the step as a handoff record on the drive's record tool instead,"
        f' to "{lane}" and attrs {{"order": "{order_id}"}}'
    )


def pr_held_elsewhere(data: dict, key: str, job_id: str) -> str | None:
    """Why a job may not take a PR the drive tracks: a job on an order another lane holds has it, and the PR's order sends its check, stage lines and wakes to that lane; compared by holding lane rather than session role, so two sub-coordinators, both of role coordinator, and a Sprite lane's call, with no session, are told apart. A job already carrying the PR, such as the one that opened it, takes it back."""
    pr = data["prs"].get(key)
    if pr is None or pr["url"] in data["jobs"][job_id]["prs"]:
        return None
    order_id = data["jobs"][job_id]["order"]
    held, own = data["orders"][pr["order"]], data["orders"][order_id]
    lane = holding_lane(data, held)
    if lane == holding_lane(data, own):
        return None
    holder, session = holding_session(data, held)
    return (
        f"{pr['job']} holds it for {pr['order']}, the order of {holder}, session {session}, while {job_id} is on {order_id}, which {holding_session(data, own)[0]} holds:"
        " the watch sends a PR's check, stage lines and wakes to the lane holding its order, so no job of another lane takes it;"
        f" put {job_id}'s change up as a PR of its own stacked on {key}'s branch, or send {holder} the step as a handoff record"
        f' on the drive\'s record tool, to "{lane}" and attrs {{"order": "{pr["order"]}"}}'
    )


def holding_session(data: dict, order: dict) -> tuple[str, str]:
    """Who holds an order and its session, "not started" while none is bound: its project's sub-coordinator, else the role holding it, the top coordinator for an order heard at root."""
    if holder_lane(order) is None and order["project"] in data["subcoordinators"]:
        return f"the sub-coordinator for {order['project']}", data["subcoordinators"][order["project"]]["session"]
    role = holder_lane(order) or "coordinator"
    return f"the {title(role)}", (data["roles"].get(role) or {}).get("session") or "not started"


def last_started(data: dict) -> str:
    return max(held["started"] for held in data["roles"].values())


def session_drives(session: str) -> list[str]:
    """The drives that bind the session, a handler's included, the newest binding first, read from each drive's saved bindings alone, so a call loads no drive it is not bound to."""
    bound = []
    for name in names():
        data = saved(name)
        bound += [(held["started"], name) for held in [*bound_sessions(data).values(), *data["handlers"].values()] if held["session"] == session]
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
    WROTE.add("events")
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


def hold_lines(name: str, project: str | None, lines: list[str]) -> None:
    """Keep the lines a watch process made and has not posted for the watch process after it, as it hands itself to a newer install or stops."""
    with held_path(name, project).open("a") as held:
        held.write("".join(f"{line}\n" for line in lines))


def take_held_lines(name: str, project: str | None = None) -> list[str]:
    """The lines the watch process before this one held for it, once, so this one hands them on before its first pass."""
    path = held_path(name, project)
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


def new_catch_up(since: str, orders: list[str]) -> dict:
    """The catch-up the drive owes him until it posts: from since, when he was last heard before he went away, over the orders with news since then, with the report of it the watch wrote, the handler last started to post it and the time he was heard that start answered."""
    return {"since": since, "orders": list(dict.fromkeys(orders)), "record": None, "handler": None, "tried": None}


def owner_away(data: dict, moment: datetime) -> bool:
    """Whether he is away: he said so, or has written nothing for an hour; a drive that never heard him counts him present."""
    owner = data.get("owner") or {}
    heard = owner.get("heard")
    return bool(owner.get("away")) or bool(heard) and moment - parse_time(heard) >= AWAY_AFTER


def open_orders(data: dict) -> dict[str, dict]:
    return {key: order for key, order in data["orders"].items() if order["state"] not in CLOSED_ORDER_STATES}


def open_incidents(data: dict, order_ids: list[str]) -> list[str]:
    return [order_id for order_id in order_ids if data["orders"][order_id]["incident"] and data["orders"][order_id]["state"] not in CLOSED_ORDER_STATES]


def hurts_now(data: dict, order: dict) -> bool:
    """Whether the bug an order fixes hurts someone now, as an incident or a bug someone reported does, or as an order joined to one does, such as the fix their cause report's FIX line named, which joins them, so its immediate fix ships at once while he picks the proper fix; a drive tooling order joined to the open order it repeats hurts no more than that order does."""
    return bool(order["incident"] or reported_bug(order) or order["joins"] and hurts_now(data, data["orders"][order["joins"]]))


def running_stops(data: dict, order_id: str) -> list[str]:
    return [job_id for job_id in data["orders"][order_id]["jobs"] if INCIDENT_STOP in data["jobs"][job_id]["parts"] and data["jobs"][job_id]["state"] not in FINISHED_JOB_STATES]


def incident_job(data: dict, name: str, order_id: str, part: str, created: str) -> str:
    order = data["orders"][order_id]
    job_id = next_id(data["jobs"], "j")
    data["jobs"][job_id] = new_job(order_id, "codex", f"{part}: {whole_order(order['text'])}", [part], created)
    order["jobs"].append(job_id)
    return f'{job_id} on "{part}": start it now with drive job launch {job_id} -, its brief on stdin ending Drive: {name}, order {order_id}, job {job_id}'


def fix_shipper(data: dict, job_id: str) -> str | None:
    """Add the builder that ships a codex job's fix once the watch took its return on an open incident's fix part the watch has not read recovered, whatever the return says, since codex edits and Claude ships, by his pick "Drive starts a Claude builder on each fix lane return: guards keep running; costs one builder and a coordinator turn per fix": a builder on that part, in the codex job's session, on the brief its launch kept, handed its report; none while a second-lane builder agent holds the codex job, since that agent ships it."""
    job = data["jobs"][job_id]
    order_id = job["order"]
    if job.get("agent") or not open_incidents(data, [order_id]) or data["orders"][order_id]["recovered"] or INCIDENT_FIX not in job_parts(job, order_id):
        return None
    builder_id = next_id(data["jobs"], "j")
    briefed = f", on its brief {job['brief']}" if job.get("brief") else ""
    flags = (job.get("brief_flags") or {}) | {"handoff": f"drive job report {job_id}, the codex fix job whose work this builder ships{briefed}"}
    data["jobs"][builder_id] = new_job(order_id, "builder", job["goal"], [INCIDENT_FIX], stamp()) | {"session": job.get("session"), "brief_flags": flags}
    data["orders"][order_id]["jobs"].append(builder_id)
    return builder_id


def promise_thread(order: dict) -> tuple[str, str] | None:
    """The channel and thread an order's lines go in, as the order keeps them, in his DM or a channel such as an alert's; None, his DM's root, for an order he gave in the thread its reporter wrote in, since that thread hears only its one note."""
    return (order["thread"][0], order["thread"][1]) if order["thread"] and not in_reporter_thread(order) else None


def in_reporter_thread(order: dict) -> bool:
    """Whether an order came in the very thread its reporter wrote in."""
    return bool(order["reporter"] and order["thread"]) and (order["reporter"]["channel"], order["reporter"]["thread"]) == tuple(order["thread"])


def thread_attr(order: dict) -> dict:
    """The thread attr of a record about an order, naming the thread its lines go in, none for one whose lines go at his DM's root."""
    return {"thread": where[1]} if (where := promise_thread(order)) else {}


def pr_threads(data: dict, keys: list[str]) -> list[tuple[str, str]]:
    """The channel and thread a card for these PRs is linked in, in his DM or a channel such as an alert's: the thread of each open order whose words they are checked against, the one each serves, its job's own and each its job carries."""
    orders = dict.fromkeys(order_id for key in keys for order_id in served_orders(data, data["prs"][key]))
    return list(
        dict.fromkeys(
            where
            for order_id in orders
            if data["orders"][order_id]["state"] not in CLOSED_ORDER_STATES and (where := promise_thread(data["orders"][order_id]))
        )
    )


def incident_threads(data: dict, order_ids: list[str]) -> list[str]:
    """The thread of each of these incident orders, where the drive says at once that a card of his blocks it."""
    return [where[1] for order_id in order_ids if (where := promise_thread(data["orders"][order_id]))]


def owed(order: dict) -> bool:
    """Whether the order owes him a line for a promised time: no drive post in its thread has kept it, and he has not withdrawn it. An incident owes none, since the watch owes him its line five quiet minutes after its last once it has news, whatever that line promised."""
    return bool(order["due"]) and not order["incident"] and not order["kept"] and not order["withdrawn"]


def owed_at(order: dict) -> str:
    return min((due for due in (order["due"], order["next_due"]) if due), key=parse_time)


def promise(order: dict, due: str, moment: datetime) -> None:
    """Promise a line by due, owed from the next line in the order's thread, the one that tells him the time; a time already owed holds until that line lands."""
    if not owed(order):
        order.update(due=due, promised=stamp(moment), kept=None, withdrawn=None, due_said=None)
    order.update(next_due=due, next_set=stamp(moment))


def remind(order: dict, due: str, moment: datetime) -> None:
    """Owe due from moment in place of any time owed or waiting before: the time a drive post made at moment promised in the order's thread, which the next post keeps, or the time an order with no thread wakes the drive at, which no post keeps, so it is said once."""
    order.update(due=due, promised=moment.isoformat(), kept=None, withdrawn=None, due_said=None, next_due=None, next_set=None)


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


def closed_orders(report: dict) -> list[str]:
    """The orders a report's close attr names, separated by commas, spaces or both, as `o1,o2` or `o1 o2`."""
    return [order for order in ORDER_LIST.split(report.get("close") or "") if order]


def second_close(data: dict, order_id: str, report: dict) -> str | None:
    """Why a report closing an order closes nothing: another report already closes it, claimed by say before its post went out, by drive order set or by the watch's post of its own close report, or, for a report not posted yet, the close report drive job prove or the watch's DONE at a merge keeps on the order for the watch to post, so he reads one close of each order whoever wrote the second, such as the watch's close report and the meta session's on one DONE."""
    order = data["orders"].get(order_id)
    if not order or order_id not in closed_orders(report):
        return None
    waiting = order["close_report"] if report.get("seq") is None else None
    closer = order["closed_on"] or (waiting and waiting["id"])
    if not closer or closer == report.get("seq"):
        return None
    posting = "" if order["closed_on"] else ", which the watch posts on its next read,"
    return f"refused, report {closer}{posting} already closes {order_id}, so this second close of it posts nothing: send no close of {order_id}, and a handler passed this one returns NOTHING {order_id} closes on report {closer}"


def part_items(report: dict, order_id: str | None) -> list[tuple[str | None, str]]:
    """The order and part each item of a report's part attr names: `<order>:<part>` items separated by `;`, as `o491:fix;o504:increase the search worker's RAM`, so one post in a thread two orders share delivers on both; an item with no order before it is a part of order_id, the order the post is recorded on."""
    items = [(named[1], named[2].strip()) if (named := ORDER_PART.fullmatch(item.strip())) else (order_id, item.strip()) for item in PART_ITEMS.split(report.get("part") or "")]
    return [(on, part) for on, part in items if part]


def keep_closing(order: dict, posts: list[datetime], written: datetime) -> None:
    """Record the line of a report that closes the order as keeping every time it owes: the first drive post after the promise, or the post that told him the time when the report was written before it, since that post is also the order's last line."""
    promised = parse_time(order["promised"])
    kept = next((post for post in posts if post > promised or post == promised >= written), None)
    if kept is not None:
        order.update(kept=kept.isoformat(), next_due=None, next_set=None)


def open_pr_keys(data: dict) -> list[str]:
    return [key for key, pr in data["prs"].items() if pr["state"] == "open"]


def unmerged_pr_keys(data: dict) -> list[str]:
    """The PRs GitHub can still change the state of: every one not merged, since a closed PR can be reopened and a merged one never unmerged."""
    return [key for key, pr in data["prs"].items() if pr["state"] != "merged"]


def open_prs(data: dict) -> list[dict]:
    return [data["prs"][key] for key in open_pr_keys(data)]


def card_wait(data: dict, wait: str) -> bool:
    """Whether a wait names a card of his, by its Slack link or waits id, rather than a job, a PR, a release of the drive or a builder slot on this Mac."""
    return wait not in data["jobs"] and wait not in data["prs"] and not RELEASE_WAIT.fullmatch(wait) and wait != SLOT_WAIT


def job_wait_refusal(data: dict, waits: list[str]) -> str | None:
    """Why a job may not wait on another job: a builder or codex job that already opened its PR is waited on by that PR instead, since a job waits on a change only until its first PR is registered, which then takes the wait's place, and a job view.holds no longer holds on leaves nothing to wait on. Any other job of any kind may be waited on, a reader that just started included."""
    from drive import view

    for wait in waits:
        if wait not in data["jobs"]:
            continue
        waited = data["jobs"][wait]
        if waited["kind"] in CHANGE_KINDS and waited["prs"]:
            keys = [pr_key(url) for url in waited["prs"]]
            return f"--waits {wait}: {wait} already opened {', '.join(keys)}, so wait on the PR this job builds on, as --waits {keys[0]}"
        if not view.holds(data, wait, None):
            return f"--waits {wait}: {wait} is {waited['state']}, so nothing of it is left to wait on"
    return None


def take_pr(data: dict, job_id: str, key: str) -> None:
    """Make a job the one answering for a PR the drive tracks: the PR's row names it, which every wake, check and lander reads, and its own PRs list the PR, the first in place of each wait on its change."""
    pr, job = data["prs"][key], data["jobs"][job_id]
    pr["job"] = job_id
    if pr["url"] not in job["prs"]:
        if not job["prs"]:
            wait_on_change(data, job_id, key)
        job["prs"].append(pr["url"])


def wait_on_change(data: dict, job_id: str, key: str) -> None:
    """Each unfinished job waiting on the change job_id builds waits on its first PR, key, in that wait's place, so it is free once that PR merges or closes, and the waits line an unstarted job was given names the PR."""
    for waiting in data["jobs"].values():
        if job_id in waiting["waits"] and waiting["state"] not in FINISHED_JOB_STATES:
            rewait(waiting, list(dict.fromkeys(key if wait == job_id else wait for wait in waiting["waits"])))


def rewait(owner: dict, waits: list[str]) -> None:
    """Give a job or PR its waits, and a job whose line is still the waits line hold_unstarted gave it that line for the waits left, or none once nothing holds it, so its line never names a wait that ended."""
    if owner.get("said") == f"waits on {', '.join(owner['waits'])}":
        owner["said"] = f"waits on {', '.join(waits)}" if waits else None
    owner["waits"] = waits


def merges_untapped(key: str) -> bool:
    return key.split("/")[0] == NO_TAP_OWNER


def guarded(key: str) -> bool:
    """Whether only his tap or an exempt class lands a PR of this repository, a Forge-AI one, whoever opened it."""
    return key.split("/")[0].lower() == GUARDED_OWNER


def queued_by_lander(key: str) -> bool:
    """Whether the drive's lander lands the PR with no session turn once his Approve or an exempt class clears it: a Forge-AI/monorepo one, which it queues through stack-enqueue."""
    return key.split("#")[0].lower() == MONOREPO.lower()


def cleared(pr: dict) -> bool:
    """Whether the PR may land with no more word from him: he approved it, or an exempt class lands it with no tap."""
    return (pr["tap"] or {}).get("label") == "Approve" or bool(exempted(pr))


def exempted(pr: dict) -> str | None:
    """The exempt class that lands the PR with no tap: its class, unless it is a flake fix changing code outside tests and fixtures, which keeps his tap and card."""
    return None if pr.get("exempt") == FLAKE_FIX and pr["wants_tap"] else pr.get("exempt")


def names_pr(words: str, key: str) -> bool:
    """Whether words name the PR by its link, its owner/name#N key or a bare #N."""
    number = key.rpartition("#")[2]
    return any(bare == number if (bare := found.groups()[-1]) else pr_key(found[0]).casefold() == key.casefold() for found in PR_REF.finditer(words))


def foreign(key: str, author: str | None, orders: Iterable[dict]) -> str | None:
    """Why the drive may not track, card, tap or land a PR: someone other than him opened it on GitHub, and none of the orders of his it serves names it by link, key or #N; None for his own, one such an order names, or one whose author no GitHub read has given since this install, which the watch's next read does."""
    if author is None or author.casefold() == HIS_LOGIN or any(names_pr(order["text"], key) for order in orders):
        return None
    return (
        f"{key} was opened on GitHub by {author}, not {HIS_LOGIN}, and no order of his it serves names it by link or #N: the drive"
        f" tracks, cards, taps, rebases and lands no one else's PR unless his order's words name it, by his ruling {FOREIGN_RULING}"
    )


def served(data: dict, pr: dict) -> Iterator[dict]:
    """The orders of his a PR serves: its job's, and the one `drive pr set --serves` names."""
    return (data["orders"][order_id] for order_id in dict.fromkeys((pr["order"], pr.get("serves"))) if order_id)


def foreign_pr(data: dict, key: str) -> str | None:
    """Why the drive may not keep a PR it tracks, from the author its last GitHub read stored."""
    return foreign(key, data["prs"][key]["author"], served(data, data["prs"][key]))


def drop_pr(data: dict, key: str) -> None:
    """Stop tracking a PR, with nothing done on GitHub: its entry, its link on each job, each release wait on it, its place in each stack a card drew, and each drive pr set --flaky record naming it the fix, whose ejection reads final again; a wait on the PR itself ends as one on a PR the drive no longer tracks."""
    url = data["prs"].pop(key)["url"]
    for owner in [*data["jobs"].values(), *data["prs"].values()]:
        rewait(owner, [wait for wait in owner["waits"] if not ((release := RELEASE_WAIT.fullmatch(wait)) and release["pr"] == key)])
    for job in data["jobs"].values():
        job["prs"] = [held for held in job["prs"] if held != url]
    for pr in data["prs"].values():
        if pr.get("stack") and key in pr["stack"]:
            pr["stack"] = [member for member in pr["stack"] if member != key]
        if (pr["flaky"] or {}).get("fix") == key:
            pr["flaky"] = None


def order_served(pr: dict) -> str:
    """The order a PR carries out, its job's until `drive pr set --serves` names another."""
    return pr.get("serves") or pr["order"]


def card_unit(key: str, stack: tuple[str, ...], pr: dict) -> tuple[tuple[str, ...], str, str]:
    """The card a PR draws on, the one rule `drive card` and the watch's draw of an uncarded PR share: the stack his Approve lands it with, a native GitHub stack or PRs each on the one below's head branch, or for a PR in none, its repository's PRs serving its order, so sibling PRs of one order ready together draw one card."""
    return (stack, "", "") if stack else ((), key.split("#")[0], order_served(pr))


def served_orders(data: dict, pr: dict) -> list[str]:
    """The orders whose words a PR's change is checked against: none for a flake fix, which his ruling FLAKE_RULING alone judges; the one `drive pr set --serves` names alone, else its job's own, the order of every other job carrying it, a builder that took the PR over included, and each order those jobs carry."""
    if pr.get("exempt") == FLAKE_FIX:
        return []
    if pr.get("serves"):
        return [pr["serves"]]
    jobs = [data["jobs"][job_id] for job_id in pr_jobs(data, pr)]
    return list(dict.fromkeys([pr["order"], *(order_id for job in jobs for order_id in (job["order"], *job["carries"]))]))


@contextmanager
def indexed_pr_jobs(data: dict) -> Iterator[None]:
    """Index PR ownership once for a read-only projection of this drive. Jobs and their PR lists must remain unchanged inside the projection; nested projections and other threads retain their own scope."""
    by_url: dict[str, list[str]] = {}
    for job_id, job in data["jobs"].items():
        for url in dict.fromkeys(job["prs"]):
            by_url.setdefault(url, []).append(job_id)
    token = PR_JOB_INDEX.set((data, by_url))
    try:
        yield
    finally:
        PR_JOB_INDEX.reset(token)


def pr_jobs(data: dict, pr: dict) -> list[str]:
    """The jobs carrying a PR: its own, then each other job listing it among its PRs, a builder that took it over or one that holds its landing included."""
    if (indexed := PR_JOB_INDEX.get()) is not None and indexed[0] is data:
        return [pr["job"], *(job_id for job_id in indexed[1].get(pr["url"], ()) if job_id != pr["job"])]
    return [pr["job"], *(job_id for job_id, job in data["jobs"].items() if job_id != pr["job"] and pr["url"] in job["prs"])]


def checked(data: dict, pr: dict) -> bool:
    """Whether the PR's change at its head passed the check against the words of every order it serves."""
    stamp = pr["checked"]
    return bool(stamp) and stamp["head"] == pr["head"] and set(served_orders(data, pr)) <= set(stamp["orders"])


def has_verdict(data: dict, pr: dict) -> bool:
    """Whether the check against his words has a verdict on the PR's change at its head, a pass or an objection, so his card may draw on it, an objection as a line on it."""
    return checked(data, pr) or bool(against(pr))


def against(pr: dict) -> str:
    """Each of his words the PR's change at its head goes against, as the watch's check judged that head, or "" when no check judged it against them."""
    checker = pr["checker"]
    return checker["against"] if checker and checker["head"] == pr["head"] else ""


def tap_refused(pr: dict) -> str | None:
    """Why his card on the PR offers no Approve; None when it does: open, waiting on his tap with no exempt class landing it, and neither his Approve, his Hold nor the hold label on it, whatever the check against his words says. A red PR offers it, and so does one the check objects to, the objection a line on the card."""
    if pr["state"] != "open":
        return f"it is {pr['state']}"
    if exempted(pr):
        return f'its exempt class "{pr["exempt"]}" lands it with no tap'
    if not pr["wants_tap"]:
        return "its author or its own builder lands it with no tap"
    match (pr["tap"] or {}).get("label"):
        case "Approve":
            return "he approved it already"
        case "Hold":
            return "he held it"
    if pr["hold_label"]:
        return "it carries the hold label"
    return None


def asks_tap(pr: dict) -> bool:
    return tap_refused(pr) is None


def rework(pr: dict, by: str, words: str) -> None:
    """Mark a PR reworked to his words, by his order or answer, until its builder's READY says how they are met; his Approve stands until that READY, which drops it so a fresh card asks it for the reworked change."""
    at = stamp()
    pr.update(rework={"by": by, "words": words, "at": at}, updated=at)


def rework_reason(pr: dict) -> str:
    return f'Being reworked to your words "{pr["rework"]["words"]}". A fresh card asks your tap once its builder says how they are met'


def new_tap(label: str, head: str, source: str, action_ts: str | None = None, via: str | None = None, at: str | None = None) -> dict:
    """His tap on a PR, the one shape every writer stores: what he pressed or said, when, on which head, its source, the card press's Slack action, and the PR of its landing stack it came from."""
    return {"label": label, "at": at or stamp(), "head": head, "source": source, "action_ts": action_ts, "via": via}


def new_pr(url: str, job_id: str, order_id: str) -> dict:
    return {
        "url": url,
        "job": job_id,
        "order": order_id,
        "wants_tap": True,
        "tap": None,
        "exempt": None,
        "fundamental": None,
        "state": "open",
        "title": "",
        "head": "",
        "checks": "pending",
        "failed_checks": [],
        "required": None,
        "review": "none",
        "conflict": False,
        "hold_label": False,
        "native": [],
        "reviews_required": 1,
        "base": None,
        "branch": None,
        "draft": False,
        "merged_sha": None,
        "stage": None,
        "card": None,
        "added": stamp(),
        "waits": [],
        "lander": None,
        "checker": None,
        "ejected": False,
        "flaky": None,
        "review_asked": None,
        "rework": None,
        "author": None,
        "checked": None,
        "undrawn": None,
        "parts": [],
        "lead": None,
        "design": None,
        "answers": None,
        "updated": stamp(),
    }
