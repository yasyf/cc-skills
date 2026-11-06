#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Keep the drive's retro table, what failed and what makes it impossible again, as one cc-notes ledger per drive whose every row has an owner, a state and a due time, and closes only when its fix is live (owner, 10:21 and 10:26 PM Nov 1).

    retro.py add  [--drive ID] --failure TEXT --mechanism TEXT --owner LANE --due TIME [--state STATE] [--pr URL] [--step N]
    retro.py set  [--drive ID] ROW [--mechanism TEXT] [--owner LANE] [--due TIME] [--state STATE] [--pr URL] [--note TEXT]
    retro.py live [--drive ID] ROW --record SEQ
    retro.py duplicate [--drive ID] ROW --of ROW
    retro.py rows  [--drive ID] [--json]
    retro.py check [--drive ID]

`add` refuses a row without a failure, a mechanism, an owner lane that posted on the drive in the last two hours, a due time, and a state from building, open or merged; `open` and `merged` need the fix's PR, and a failure already on the table is refused with the row that holds it. `set` changes a row under the same checks. A row closes only by `live`, naming the record that shows the fix live, or by `duplicate`, naming the row that holds it; `live` also gives its record to a row written live without one. TIME is an ISO 8601 time or a span from now such as `90m`, `4h` or `1d`. `rows` prints every row with its problems. `check` prints one PROBLEM line per row that is malformed, repeats another row's failure, or is past due and not live, and exits 1 when it printed any, so the coordinator's Stop hook and the stall watch read one rule.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from long_running import cci
from long_running import drive
from long_running import ledger

STATES = ("building", "open", "merged", "live")
NEEDS_PR = frozenset({"open", "merged"})
CLOSED = "live"
REQUIRED = ("failure", "mechanism", "owner", "due", "state")
PREFIX = "retro/"
STATE_FILE = "retro.json"
PACIFIC = ZoneInfo("America/Los_Angeles")


def entry_of(name: str | None) -> dict:
    found = drive.find(name or drive.current_drive(), None)
    if not found:
        raise SystemExit(f"retro: no drive {name or 'bound to this session'}; pass --drive")
    return found


def said(text: str) -> str:
    return " ".join(text.lower().split())


def problems(row: dict[str, str], moment: datetime) -> list[str]:
    found = [f"no {name}" for name in REQUIRED if not row.get(name)]
    if row.get("dup_of"):
        return found
    if (state := row.get("state")) and state not in STATES:
        found.append(f"state {state!r} is not one of {', '.join(STATES)}")
    if state in NEEDS_PR and not row.get("pr"):
        found.append(f"state {state} names no PR")
    if state == CLOSED and not row.get("record"):
        found.append("live with no FIX-LIVE record")
    if row.get("owner") and row["owner"] in cci.NOT_OWNERS:
        found.append(f"owner {row['owner']} is the coordinator, not a worker")
    if due := row.get("due"):
        try:
            if state != CLOSED and datetime.fromisoformat(due.replace("Z", "+00:00")) < moment:
                found.append(f"past due since {pacific(due)}")
        except ValueError:
            found.append(f"due {due!r} is not a time")
    return found


def pacific(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PACIFIC).strftime("%b %-d %-I:%M %p")


class Retro:
    def __init__(self, entry: dict, shell: ledger.Shell):
        self.shell = shell
        self.cci = drive.cci_drive(entry)
        self.checkout = entry["checkout"]
        self.path = Path(entry["state_dir"]) / STATE_FILE
        self.state = json.loads(self.path.read_text()) if self.path.is_file() else {}

    def ccn(self, *argv: str, stdin: str | None = None) -> str:
        return self.shell.run(["ccn", "-R", self.checkout, *argv], stdin=stdin)

    def ledger(self) -> str:
        if not self.state.get("ledger"):
            made = json.loads(self.ccn("ledger", "add", f"retro: {self.cci}", "--json"))
            self.state["ledger"] = made["id"]
            drive.write_atomic(self.path, self.state)
        return self.state["ledger"]

    def rows(self) -> dict[str, dict[str, str]]:
        return {row["key"]: row["fields"] for row in json.loads(self.ccn("ledger", "show", self.ledger(), "--json"))["rows"] if row["key"].startswith(PREFIX)}

    def write(self, key: str, fields: dict[str, str]) -> None:
        self.ccn("ledger", "sync", self.ledger(), "--file", "-", stdin=json.dumps([{"key": key, "fields": {**fields, "moved": ledger.utc_stamp()}}]))

    def live_owner(self, lane: str) -> None:
        if why := cci.dead_owner(self.shell.run, self.cci, lane):
            raise SystemExit(f"retro: owner refused: {why}")

    def checked(self, key: str, row: dict[str, str]) -> None:
        if found := problems(row, ledger.now()):
            raise SystemExit(f"retro: {key} refused: {'; '.join(found)}")


