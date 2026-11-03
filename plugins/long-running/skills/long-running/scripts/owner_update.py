#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""What the owner's next update must cover, read from the lane registry, the PR ledger, the board and cci, never from memory.

    owner_update.py --drive D [--since TIME] [--kind KIND]... [--only SECTION]... [--json]
    owner_update.py --drive D --render
    owner_update.py --drive D --check FILE|-

STDLIB ONLY. The drive's root is its newest registry session; its task list is
`$CLAUDE_CONFIG_DIR/tasks/session-<root8>/`. Prints one line per item, in five sections:

    ask <state> <key> <lane>: <words> | last <kind> <time>
    in flight #<task> <lane>: <subject> | last <kind> <time>       (or: no record in 24 h)
    delivered #<task> <lane>: <subject> | <kind> #<seq> <time>: <text>
    board #<pr> at <head7>[: approved|rejected]
    incident #<seq> <lane> <time>: <text>

An ask is every owner ask the PR ledger holds that is neither dropped nor answered, graded
as `ledger.py summary` grades it, read without the reconcile that command writes first.
In flight is every in-progress task whose owner is a lane. Delivered is every open task
whose lane posted a `report`, `done`, `fix-live` or `handoff` record after `--since`
(default 2 h ago), the newest such record per lane; each `--kind` replaces those kinds,
as the compaction nudge does with :data:`REPORTED_KINDS` to add `head`. The board is
every PR row on the root's live board; a board that does not read prints
`board unread: <why>` and the rest still prints.
Incidents are the drive's open `incident` records. Times are Pacific.

Each `--only` limits the reads to the sections it names, as the compaction nudge reads
only `delivered`. `--json` prints `{since, asks, in_flight, delivered, board, incidents}`,
each a list of objects carrying a `line`; the root's Stop hook reads it to refuse an
update to the owner that omits a delivered item or calls an in-flight one not started.

`--render` prints the whole owner update as markdown from sources only, never the task
list or memory. The lane supervisor's registry (`drive_supervisor.py lanes --json`) gives
every lane, its brief's step, standing duty and deliverable, and the PRs it owns; cci gives
each lane's newest record, the asks and blockers waiting on root, incidents and releases
since midnight Pacific; the ledger gives owner asks, LANDED-NOT-LIVE first, and every open
PR with why it waits; the board gives open picks. Sections are derived, never listed by
hand: standing duties, every row of the tables in the `<program>: owner report table`
doc with the lane on it now or **nobody**, unowned rows first, one per step of the
program's plans holding the lanes whose
`Register slice: ... step=` names it, one per other step a brief names, lanes on no step,
then the registry lanes quiet over a day, and the rest. Every row, a standing duty, plan
step or piece, lane, owner ask, incident, release or PR, carries the owner report table's
columns (`reference/owner-report-table.md`): what it delivers, state, who is on it right now
(a lane heard within 2 h, or **nobody**), what blocks it, what is being done to unblock it
and by when (each lane's newest record and its next record due by the supervisor's stale
line), and what went wrong; every open row with nobody on it also leads the update as
unowned work. A section with nothing says so in
one line. It exits 1 printing to stderr `missing: <count>` and then, one per line, each section,
working lane, open ask and open PR the render left out. `--check` reads an update the root wrote and exits 1 naming each of those it
leaves out, which the root's Stop hook turns into a nudge; the compaction hook adds the
render to the progress record so the root after compaction inherits every lane.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from long_running import cci
from long_running import drive
from long_running import incidents as incident_log
from long_running import ledger
import owner_waits
import plans
from lr.store import STALE_AFTER

