"""Each project's Claude spend today across every drive on this Mac, read from its jobs' transcripts, and the daily budgets caps.json sets on it."""

import contextlib
import fcntl
import json
import os
from datetime import datetime
from collections.abc import Iterator
from pathlib import Path

from drive import state, watch

SPEND_DIR = "spend"
SPEND_SECONDS = 300
TOOLING_PROJECT = "Drive tooling"
UNFILED = "unfiled"
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
BUDGETS = {"daily_tokens": 40_000_000_000, "tooling_share": 0.25, "project_tokens": 10_000_000_000, "projects": {}}


def budgets() -> dict:
    """The daily budgets caps.json sets beside its caps, BUDGETS for each it leaves out: daily_tokens, the day's total, of which drive tooling may spend tooling_share, and project_tokens, what each other project may spend a day unless projects names its own, by its name."""
    from drive import place

    path = state.root() / place.CAPS_FILE
    return BUDGETS | {key: value for key, value in (json.loads(path.read_text()) if path.is_file() else {}).items() if key in BUDGETS}


def project(data: dict, job: dict) -> str:
    """The project a job's spend counts against: drive tooling for a job serving a tooling order, else its order's project."""
    orders = [data["orders"][order_id] for order_id in [job["order"], *job["carries"]] if order_id in data["orders"]]
    if any(state.tooling(order) for order in orders):
        return TOOLING_PROJECT
    return next((order["project"] for order in orders if order.get("project")), UNFILED)


def transcripts(data: dict, job: dict) -> list[Path]:
    """Every transcript on this Mac a job's workers wrote: its agent's here, each session's in the worktree its lane here ran in, and each one a dead or ended Sprite lane's copy keeps; a lane at work on a Sprite counts once its transcripts are copied here."""
    found = []
    if job.get("agent") and job.get("where") != "remote" and (state.own_transcript(data, job) or watch.session_of(job, data)):
        found.append(watch.agent_transcript(watch.session_of(job, data), job, data))
    if job.get("worktree"):
        found += (watch.config_dir() / "projects" / watch.NOT_SLUG.sub("-", job["worktree"])).glob("*.jsonl")
    for death in job.get("deaths", []):
        if kept := death.get("transcript"):
            found += [Path(kept)] if kept.endswith(".jsonl") else Path(kept).glob("**/*.jsonl")
    return [path for path in dict.fromkeys(found) if path.is_file()]


def ledger_path(moment: datetime) -> Path:
    return state.root() / SPEND_DIR / f"{moment.date().isoformat()}.json"


def read_since(path: Path, held: list, day: str) -> int:
    """The tokens a transcript's assistant turns dated day spent past the offset held keeps, each message once though Claude Code writes one line per content block with the same usage, a line cut short by a crash or a copy read as no turn, moving held to where this read ended and the last message it counted."""
    offset, last = held
    with open(path, "rb") as handle:
        handle.seek(offset)
        chunk = handle.read()
    whole = chunk.rfind(b"\n") + 1
    spent = 0
    for line in chunk[:whole].splitlines():
        if b'"usage"' not in line or b'"assistant"' not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = entry.get("message") or {}
        if entry.get("type") != "assistant" or not (usage := message.get("usage")) or message.get("id") == last:
            continue
        last = message.get("id")
        if state.parse_time(entry["timestamp"]).astimezone().date().isoformat() == day:
            spent += sum(usage.get(key) or 0 for key in USAGE_KEYS)
    held[:] = [offset + whole, last]
    return spent


def kept(moment: datetime) -> dict:
    """Today's ledger as the last refresh left it, empty before the day's first: {"at", "offsets", "tokens": {"<drive>/<project>": n}, "told": [project, ...]}."""
    with contextlib.suppress(FileNotFoundError):
        return json.loads(ledger_path(moment).read_text())
    return {"at": None, "offsets": {}, "tokens": {}, "told": []}


@contextlib.contextmanager
def ledger(moment: datetime) -> Iterator[dict]:
    """Today's ledger under the one lock every drive's watch shares, written back whole once the body changed it."""
    path = ledger_path(moment)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        held = kept(moment)
        yield held
        made = path.with_name(f"{path.name}.{os.getpid()}")
        made.write_text(json.dumps(held))
        os.replace(made, path)


def refresh(moment: datetime) -> dict:
    """Read every drive's projects' spend today again once the kept ledger is SPEND_SECONDS old, from the transcripts of each job not finished or touched today, each from where the last read ended, so a read costs what was written since; the watch's pass runs it, never an admission, which reads the kept ledger alone, and a pass that finds another watch reading takes the kept ledger rather than wait for it."""
    path = ledger_path(moment)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return kept(moment)
        return refreshed(path, moment)


def refreshed(path: Path, moment: datetime) -> dict:
    """The ledger refresh reads again, written back whole; its caller holds the ledger's lock."""
    held = kept(moment)
    if held["at"] and (moment - state.parse_time(held["at"])).total_seconds() < SPEND_SECONDS:
        return held
    day = moment.date().isoformat()
    for name in state.names():
        data = state.load(name)
        for job in data["jobs"].values():
            if job["state"] in state.FINISHED_JOB_STATES and state.parse_time(job["updated"]).astimezone().date().isoformat() != day:
                continue
            bucket = f"{name}/{project(data, job)}"
            for transcript in transcripts(data, job):
                offset = held["offsets"].setdefault(str(transcript), [0, None])
                held["tokens"][bucket] = held["tokens"].get(bucket, 0) + read_since(transcript, offset, day)
    held["at"] = state.stamp(moment)
    made = path.with_name(f"{path.name}.{os.getpid()}")
    made.write_text(json.dumps(held))
    os.replace(made, path)
    return held


def spent(tokens: dict[str, int], mine: str) -> int:
    """A project's spend today summed over every drive that files work under its name."""
    return sum(used for bucket, used in tokens.items() if bucket.rpartition("/")[2] == mine)


def limit(mine: str) -> int:
    """A project's daily budget: drive tooling's tooling_share of daily_tokens, any other project's own in projects, else project_tokens."""
    held = budgets()
    if mine == TOOLING_PROJECT:
        return int(held["tooling_share"] * held["daily_tokens"])
    return held["projects"].get(mine, held["project_tokens"])


def over_project(mine: str, moment: datetime) -> str | None:
    """Why a project may spend no more today, by the kept ledger, None while it may."""
    used, budget = spent(kept(moment)["tokens"], mine), limit(mine)
    return f"{mine} spent {used:,} tokens today, at its daily budget of {budget:,} set by caps.json" if used >= budget else None


def over(data: dict, job: dict, moment: datetime) -> str | None:
    """Why a job's project may spend no more today, by over_project."""
    return over_project(project(data, job), moment)
