"""Each project's Claude spend today in dollars across every drive on this Mac, priced from its jobs' transcripts, and the daily budgets caps.json sets on it."""

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
PRICES = {
    "claude-fable-5-2": (8.0, 40.0, 0.20),
    "claude-fable-5-1": (10.0, 50.0, 0.50),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.25),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
    "claude-sonnet-5": (2.0, 10.0, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.005),
    "claude-haiku-4-5": (1.0, 5.0, 0.05),
}
FAMILIES = {"fable": "claude-fable-5-2", "opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}
SYNTHETIC = "<synthetic>"
ADVISOR = "advisor_message"
CACHE_WRITE = 1.25
CACHE_WRITE_HOUR = 2.0
BUDGETS = {"daily_usd": 15000, "tooling_share": 0.25, "project_usd": 5000, "projects": {}}


def budgets() -> dict:
    """The daily budgets in dollars caps.json sets beside its caps, BUDGETS for each it leaves out: daily_usd, the day's total, of which drive tooling may spend tooling_share, and project_usd, what each other project may spend a day unless projects names its own, by its name."""
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
    if job.get("agent") and job.get("where") != "remote" and (state.own_transcript(job) or watch.session_of(job, data)):
        found.append(watch.agent_transcript(watch.session_of(job, data), job, data))
    if job.get("worktree"):
        found += (watch.config_dir() / "projects" / watch.NOT_SLUG.sub("-", job["worktree"])).glob("*.jsonl")
    for death in job.get("deaths", []):
        if kept := death.get("transcript"):
            found += [Path(kept)] if kept.endswith(".jsonl") else Path(kept).glob("**/*.jsonl")
    return [path for path in dict.fromkeys(found) if path.is_file()]


def ledger_path(moment: datetime) -> Path:
    return state.root() / SPEND_DIR / f"{moment.date().isoformat()}.usd.json"


def priced(model: str, usage: dict) -> float:
    """What one message cost in dollars at its model's list price per million tokens, a new model by its family's newest: input, output and cache reads at their own prices, a cache write at CACHE_WRITE times input, or CACHE_WRITE_HOUR for one kept an hour, plus each advisor call its usage iterations hold, at the advisor's own model, which the message's top-level usage leaves out; Claude Code's synthetic messages cost nothing."""
    if model == SYNTHETIC:
        return 0.0
    given, made, reading = PRICES.get(model) or PRICES[next(newest for family, newest in FAMILIES.items() if family in model)]
    written = usage.get("cache_creation_input_tokens") or 0
    hour = (usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0
    taken = (usage.get("input_tokens") or 0) + CACHE_WRITE * (written - hour) + CACHE_WRITE_HOUR * hour
    advised = sum(priced(step["model"], step) for step in usage.get("iterations") or [] if step.get("type") == ADVISOR)
    return (given * taken + reading * (usage.get("cache_read_input_tokens") or 0) + made * (usage.get("output_tokens") or 0)) / 1e6 + advised


def money(usd: float) -> str:
    """Dollars as he reads them: $3.8k, $950."""
    return f"${usd / 1000:.1f}k".replace(".0k", "k") if usd >= 1000 else f"${usd:,.0f}"


def read_since(path: Path, held: list, day: str) -> float:
    """The dollars a transcript's assistant turns dated day spent, by priced, past the offset held keeps, each message once though Claude Code writes one line per content block with the same usage, a line cut short by a crash or a copy read as no turn, moving held to where this read ended and the last message it counted."""
    offset, last = held
    with open(path, "rb") as handle:
        handle.seek(offset)
        chunk = handle.read()
    whole = chunk.rfind(b"\n") + 1
    spent = 0.0
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
            spent += priced(message.get("model") or SYNTHETIC, usage)
    held[:] = [offset + whole, last]
    return spent


def kept(moment: datetime) -> dict:
    """Today's ledger as the last refresh left it, empty before the day's first: {"at", "offsets", "usd": {"<drive>/<project>": dollars}, "told": [project, ...]}."""
    with contextlib.suppress(FileNotFoundError):
        return json.loads(ledger_path(moment).read_text())
    return {"at": None, "offsets": {}, "usd": {}, "told": []}


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
                held["usd"][bucket] = held["usd"].get(bucket, 0.0) + read_since(transcript, offset, day)
    held["at"] = state.stamp(moment)
    made = path.with_name(f"{path.name}.{os.getpid()}")
    made.write_text(json.dumps(held))
    os.replace(made, path)
    return held


def spent(usd: dict[str, float], mine: str) -> float:
    """A project's dollars spent today summed over every drive that files work under its name."""
    return sum(used for bucket, used in usd.items() if bucket.rpartition("/")[2] == mine)


def limit(mine: str) -> float:
    """A project's daily budget in dollars: drive tooling's tooling_share of daily_usd, any other project's own in projects, else project_usd."""
    held = budgets()
    if mine == TOOLING_PROJECT:
        return held["tooling_share"] * held["daily_usd"]
    return held["projects"].get(mine, held["project_usd"])


def over_project(mine: str, moment: datetime) -> str | None:
    """Why a project may spend no more today, by the kept ledger, None while it may."""
    used, budget = spent(kept(moment)["usd"], mine), limit(mine)
    return f"{mine} hit today's {money(budget)} budget, at {money(used)} spent, set by caps.json" if used >= budget else None


def his_line(mine: str) -> str:
    """The one line he gets the first time a project hits its budget on a day, in plain dollars."""
    work = "tooling" if mine == TOOLING_PROJECT else f"{mine} work"
    return f"{mine} hit today's {money(limit(mine))} budget; non-urgent {work} waits until tomorrow."


def over(data: dict, job: dict, moment: datetime) -> str | None:
    """Why a job's project may spend no more today, by over_project."""
    return over_project(project(data, job), moment)