DELIVERED_KINDS = frozenset({"report", "done", "fix-live", "handoff"})
REPORTED_KINDS = DELIVERED_KINDS | {"head"}
CLOSED_ASKS = frozenset({ledger.ASK_DROPPED, ledger.ASK_ANSWERED})
OPEN_TASKS = frozenset({"in_progress", "pending"})
RECORD_WINDOW = "24h"
DEFAULT_SINCE = timedelta(hours=2)
TEXT_CHARS = 160
SCRIPTS = Path(__file__).resolve().parent
LIVE_LANES = frozenset({"launching", "desired", "held"})
POSTING = LIVE_LANES | {"unregistered"}
LANE_NAME = re.compile(r"[a-z][\w.-]*")
QUIET_AFTER = timedelta(hours=24)
WAITS_ON_ROOT = frozenset({"ask", "blocker", "blocked"})
INCIDENT_ROLE = re.compile(r"-(?:fix|evidence|ship|monitor|backup|diag)(?:-\d+)?$")
ROOT = "root"
SELVES = frozenset({ROOT, "main", "supervisor"})
COORDINATORS = frozenset({ROOT, "main", "all", "desk-runner", "stall-watch", "supervisor"})
INCIDENT_SLUG = re.compile(r"\bINCIDENT (?:OPEN |REOPENED )?(?P<slug>[a-z][a-z0-9-]+[a-z0-9])\b")
NOTHING = "- nothing new"
NOBODY = "**nobody**"
UNOWNED = "Unowned work"
OPEN_PRS = "Open PRs"
COLUMNS = "| Row | What it delivers | State | Who is on it right now | What blocks it | What we are doing to unblock it, and by when | What went wrong or slowed it |"
PARENTHETICAL = re.compile(r"\([^)]*\)")
ASK_WRONG = {ledger.ASK_LOST: "no PR and no record carry it", ledger.ASK_STALE: "no progress past its due time", ledger.ASK_LANDED_NOT_LIVE: "landed but not live for users"}
PR_WRONG = {"red": "its head is red", "refused": "the landing gate refused its head", "held": "a hold stops it"}

Runner = Callable[..., subprocess.CompletedProcess]


def tasks_of(session: str) -> list[dict]:
    folder = drive.config_dir() / "tasks" / f"session-{session[:8]}"
    rows = [json.loads(path.read_text()) for path in folder.glob("*.json")] if folder.is_dir() else []
    return sorted(rows, key=lambda row: int(row["id"]) if row["id"].isdigit() else 0)


def newest_by_lane(records: list[dict]) -> dict[str, dict]:
    return {record["lane"]: record for record in sorted(records, key=lambda record: record["seq"])}


def when(record: dict) -> str:
    return owner_waits.pacific(owner_waits.at(record))


def last_words(record: dict | None) -> str:
    return f"last {record['kind']} {when(record)}" if record else f"no record in {RECORD_WINDOW.removesuffix('h')} h"


def ask_rows(runner: Runner, checkout: str, ledger_id: str, records: dict[str, dict]) -> list[dict]:
    done = runner(["ccn", "-R", checkout, "ledger", "show", ledger_id, "--json"])
    if done.returncode:
        raise owner_waits.ReadFailed(f"ccn ledger show exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}")
    rows = {row["key"]: row["fields"] for row in json.loads(done.stdout)["rows"] if not ledger.is_malformed(row["key"])}
    prs, live, moment = ledger.pull_rows(rows), ledger.live_since(rows), ledger.now()
    asks = []
    for key, fields in rows.items():
        if not key.startswith(ledger.ASK_PREFIX) or (state := ledger.ask_state(fields, prs, moment, live)) in CLOSED_ASKS:
            continue
        last = records.get(fields["lane"])
        asks.append({"key": key, "lane": fields["lane"], "state": state or "fresh", "text": fields["text"], "line": f"ask {state or 'fresh'} {owner_waits.clip(ledger.ask_line(key, fields))} | {last_words(last)}"})
    return asks


def in_flight(tasks: list[dict], records: dict[str, dict]) -> list[dict]:
    return [
        {"task": task["id"], "lane": task["owner"], "subject": task["subject"], "last": records.get(task["owner"]), "line": f"in flight #{task['id']} {task['owner']}: {owner_waits.clip(task['subject'])} | {last_words(records.get(task['owner']))}"}
        for task in tasks
        if task["status"] == "in_progress" and task.get("owner")
    ]


