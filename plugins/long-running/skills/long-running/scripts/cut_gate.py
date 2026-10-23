#!/usr/bin/env python3
"""Decide whether a release cut or a flag widening may run, for the go-and-cut hook.

`cut_gate.py check --verb cut|widen --target T ... --drive D --ledger L --repo OWNER/NAME --started ISO [--incident --repair N ...]`
reads the register's `rulings.py rows --json` payload on stdin and prints `{"refusals": [...], "blockers": [...]}`,
each entry `{"key", "text"}`. Outside an incident every finding is a refusal; during one only a cut that leaves out the
incident's declared fix is refused, and every other finding becomes a blocker.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cci
from ledger import LANDED, current_head, parse_iso

LEDGER_SCRIPT = Path(__file__).resolve().parent / "ledger.py"
UNGRADED = timedelta(minutes=30)
RELEASED = re.compile(r"^RELEASED\b")
RELEASE_KINDS = frozenset({"release", "fix-live"})
LAST_ONE_RELEASE_CUT = "R1b"
FIDELITY_OK = re.compile(r"^fidelity: ok (?P<head>[0-9a-f]{7,40})\b")
CHECKED = re.compile(r"\bchecked (?P<keys>\S+)")
SHORT = 7
GATE = "gate"
OWED = "owed"
BROKEN = "broken"
GLOBAL_KINDS = frozenset({"reserved", "scope"})
VERB_TOKENS = {"cut": "cut", "widen": "widen"}


@dataclass(frozen=True)
class Finding:
    key: str
    text: str


@dataclass(frozen=True)
class Riding:
    pr: str
    landed_at: datetime
    head: str
    step: str
    checked: frozenset[str]
    graded: bool


def tokens(value: str | None) -> list[str]:
    if not value:
        return []
    return [str(token) for token in json.loads(value)] if value.startswith("[") else [value]


def owed_gates(rows: dict[str, dict], verb: str, targets: list[str]) -> list[Finding]:
    wanted = {VERB_TOKENS[verb], *(targets if verb == "cut" else ())}
    return [
        Finding(f"gate:{key}", f"gate row {key} is owed and blocks {', '.join(sorted(blocked & wanted))}")
        for key, row in rows.items()
        if row.get("kind") == GATE and row.get("status") == OWED and (blocked := set(tokens(row.get("blocks")))) & wanted
    ]


def last_release(records: list[dict], target: str) -> dict | None:
    released = [
        record
        for record in records
        if record["kind"] in RELEASE_KINDS and RELEASED.match(record["text"]) and target in ((record.get("refs") or {}).get("targets") or [])
    ]
    return max(released, key=lambda record: record["seq"], default=None)


def after_last_one_release_cut(records: list[dict]) -> bool:
    return any(record["kind"] in RELEASE_KINDS and RELEASED.match(record["text"]) and record.get("topic") == LAST_ONE_RELEASE_CUT for record in records)


def step_span(riding: list[Riding]) -> list[Finding]:
    steps = sorted({ride.step for ride in riding if ride.step})
    if len(steps) <= 1:
        return []
    return [Finding("steps", f"the PRs riding this cut span plan steps {', '.join(steps)}; after {LAST_ONE_RELEASE_CUT} a cut carries one")]


def ungraded(riding: list[Riding], now: datetime) -> list[Finding]:
    return [
        Finding(f"ungraded:{ride.pr}", f"#{ride.pr} merged at {ride.landed_at.isoformat(timespec='minutes')} and its head has no fidelity ok")
        for ride in riding
        if not ride.graded and now - ride.landed_at > UNGRADED
    ]


def broken_rows(rows: dict[str, dict], riding: list[Riding]) -> list[Finding]:
    checked = frozenset().union(*(ride.checked for ride in riding if ride.graded))
    return [
        Finding(f"broken:{key}", f"{row['kind']} row {key} is broken and no head graded ok against it rides this cut")
        for key, row in rows.items()
        if row.get("kind") in GLOBAL_KINDS and row.get("status") == BROKEN and key not in checked
    ]


def missing_fixes(repairs: list[str], riding: list[Riding]) -> list[Finding]:
    carried = {ride.pr for ride in riding}
    return [Finding(f"fix:{pr}", f"the open incident's declared fix #{pr} has not landed, so this cut does not carry it") for pr in repairs if pr not in carried]


def verdict(
    rows: dict[str, dict], verb: str, targets: list[str], riding: list[Riding], releases: list[dict], repairs: list[str], incident: bool, now: datetime
) -> dict[str, list[dict]]:
    findings = owed_gates(rows, verb, targets)
    if verb == "cut":
        findings += [*(step_span(riding) if after_last_one_release_cut(releases) else []), *ungraded(riding, now), *broken_rows(rows, riding)]
    fixes = missing_fixes(repairs, riding) if incident and verb == "cut" else []
    refusals, blockers = (fixes, findings) if incident else (findings, [])
    return {"refusals": [vars(finding) for finding in refusals], "blockers": [vars(finding) for finding in blockers]}


def grades(records: list[dict]) -> list[tuple[str, frozenset[str]]]:
    found = []
    for record in records:
        if record["kind"] == "review" and (ok := FIDELITY_OK.match(record["text"])):
            path = (record.get("refs") or {}).get("path")
            text = Path(path).read_text() if path and Path(path).is_file() else record["text"]
            keys = frozenset(match["keys"].split(",")) if (match := CHECKED.search(text)) else frozenset()
            found.append((ok["head"], keys))
    return found


def same_head(graded: str, head: str) -> bool:
    shorter = min(len(graded), len(head))
    return shorter >= SHORT and graded[:shorter] == head[:shorter]


def ride(row: dict, records: list[dict]) -> Riding:
    head = current_head(row)
    matching = [keys for graded, keys in grades(records) if same_head(graded, head)]
    return Riding(row["pr"], parse_iso(row["landed_at"]), head, row.get("step", ""), frozenset().union(*matching), bool(matching))


def landed_since(rows: list[dict], since: datetime) -> list[dict]:
    return [row for row in rows if row.get("state") == LANDED and row.get("landed_at") and parse_iso(row["landed_at"]) > since]


def shell(argv: list[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True, check=True).stdout


def check(args: argparse.Namespace, run: Callable[[list[str]], str], register: dict, now: datetime) -> dict[str, list[dict]]:
    releases = [record for target in args.target for record in cci.records(run, args.drive, "--target", target)]
    started = parse_iso(args.started)
    since = min((parse_iso(released["at"]) if (released := last_release(releases, target)) else started for target in args.target), default=started)
    ledger_rows = json.loads(run([sys.executable, str(LEDGER_SCRIPT), "list", "--ledger", args.ledger, "--repo", args.repo, "--json"]))
    riding = [ride(row, cci.records(run, args.drive, "--kind", "review", "--pr", f"{args.repo}#{row['pr']}")) for row in landed_since(ledger_rows, since)]
    releases += cci.records(run, args.drive, "--topic", LAST_ONE_RELEASE_CUT)
    return verdict(register["rows"], args.verb, args.target, riding, releases, args.repair, args.incident, now)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    gate = sub.add_parser("check")
    gate.add_argument("--verb", choices=sorted(VERB_TOKENS), required=True)
    gate.add_argument("--target", action="append", default=[])
    gate.add_argument("--drive", required=True)
    gate.add_argument("--ledger", required=True)
    gate.add_argument("--repo", required=True)
    gate.add_argument("--started", required=True)
    gate.add_argument("--incident", action="store_true")
    gate.add_argument("--repair", action="append", default=[])
    args = parser.parse_args(argv)
    print(json.dumps(check(args, shell, json.load(sys.stdin), datetime.now(UTC))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