def table(rows: dict[str, dict[str, str]], moment: datetime) -> list[dict]:
    seen: dict[str, str] = {}
    out = []
    for key in sorted(rows):
        row = rows[key]
        found = problems(row, moment)
        if not row.get("dup_of") and (failure := said(row.get("failure", ""))):
            if failure in seen:
                found.append(f"repeats {seen[failure]}")
            seen.setdefault(failure, key)
        out.append({"key": key, **row, "problems": found})
    return out


def main(argv: list[str] | None = None, shell: ledger.Shell | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--drive")
    verbs = parser.add_subparsers(dest="verb", required=True)
    add = verbs.add_parser("add", help="add one row: what failed, what makes it impossible, its owner, due time and state")
    add.add_argument("--failure", required=True)
    add.add_argument("--mechanism", required=True)
    add.add_argument("--owner", required=True)
    add.add_argument("--due", required=True, type=ledger.due_time)
    add.add_argument("--state", default="building", choices=STATES[:-1])
    add.add_argument("--pr", default="")
    add.add_argument("--step", default="")
    edit = verbs.add_parser("set", help="change a row under the same checks")
    edit.add_argument("row")
    edit.add_argument("--mechanism")
    edit.add_argument("--owner")
    edit.add_argument("--due", type=ledger.due_time)
    edit.add_argument("--state", choices=STATES[:-1])
    edit.add_argument("--pr")
    edit.add_argument("--note")
    live = verbs.add_parser("live", help="close a row: its fix is live, shown by the named record")
    live.add_argument("row")
    live.add_argument("--record", required=True, type=int)
    dup = verbs.add_parser("duplicate", help="close a row as a repeat of another")
    dup.add_argument("row")
    dup.add_argument("--of", required=True)
    shown = verbs.add_parser("rows", help="print every row with its problems")
    shown.add_argument("--json", action="store_true")
    verbs.add_parser("check", help="print one PROBLEM line per malformed, repeated or past-due row; exit 1 when any")
    args = parser.parse_args(argv)
    retro = Retro(entry_of(args.drive), shell or ledger.Shell())
    if args.verb in ("rows", "check"):
        rows = table(retro.rows(), ledger.now())
        if args.verb == "rows":
            print("\n".join(json.dumps(row) for row in rows) if args.json else "\n".join(f"{row['key']} {row.get('state', '?')} owner {row.get('owner', '?')}: {row.get('failure', '')}" + (f" | {'; '.join(row['problems'])}" if row["problems"] else "") for row in rows))
            return 0
        bad = [row for row in rows if row["problems"]]
        for row in bad:
            print(f"PROBLEM {row['key']} owner {row.get('owner') or 'none'}: {'; '.join(row['problems'])} | {row.get('failure', '')}")
        return 1 if bad else 0
    rows = retro.rows()
    if args.verb == "add":
        if held := next((key for key, row in rows.items() if not row.get("dup_of") and said(row.get("failure", "")) == said(args.failure)), None):
            raise SystemExit(f"retro: {held} already holds this failure; change it with `retro.py set {held}`")
        key = f"{PREFIX}{max((int(key.removeprefix(PREFIX)) for key in rows), default=0) + 1:06d}"
        row = {"failure": args.failure, "mechanism": args.mechanism, "owner": args.owner, "due": args.due, "state": args.state, "pr": args.pr, "step": args.step, "at": ledger.utc_stamp()}
        retro.checked(key, row)
        retro.live_owner(args.owner)
        retro.write(key, row)
        print(f"{key} {args.state} owner {args.owner} due {pacific(args.due)}: {args.failure}")
        return 0
    if args.row not in rows:
        raise SystemExit(f"retro: no row {args.row}")
    row = rows[args.row]
    if row.get("state") == CLOSED and row.get("record") or row.get("dup_of"):
        raise SystemExit(f"retro: {args.row} is closed")
    if args.verb == "live":
        changed = {"state": CLOSED, "record": str(args.record)}
    elif args.verb == "duplicate":
        if args.of not in rows or args.of == args.row:
            raise SystemExit(f"retro: no other row {args.of}")
        changed = {"dup_of": args.of}
    else:
        changed = {name: value for name in ("mechanism", "owner", "due", "state", "pr", "note") if (value := getattr(args, name)) is not None}
    merged = {**row, **changed}
    retro.checked(args.row, merged)
    if changed.get("owner"):
        retro.live_owner(changed["owner"])
    retro.write(args.row, merged)
    print(f"{args.row} {merged.get('state')}{' duplicate of ' + merged['dup_of'] if merged.get('dup_of') else ''} owner {merged.get('owner')}: {merged.get('failure')}")
    return 0


if __name__ == "__main__":
    drive.run_installed(__file__)
    sys.exit(main())