def delivered(tasks: list[dict], records: list[dict], since: datetime, kinds: frozenset[str] = DELIVERED_KINDS) -> list[dict]:
    """The newest deliverable record after `since` of each lane that owns an open task, with that task, its in-progress one first."""
    owned: dict[str, dict] = {}
    for task in sorted(tasks, key=lambda task: task["status"] != "in_progress"):
        if task["status"] in OPEN_TASKS and task.get("owner"):
            owned.setdefault(task["owner"], task)
    newest = newest_by_lane([record for record in records if record["kind"] in kinds and owner_waits.at(record) > since and record["lane"] in owned])
    return [
        {
            "task": (task := owned[lane])["id"],
            "lane": lane,
            "subject": task["subject"],
            "seq": record["seq"],
            "kind": record["kind"],
            "at": record["at"],
            "text": (text := owner_waits.clip(record.get("text") or "")),
            "prs": sorted(set(cci.NAMED_PR.findall(record.get("text") or "")) | {str(pr) for pr in (record.get("refs") or {}).get("prs", [])}),
            "line": f"delivered #{task['id']} {lane}: {owner_waits.clip(task['subject'])} | {record['kind']} #{record['seq']} {when(record)}: {text}",
        }
        for lane, record in newest.items()
    ]


def board_rows(runner: Runner, drive_name: str, session: str) -> list[dict]:
    try:
        board = owner_waits.read_board(drive_name, session, runner)
    except owner_waits.ReadFailed as failed:
        return [{"pr": "", "head": "", "verdict": "", "line": f"board unread: {failed}"}]
    return [
        {"pr": row.pr, "head": row.head, "verdict": (verdict := board.decided(row)), "line": f"board #{row.pr} at {row.head[:7]}" + (f": {verdict}" if verdict else "")}
        for row in board.rows
        if row.head
    ]


def incidents(runner: Runner, drive_name: str) -> list[dict]:
    done = runner(["cci", "digest", "--drive", drive_name, "--json", "--since", RECORD_WINDOW, "--budget", str(cci.PAGE_BYTES)])
    if done.returncode:
        raise owner_waits.ReadFailed(f"cci digest exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}")
    found = json.loads(done.stdout).get("open_incidents") or []
    return [{"seq": record["seq"], "lane": record["lane"], "at": record["at"], "text": record.get("text") or "", "line": f"incident #{record['seq']} {record['lane']} {when(record)}: {owner_waits.clip(record.get('text') or '')}"} for record in found]


def since_of(value: str | None, now: datetime) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else now - DEFAULT_SINCE


SECTIONS = ("asks", "in_flight", "delivered", "board", "incidents")


def update(drive_name: str, since: datetime, runner: Runner, kinds: frozenset[str] = DELIVERED_KINDS, only: tuple[str, ...] = SECTIONS) -> dict:
    entry = next(entry for entry in drive.drives() if drive.cci_drive(entry) == drive_name and entry.get("sessions"))
    session = entry["sessions"][-1]
    tasks = tasks_of(session)
    lanes = sorted({task["owner"] for task in tasks if task.get("owner") and task["status"] in OPEN_TASKS})
    window = min(since, datetime.now(timezone.utc) - timedelta(hours=int(RECORD_WINDOW.removesuffix("h")))).isoformat()
    records = cci.records(owner_waits.tail(runner), drive_name, *(f"--lane={lane}" for lane in lanes), since=window) if lanes else []
    latest = newest_by_lane(records)
    reads = {
        "asks": lambda: ask_rows(runner, entry["checkout"], entry["ledger"], latest),
        "in_flight": lambda: in_flight(tasks, latest),
        "delivered": lambda: delivered(tasks, records, since, kinds),
        "board": lambda: board_rows(runner, drive_name, session),
        "incidents": lambda: incidents(runner, drive_name),
    }
    return {"since": since.isoformat()} | {section: reads[section]() if section in only else [] for section in SECTIONS}


