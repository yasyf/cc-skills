#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Keep every sweep finding and every owner pick on a board card in one cc-notes ledger per drive, open until a HEAD or LANDED record names its fix, so none is lost (owner, 3:06 AM Oct 30; root #205185).

    findings.py post  [--drive ID] --sweep LANE --owner LANE --verdict WORD [--pr N] [--line PATH:LINE] TEXT
    findings.py sync  [--drive ID] [--since SEQ|DURATION]
    findings.py open  [--drive ID] [--sweep LANE] [--pr N] [--json]
    findings.py track [--drive ID] SEQ...
    findings.py close [--drive ID] --why TEXT SEQ...

`post` writes a finding as a cci `defect` that opens `[finding:<sweep>] #<pr> <path:line> <verdict> owner <lane>: <text>`, addressed to the owner lane. `sync` adds each new finding and each owner pick (`picked` or `wrote` on a board card) after its cursor as a ledger row, then moves each open or routed row to `fixed` when a later `head` or `landed` record names its PR or seq (or a pick's card), to `closed` when a record resolves it without one, and to `routed` when a record answers it. `open` syncs, then prints the open and routed rows by sweep. `track` adds the records it names, such as a pick from before the ledger began, and `close` closes rows no HEAD will ever name, such as a one-shot pick already carried out, with the reason. A lane that builds a pick names its seq or card in its HEAD record.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import cci
import drive
import ledger

FINDING = re.compile(r"\[finding:(?P<sweep>[\w.-]+)\]\s*(?P<rest>.*)", re.DOTALL)
CANONICAL = re.compile(r"^(?:#(?P<pr>\d+)|-)\s+(?P<line>\S+)\s+(?P<verdict>\S+)\s+owner\s+(?P<owner>[\w.-]+):\s*(?P<text>.*)$", re.DOTALL)
LEADING_PR = re.compile(r"^#(\d+)\b|\bpr#(\d+)\b")
OWNER = re.compile(r"\bowner\s+(?P<owner>[a-z][\w.-]*)")
PICK = re.compile(r"^Owner on board \w+, (?P<question>.*?) \((?P<card>[\w.-]+)\): (?:picked|wrote) “(?P<answer>[^”]*)”?", re.DOTALL)
NOT_OWNERS = frozenset({"root", "main", "all"})
FIX_KINDS = frozenset({"head", "landed"})
LIVE = ("open", "routed")
STATE_FILE = "findings.json"
FIRST_SINCE = "12h"
GREP_BUDGET = 4_000_000
PACIFIC = ZoneInfo("America/Los_Angeles")


def entry_of(name: str | None) -> dict:
    found = drive.find(name or drive.current_drive(), None)
    if not found:
        raise SystemExit(f"findings: no drive {name or 'bound to this session'}; pass --drive")
    return found


class Findings:
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
            made = json.loads(self.ccn("ledger", "add", f"findings: {self.cci}", "--json"))
            self.state["ledger"] = made["id"]
            self.save()
        return self.state["ledger"]

    def save(self) -> None:
        drive.write_atomic(self.path, self.state)

    def rows(self) -> dict[str, dict[str, str]]:
        return {row["key"]: row["fields"] for row in json.loads(self.ccn("ledger", "show", self.ledger(), "--json"))["rows"]}

    def write(self, rows: dict[str, dict[str, str]]) -> None:
        if rows:
            self.ccn("ledger", "sync", self.ledger(), "--file", "-", stdin=json.dumps([{"key": key, "fields": fields} for key, fields in rows.items()]))

    def later(self, row: dict[str, str]) -> list[dict]:
        names = [f"#{row['seq']}\\b", *([f"#{row['pr']}\\b"] if row.get("pr") else []), *([re.escape(row["card"])] if row.get("card") else [])]
        out = self.shell.run(["cci", "grep", "|".join(names), "--drive", self.cci, "--since-seq", str(int(row["seq"]) + 1), "--json", "--budget", str(GREP_BUDGET)])
        return sorted((json.loads(line) for line in out.splitlines() if line.strip()), key=lambda record: record["seq"])

    def settle(self, row: dict[str, str]) -> dict[str, str] | None:
        seq = int(row["seq"])
        for record in self.later(row):
            if record["kind"] in FIX_KINDS:
                return {**row, "state": "fixed", "by": str(record["seq"])}
            if seq in cci.resolved(record):
                return {**row, "state": "closed", "by": str(record["seq"])}
            if record.get("re") == seq and row["state"] == "open":
                row = {**row, "state": "routed", "by": str(record["seq"])}
        return row

    def sync(self, since: str | None = None) -> dict[str, dict[str, str]]:
        rows = self.rows()
        start = since or str(self.state.get("cursor") or FIRST_SINCE)
        added = {}
        newest = int(self.state.get("cursor") or 0)
        for record in cci.records(self.shell.run, self.cci, "--kind", "defect", "--kind", "owner", since=start):
            newest = max(newest, record["seq"])
            if (made := row_of(record)) and made[0] not in rows:
                added[made[0]] = made[1]
        changed = dict(added)
        for key, row in {**rows, **added}.items():
            if row.get("state") in LIVE and (settled := self.settle(row)) != row:
                changed[key] = settled
        self.write(changed)
        self.state["cursor"] = newest
        self.save()
        return {**rows, **changed}

    def track(self, seqs: list[int]) -> dict[str, dict[str, str]]:
        tracked = {}
        for seq in seqs:
            record = json.loads(self.shell.run(["cci", "show", str(seq), "--json"]))
            if not (made := row_of(record)):
                raise SystemExit(f"findings: #{seq} is neither a [finding:<lane>] defect nor an owner pick on a board card")
            tracked[made[0]] = self.settle(made[1])
        self.write(tracked)
        return tracked


def row_of(record: dict) -> tuple[str, dict[str, str]] | None:
    text = record.get("text") or ""
    base = {"seq": str(record["seq"]), "at": record["at"], "state": "open", "by": ""}
    if record["kind"] == "defect" and (found := FINDING.search(text)):
        rest = found["rest"].strip()
        if canonical := CANONICAL.match(rest):
            said = {"pr": canonical["pr"] or "", "line": "" if canonical["line"] == "-" else canonical["line"], "verdict": canonical["verdict"], "owner": canonical["owner"], "text": canonical["text"].strip()}
        else:
            prs = [str(ref).rsplit("#", 1)[-1] for ref in (record.get("refs") or {}).get("prs") or []]
            leading = LEADING_PR.search(rest)
            owner = OWNER.search(rest)
            said = {"pr": (prs or [next(filter(None, leading.groups())) if leading else ""])[0], "line": "", "verdict": "", "owner": owner["owner"] if owner else next((lane for lane in record.get("to") or [] if lane not in NOT_OWNERS), "root"), "text": rest}
        return f"finding/{record['seq']}", {**base, "kind": "finding", "sweep": found["sweep"], "card": "", **said}
    if record["kind"] == "owner" and (found := PICK.match(text)):
        return f"pick/{record['seq']}", {**base, "kind": "pick", "sweep": "owner", "pr": "", "line": "", "verdict": "pick", "owner": "root", "text": f"{found['question']}: {found['answer']}", "card": found["card"]}
    return None


def live(rows: dict[str, dict[str, str]], sweep: str | None, pr: str | None) -> list[dict[str, str]]:
    return sorted((row for row in rows.values() if row.get("state") in LIVE and (not sweep or row["sweep"] == sweep) and (not pr or row.get("pr") == pr)), key=lambda row: (row["sweep"], int(row["seq"])))


def printed(rows: list[dict[str, str]]) -> str:
    lines, sweep = [], None
    for row in rows:
        if row["sweep"] != sweep:
            sweep = row["sweep"]
            lines.append(f"{sweep}:")
        when = datetime.fromisoformat(row["at"].replace("Z", "+00:00")).astimezone(PACIFIC).strftime("%b %-d %-I:%M %p")
        lines.append(f"  #{row['seq']} {row['state']} {when} {'#' + row['pr'] + ' ' if row['pr'] else ''}{row['line'] + ' ' if row['line'] else ''}owner {row['owner']}: {row['text']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None, shell: ledger.Shell | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--drive")
    verbs = parser.add_subparsers(dest="verb", required=True)
    post = verbs.add_parser("post", help="post one finding as a [finding:<sweep>] defect to its owner lane")
    post.add_argument("--sweep", required=True)
    post.add_argument("--owner", required=True)
    post.add_argument("--verdict", required=True)
    post.add_argument("--pr", default="")
    post.add_argument("--line", default="")
    post.add_argument("text")
    sync = verbs.add_parser("sync", help="add new findings and picks to the ledger and settle the open ones")
    sync.add_argument("--since", help="read records from this seq or duration instead of the saved cursor")
    shown = verbs.add_parser("open", help="sync, then print the open and routed rows by sweep")
    shown.add_argument("--sweep")
    shown.add_argument("--pr")
    shown.add_argument("--json", action="store_true")
    track = verbs.add_parser("track", help="add the named records to the ledger")
    track.add_argument("seqs", nargs="+", type=int)
    close = verbs.add_parser("close", help="close rows no HEAD will name, with the reason")
    close.add_argument("--why", required=True)
    close.add_argument("seqs", nargs="+", type=int)
    args = parser.parse_args(argv)
    shell = shell or ledger.Shell()
    found = Findings(entry_of(args.drive), shell)
    if args.verb == "post":
        text = f"[finding:{args.sweep}] {'#' + args.pr.lstrip('#') if args.pr else '-'} {args.line or '-'} {args.verdict} owner {args.owner}: {args.text}"
        print(shell.run(["cci", "post", "--drive", found.cci, "--lane", args.sweep, "--kind", "defect", "--to", args.owner, "--force", *(["--pr", args.pr.lstrip("#")] if args.pr else []), "--text", text]).strip())
    elif args.verb == "sync":
        found.sync(args.since)
    elif args.verb == "open":
        rows = live(found.sync(), args.sweep, args.pr)
        print("\n".join(json.dumps(row) for row in rows) if args.json else printed(rows))
    elif args.verb == "track":
        found.track(args.seqs)
    else:
        found.write({key: {**row, "state": "closed", "by": args.why} for key, row in found.rows().items() if int(row["seq"]) in args.seqs})
    return 0


if __name__ == "__main__":
    drive.run_installed(__file__)
    sys.exit(main())
