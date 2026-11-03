#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""What the owner's next update must cover, read from the root's task list, the PR ledger, the board and cci, never from memory.

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

`--render` prints the whole owner update as markdown, its sections derived from sources,
never listed by hand: one per step of the program's plans, each holding the live lanes
whose brief's `Register slice: ... step=` names it, then one per other step a live lane's
brief names, then lanes on no step, one per
workstream heading of the `<program>: owner report table` doc, then open owner asks,
board picks, incidents and releases since midnight Pacific, and every open ledger PR with
its hold. A section with nothing says so in one line. It exits 1 naming each live
registry lane, open ask and open PR the render left out. `--check` reads an update the
root wrote and exits 1 naming each of those it leaves out, which the root's Stop hook
turns into a nudge, and the compaction hook adds `--render` to the progress record so
the root after compaction inherits every lane.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from long_running import cci
from long_running import drive
from long_running import ledger
import owner_waits
import plans

DELIVERED_KINDS = frozenset({"report", "done", "fix-live", "handoff"})
REPORTED_KINDS = DELIVERED_KINDS | {"head"}
CLOSED_ASKS = frozenset({ledger.ASK_DROPPED, ledger.ASK_ANSWERED})
OPEN_TASKS = frozenset({"in_progress", "pending"})
RECORD_WINDOW = "24h"
DEFAULT_SINCE = timedelta(hours=2)
TEXT_CHARS = 160
SCRIPTS = Path(__file__).resolve().parent
LIVE_LANES = frozenset({"launching", "desired", "held"})
SLICE_STEP = re.compile(r"^Register slice: .*\bstep=(\S+)", re.MULTILINE)
HEADING = re.compile(r"^#{2,3} +(.+?)\s*$", re.MULTILINE)
OWNER_TABLE = ": owner report table"
NOTHING = "- nothing new"

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


def live_lanes(runner: Runner, entry: dict) -> list[dict]:
    done = runner([sys.executable, str(SCRIPTS / "drive_supervisor.py"), "lanes", "--config", str(Path(entry["state_dir"]) / "lr.json"), "--json"])
    if done.returncode:
        raise owner_waits.ReadFailed(f"drive_supervisor.py lanes exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}")
    rows = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
    return [row | {"step": step_of(entry, row["lane"])} for row in rows if row["live"] and row["state"] in LIVE_LANES]


def step_of(entry: dict, lane: str) -> str:
    brief = Path(entry["state_dir"]) / lane / "brief.md"
    return found[1] if brief.is_file() and (found := SLICE_STEP.search(brief.read_text(errors="replace"))) else ""


def on_step(lane: dict, step: plans.Step) -> bool:
    return bool(lane["step"]) and (lane["step"] == step.number or lane["step"].casefold() in step.title.casefold())


def lane_line(lane: dict) -> str:
    prs = "".join(f" #{pr}" for pr in lane.get("prs") or [])
    return f"- {lane['lane']} ({lane['state']}, last heard {owner_waits.pacific_text(lane.get('heard') or '')}){prs}"


def workstreams(runner: Runner, entry: dict) -> list[str]:
    ccn = lambda *argv: runner(["ccn", "-R", entry["checkout"], *argv]).stdout
    table = next((doc for doc in json.loads(ccn("doc", "list", "--limit", "0", "--json") or "[]") if doc.get("title", "").endswith(f"{entry['program']}{OWNER_TABLE}")), None)
    return HEADING.findall(json.loads(ccn("doc", "show", table["id"], "--json"))["body"]) if table else []


def section(title: str, lines: list[str]) -> list[str]:
    return ["", f"## {title}", *(lines or [NOTHING])]


def today(runner: Runner, drive_name: str, kind: str) -> list[str]:
    midnight = datetime.now(owner_waits.PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    found = [record for record in cci.records(owner_waits.tail(runner), drive_name, f"--kind={kind}", since=midnight.isoformat()) if record["kind"] == kind and record["drive"] == drive_name]
    return [f"- {record['lane']} {when(record)}: {owner_waits.clip(record.get('text') or '')}" for record in found]


def open_prs(runner: Runner, entry: dict, drive_name: str) -> list[str]:
    done = runner(["ccn", "-R", entry["checkout"], "ledger", "show", entry["ledger"], "--json"])
    if done.returncode:
        raise owner_waits.ReadFailed(f"ccn ledger show exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}")
    rows = ledger.pull_rows({row["key"]: row["fields"] for row in json.loads(done.stdout)["rows"]})
    held = cci.held_prs(owner_waits.tail(runner), drive_name)
    return [
        f"- #{(pr := ledger.PR_KEY.fullmatch(key)['pr'])} {fields.get('lane') or 'no lane'}: {owner_waits.clip(fields.get('title') or '')}" + (f" | held by cci #{held[pr]}" if pr in held else "")
        for key, fields in sorted(rows.items(), key=lambda item: ledger.pr_order(item[0]))
        if fields.get("state", "open") == "open"
    ]


def report(drive_name: str, runner: Runner) -> tuple[str, list[str]]:
    """The owner update as markdown, with every name it must mention: each live lane, open ask key and open PR."""
    entry = next(entry for entry in drive.drives() if drive.cci_drive(entry) == drive_name and entry.get("sessions"))
    found = update(drive_name, datetime.now(timezone.utc) - DEFAULT_SINCE, runner)
    lanes = live_lanes(runner, entry)
    progress = plans.read(lambda *argv: runner(["ccn", "-R", entry["checkout"], *argv]).stdout, entry.get("program"))
    out = [f"# Owner update: {drive_name}"]
    placed: set[str] = set()
    for plan, step in progress.steps():
        mine = [lane for lane in lanes if on_step(lane, step)]
        placed |= {lane["lane"] for lane in mine}
        out += section(f"{plan.title if plan else 'Unplanned'}: {step.title}", [lane_line(lane) for lane in mine])
    rest = [lane for lane in lanes if lane["lane"] not in placed]
    for step in sorted({lane["step"] for lane in rest if lane["step"]}):
        out += section(f"Step {step}", [lane_line(lane) for lane in rest if lane["step"] == step])
    out += section("Lanes on no plan step", [lane_line(lane) for lane in rest if not lane["step"]])
    out += [line for heading in workstreams(runner, entry) for line in section(f"Workstream: {heading}", [])]
    out += section("Owner asks", [f"- {item['line']}" for item in found["asks"]])
    out += section("Board picks", [f"- {item['line']}" for item in found["board"]])
    out += section("Delivered", [f"- {item['line']}" for item in found["delivered"]])
    out += section("Incidents today", today(runner, drive_name, "incident"))
    out += section("Releases today", today(runner, drive_name, "release"))
    prs = open_prs(runner, entry, drive_name)
    out += section("Open PRs and holds", prs)
    names = [*(lane["lane"] for lane in lanes), *(item["key"] for item in found["asks"]), *(line.split()[1] for line in prs)]
    return "\n".join(out) + "\n", names


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
            print(f"missing: {', '.join(left)}", file=sys.stderr if args.render else sys.stdout)
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