def registry(runner: Runner, entry: dict) -> list[dict]:
    """Every row of the lane supervisor's registry, each carrying its brief's step, standing duty and deliverable."""
    done = runner([sys.executable, str(SCRIPTS / "drive_supervisor.py"), "lanes", "--config", str(Path(entry["state_dir"]) / "lr.json"), "--json"])
    if done.returncode:
        raise owner_waits.ReadFailed(f"drive_supervisor.py lanes exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}")
    return [json.loads(line) for line in done.stdout.splitlines() if line.strip()]


def heard_at(row: dict) -> datetime | None:
    return datetime.fromisoformat(row["heard"].replace("Z", "+00:00")) if row.get("heard") else None


def working(rows: list[dict], now: datetime) -> list[dict]:
    """The lanes the update must name: each one not retired and heard within `QUIET_AFTER`, registered or not, besides root and the supervisor, and each the brief of which keeps a standing duty."""
    return [
        row
        for row in rows
        if row.get("standing") and row["state"] != "retired"
        or (row["state"] in POSTING and row["lane"] not in SELVES and LANE_NAME.fullmatch(row["lane"]) and (heard := heard_at(row)) and now - heard < QUIET_AFTER)
    ]


def head_of(row: dict, by_name: dict[str, dict]) -> dict:
    seen = {row["lane"]}
    while row["state"] == "retired" and (after := by_name.get(row.get("retired_by") or "")) and after["lane"] not in seen:
        row = after
        seen.add(row["lane"])
    return row


def step_of(lane: dict) -> str:
    """The step a lane's brief names, or "" for none and for `all`, the slice the supervisor gives a successor whose predecessor named none."""
    return "" if lane.get("step") in (None, "", "all") else lane["step"]


def on_step(lane: dict, step: plans.Step) -> bool:
    return bool(named := step_of(lane)) and (named == step.number or named.startswith(f"{step.number}."))


def section(title: str, lines: list[str]) -> list[str]:
    return ["", f"## {title}", *(lines or [NOTHING])]


def waiting_on_root(runner: Runner, drive_name: str) -> list[dict]:
    """Every open ask or blocker addressed to root, from cci's open-record digest."""
    done = runner(["cci", "digest", "--drive", drive_name, "--json", "--since", RECORD_WINDOW, "--budget", str(cci.PAGE_BYTES)])
    if done.returncode:
        raise owner_waits.ReadFailed(f"cci digest exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}")
    found = json.loads(done.stdout)
    opened = [*(found.get("open_asks") or []), *(found.get("open_blockers") or [])]
    return sorted((record for record in opened if record["kind"] in WAITS_ON_ROOT and {to.split("@", 1)[0] for to in record.get("to") or []} & {ROOT, "main"}), key=lambda record: record["seq"])


def incident_name(record: dict) -> str:
    """The incident a record is about: the slug a root `INCIDENT [OPEN|REOPENED] <slug>` names, else its lane, else for a coordinator's record the lane it addresses."""
    if slug := INCIDENT_SLUG.search(record.get("text") or ""):
        return slug["slug"]
    addressed = [to.split("@", 1)[0] for to in record.get("to") or []]
    return record["lane"] if record["lane"] not in COORDINATORS else next((to for to in addressed if to not in COORDINATORS), record["lane"])


@dataclass(frozen=True)
class Row:
    """One row of the owner report table: the columns `reference/owner-report-table.md` defines, each filled from a source."""

    name: str
    delivers: str
    state: str
    who: str
    blocks: str
    unblock: str
    wrong: str
    done: bool = False

    @property
    def unowned(self) -> bool:
        return self.who == NOBODY and not self.done


@dataclass(frozen=True)
class Sources:
    """What every row reads: the registry rows by lane, each lane's newest record, and each lane's open ask or blocker to root."""

    by_lane: dict[str, dict]
    latest: dict[str, dict]
    waits: dict[str, dict]
    now: datetime

    def live(self, lane: str) -> bool:
        return lane not in SELVES and (row := self.by_lane.get(lane)) is not None and row["state"] in POSTING and (heard := heard_at(row)) is not None and self.now - heard < DEFAULT_SINCE

    def work(self, lanes: list[str]) -> tuple[str, str, str, str]:
        """Who is on a piece of work held by `lanes`, what blocks it, what they are doing about it and by when, and what slowed it: every lane heard within `DEFAULT_SINCE` is on it, and nobody else."""
        on = [lane for lane in dict.fromkeys(lanes) if self.live(lane)]
        blocks = "; ".join(f"{lane} waits on root's answer to {wait['kind']} #{wait['seq']} since {when(wait)}: {owner_waits.clip(wait.get('text') or '')}" for lane in on if (wait := self.waits.get(lane))) or "nothing recorded"
        unblock = "; ".join(
            f"{lane}: {last['kind']} {when(last)}: {owner_waits.clip(last.get('text') or '')}; next record due by {owner_waits.pacific(heard_at(self.by_lane[lane]) + STALE_AFTER)}"
            for lane in on
            if (last := self.latest.get(lane))
        ) or ("nobody is on it, so root starts a lane on it this turn" if not on else "; ".join(f"{lane}: no record in a day" for lane in on))
        quiet = [f"{lane} quiet since {owner_waits.pacific_text(self.by_lane[lane].get('heard') or '')}" for lane in dict.fromkeys(lanes) if lane in self.by_lane and lane not in SELVES and not self.live(lane)]
        took = [f"{lane} took over from {row['predecessor']}" for lane in on if (row := self.by_lane[lane]).get("predecessor")]
        return ", ".join(on) or NOBODY, blocks, unblock, "; ".join([*quiet, *took]) or "None recorded"


def cell(text: str) -> str:
    return owner_waits.clip(text).replace("|", "/")


def table(rows: list[Row]) -> list[str]:
    return [COLUMNS, "|---|---|---|---|---|---|---|", *(f"| {' | '.join(cell(value) for value in (row.name, row.delivers, row.state, row.who, row.blocks, row.unblock, row.wrong))} |" for row in rows)] if rows else [NOTHING]


def duty_rows(sources: Sources) -> list[Row]:
    """One row per standing duty, at the newest lane of its successor chain."""
    rows = list(sources.by_lane.values())
    heads = {(head := head_of(row, sources.by_lane))["lane"]: (row["standing"], head) for row in rows if row.get("standing")}
    out = []
    for duty, head in sorted(heads.values(), key=lambda pair: pair[0]):
        who, blocks, unblock, wrong = sources.work([head["lane"]])
        if head["state"] == "retired":
            blocks, unblock = f"{head['lane']} retired by {head.get('retired_by') or 'nobody'} with no successor", f"root launches its successor from {head.get('brief') or 'its brief'} this turn"
        out.append(Row(duty, duty, f"{head['lane']} {head['state']}, heard {owner_waits.pacific_text(head.get('heard') or '')}", who, blocks, unblock, wrong))
    return out


def step_row(name: str, delivers: str, tasks: list[plans.Task], lanes: list[dict], sources: Sources) -> Row:
    who, blocks, unblock, wrong = sources.work([lane["lane"] for lane in lanes])
    counts = {state: sum(1 for task in tasks if task.state == state) for state in plans.STATES}
    state = f"{counts['done']} of {len(tasks)} tasks done, {counts['in_progress']} in progress, {counts['blocked']} blocked" if tasks else f"no tasks; {len(lanes)} lanes name it"
    waiting = "; ".join(f"task {owner_waits.clip(task.title)} waits on {task.waiting}" for task in tasks if task.state == "blocked" and task.waiting)
    return Row(name, delivers, state, who, "; ".join(filter(None, [waiting, blocks if blocks != "nothing recorded" else ""])) or blocks, unblock, wrong, done=bool(tasks) and counts["done"] == len(tasks))


def pieces(plan: plans.Plan) -> list[str]:
    """The named pieces of a plan with no numbered steps, listed in its title after the first colon, up to a semicolon: `SandSQL five pieces: ssql-server (two modes), ssql-replicator, ...`."""
    listed = plan.title.partition(": ")[2].split(";", 1)[0]
    return [PARENTHETICAL.sub("", piece).strip() for piece in listed.split(",") if PARENTHETICAL.sub("", piece).strip()] if not any(step.number != plans.UNMAPPED for step in plan.steps) else []


def on_piece(lane: dict, piece: str) -> bool:
    word = piece.rsplit("-", 1)[-1].casefold()
    return any(re.search(rf"(?<![a-z]){re.escape(word)}", (lane.get(key) or "").casefold()) for key in ("lane", "step", "deliverable"))


def lane_row(lane: dict, sources: Sources) -> Row:
    who, blocks, unblock, wrong = sources.work([lane["lane"]])
    last = sources.latest.get(lane["lane"])
    state = f"{lane['state']}, last {last['kind']} {when(last)}" if last else f"{lane['state']}, heard {owner_waits.pacific_text(lane.get('heard') or '')}"
    prs = " ".join(f"#{pr}" for pr in lane.get("prs") or [])
    return Row(lane["lane"], (lane.get("deliverable") or "no Deliverable line in its brief") + (f" ({prs})" if prs else ""), state, who, blocks, unblock, wrong)


def incident_rows(runner: Runner, drive_name: str, midnight: datetime, sources: Sources) -> list[Row]:
    """One row per incident since midnight, its fix, evidence and ship lanes folded into one."""
    read, since = owner_waits.tail(runner), midnight.isoformat()
    found = cci.current_records(read, drive_name, f"--kind={incident_log.INCIDENT}", since=since)
    found += [record for record in cci.current_records(read, drive_name, "--kind=decide", "--lane", ROOT, since=since) if incident_log.closes(record)]
    still = {record["seq"] for record in incident_log.open_incidents(found)}
    groups: dict[str, list[dict]] = {}
    for record in sorted(found, key=lambda record: record["seq"]):
        if record["kind"] == incident_log.INCIDENT and record["drive"] == drive_name:
            groups.setdefault(INCIDENT_ROLE.sub("", incident_name(record)), []).append(record)
    out = []
    for name, records in groups.items():
        open_now = any(record["seq"] in still for record in records)
        lanes = [lane for lane in sources.by_lane if lane == name or lane.startswith(f"{name}-")]
        who, blocks, unblock, wrong = sources.work(lanes)
        out.append(Row(name, owner_waits.clip(records[0].get("text") or ""), f"{'open' if open_now else 'closed'} since {when(records[0])}; newest {when(records[-1])}: {owner_waits.clip(records[-1].get('text') or '')}", who, blocks, unblock, wrong, done=not open_now))
    return out


def release_rows(runner: Runner, drive_name: str, midnight: datetime, sources: Sources) -> list[Row]:
    """One row per lane that posted a release record since midnight."""
    found = [record for record in cci.records(owner_waits.tail(runner), drive_name, "--kind=release", since=midnight.isoformat()) if record["kind"] == "release" and record["drive"] == drive_name]
    by_lane: dict[str, list[dict]] = {}
    for record in found:
        by_lane.setdefault(record["lane"], []).append(record)
    out = []
    for lane, records in by_lane.items():
        who, blocks, unblock, wrong = sources.work([lane])
        out.append(Row(lane, owner_waits.clip(records[0].get("text") or ""), f"{len(records)} release records since midnight; newest {when(records[-1])}: {owner_waits.clip(records[-1].get('text') or '')}", who, blocks, unblock, wrong, done=not sources.live(lane)))
    return out


def ask_row(ask: dict, sources: Sources) -> Row:
    who, blocks, unblock, wrong = sources.work([ask["lane"]])
    return Row(ask["key"], owner_waits.clip(ask["text"]), ask["state"], who, blocks, unblock, f"{ask['state']}: {ASK_WRONG[ask['state']]}" if ask["state"] in ASK_WRONG else wrong, done=ask["state"] == ledger.ASK_LIVE)


def pr_rows(runner: Runner, entry: dict, drive_name: str, sources: Sources) -> list[Row]:
    """Each open ledger PR: its owning lane, its title, whether it carries the queue label or why it waits, and the hold on it."""
    done = runner(["ccn", "-R", entry["checkout"], "ledger", "show", entry["ledger"], "--json"])
    if done.returncode:
        raise owner_waits.ReadFailed(f"ccn ledger show exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}")
    rows = ledger.pull_rows({row["key"]: row["fields"] for row in json.loads(done.stdout)["rows"] if not ledger.is_malformed(row["key"])})
    held = cci.held_prs(owner_waits.tail(runner), drive_name)
    out = []
    for key, fields in sorted(rows.items(), key=lambda item: ledger.pr_order(item[0])):
        if not ledger.is_open(fields):
            continue
        pr = ledger.PR_KEY.fullmatch(key)["pr"]
        state = "labelled for the queue" if ledger.carries_label(fields) else ledger.waiting_reason(fields) or "open"
        who, blocks, unblock, wrong = sources.work([fields["lane"]] if fields.get("lane") else [])
        blocks = f"held by cci #{held[pr]}" if pr in held else f"waits: {state}" if state in ledger.WAITING_REASONS else blocks
        out.append(Row(f"#{pr}", owner_waits.clip(fields.get("title") or ""), state, who, blocks, unblock, PR_WRONG.get(state, wrong), done=state == "labelled for the queue"))
    return out


def report(drive_name: str, runner: Runner, now: datetime | None = None) -> tuple[str, list[str]]:
    """The owner update as markdown from sources only, every row in the owner report table's columns and unowned rows first, and every name it must mention: each section, working lane, open ask key and open PR."""
    now = now or datetime.now(timezone.utc)
    midnight = now.astimezone(owner_waits.PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    entry = next(entry for entry in drive.drives() if drive.cci_drive(entry) == drive_name and entry.get("sessions"))
    rows = registry(runner, entry)
    lanes = working(rows, now)
    names = [lane["lane"] for lane in lanes]
    records = cci.records(owner_waits.tail(runner), drive_name, *(f"--lane={name}" for name in names), since=(now - QUIET_AFTER).isoformat()) if names else []
    latest = newest_by_lane(records)
    waits = {record["lane"]: record for record in waiting_on_root(runner, drive_name)}
    sources = Sources({row["lane"]: row for row in rows}, latest, waits, now)
    progress = plans.read(lambda *argv: runner(["ccn", "-R", entry["checkout"], *argv]).stdout, entry.get("program"))
    asks = ask_rows(runner, entry["checkout"], entry["ledger"], latest)
    board = board_rows(runner, drive_name, entry["sessions"][-1])
    tables: list[tuple[str, list[Row]]] = [("Standing duties", duty_rows(sources))]
    placed: set[str] = set()
    for plan, step in progress.steps():
        if plan is None:
            continue
        if step.number == plans.UNMAPPED:
            for piece in pieces(plan):
                mine = [lane for lane in lanes if on_piece(lane, piece)]
                placed |= {lane["lane"] for lane in mine}
                tables.append((f"{owner_waits.clip(plan.title)}, {piece}", [step_row(piece, f"the {piece} piece of {owner_waits.clip(plan.title)}", [], mine, sources), *(lane_row(lane, sources) for lane in mine)]))
            continue
        mine = [lane for lane in lanes if on_step(lane, step)]
        placed |= {lane["lane"] for lane in mine}
        tables.append((f"{owner_waits.clip(plan.title)}, step {owner_waits.clip(step.title)}", [step_row(f"Step {step.number}", step.title, step.tasks, mine, sources), *(lane_row(lane, sources) for lane in mine)]))
    rest = [lane for lane in lanes if lane["lane"] not in placed]
    tables += [(f"Workstream {step}", [lane_row(lane, sources) for lane in rest if step_of(lane) == step]) for step in sorted({step_of(lane) for lane in rest} - {""})]
    tables += [
        ("Lanes on no plan step", [lane_row(lane, sources) for lane in rest if not step_of(lane)]),
        ("Owner asks", [ask_row(item, sources) for item in sorted(asks, key=lambda item: item["state"] != ledger.ASK_LANDED_NOT_LIVE)]),
        ("Incidents today", incident_rows(runner, drive_name, midnight, sources)),
        ("Releases today", release_rows(runner, drive_name, midnight, sources)),
        (OPEN_PRS, pr_rows(runner, entry, drive_name, sources)),
    ]
    unowned = [row for _, found in tables for row in found if row.unowned]
    quiet = sorted(row["lane"] for row in rows if row["state"] in LIVE_LANES and row["lane"] not in set(names))
    lists = [
        (f"Registry lanes quiet over {QUIET_AFTER.days * 24} h", [f"- {', '.join(quiet)}"] if quiet else []),
        ("Waiting on root", [f"- {record['kind']} #{record['seq']} {record['lane']} {when(record)}: {owner_waits.clip(record.get('text') or '')}" for record in waits.values()]),
        ("Board picks", [f"- {item['line']}" for item in board]),
        (f"Delivered in the last {DEFAULT_SINCE.seconds // 3600} h", [f"- {record['lane']} {record['kind']} #{record['seq']} {when(record)}: {owner_waits.clip(record.get('text') or '')}" for record in newest_by_lane([record for record in records if record["kind"] in DELIVERED_KINDS and now - owner_waits.at(record) < DEFAULT_SINCE]).values()]),
    ]
    head = [f"{len(unowned)} rows have nobody on them; each also sits in its own section below.", "", *table(unowned)] if unowned else []
    out = [f"# Owner update: {drive_name}, {owner_waits.pacific(now)}", *section(UNOWNED, head), *(text for title, found in tables for text in section(title, table(found))), *(text for title, found in lists for text in section(title, found))]
    titles = [UNOWNED, *(title for title, _ in tables), *(title for title, _ in lists)]
    prs = next(found for title, found in tables if title == OPEN_PRS)
    return "\n".join(out) + "\n", [*titles, *names, *(item["key"] for item in asks), *(row.name for row in prs)]


def missing(text: str, names: list[str]) -> list[str]:
    return [name for name in dict.fromkeys(names) if not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text)]


def main(argv: list[str] | None = None, runner: Runner = owner_waits.run) -> int:
    parser = argparse.ArgumentParser(prog="owner_update.py", description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--drive", required=True)
    parser.add_argument("--since", help="an RFC 3339 time; delivered records after it count (default: 2 h ago)")
    parser.add_argument("--kind", action="append", help="a record kind that counts as a delivery, repeatable (default: report, done, fix-live, handoff)")
    parser.add_argument("--only", action="append", choices=SECTIONS, help="read only this section, repeatable (default: every section)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--render", action="store_true", help="print the whole owner update as markdown and exit 1 naming what it leaves out")
    parser.add_argument("--check", metavar="FILE", help="exit 1 naming each live lane, open ask and open PR this written update leaves out; - reads stdin")
    args = parser.parse_args(argv)
    if args.render or args.check:
        try:
            text, names = report(args.drive, runner)
        except owner_waits.ReadFailed as failed:
            print(f"owner-update: {failed}", file=sys.stderr)
            return 1
        if args.render:
            print(text, end="")
        written = text if args.render else sys.stdin.read() if args.check == "-" else Path(args.check).read_text()
        if left := missing(written, names):
            print(f"missing: {len(left)}", *left, sep="\n", file=sys.stderr)
            return 1
        return 0
    try:
        found = update(args.drive, since_of(args.since, datetime.now(timezone.utc)), runner, frozenset(args.kind or DELIVERED_KINDS), tuple(args.only or SECTIONS))
    except owner_waits.ReadFailed as failed:
        print(f"owner-update: {failed}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(found, default=str))
        return 0
    print("\n".join(item["line"] for section in SECTIONS for item in found[section]) or "nothing open")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
