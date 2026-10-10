#!/usr/bin/env python3
"""Read live standing rules from cci and lint their handoff.

    standing.py live   --drive DRIVE
    standing.py lint   (--doc ID | --file PATH) --program SLUG [--previous-doc ID | --previous-file PATH] [--repo PATH]

A standing rule is a cci ``go`` record with topic ``standing``. The root addresses it
to a desk and cites its durable answer with ``--ccn <answer id>``. It is never done.
Only a later ``correction`` record with ``--topic standing --re <seq>`` and a later
answer replaces it. Rule ids are ``#<seq>``. ``live`` prints each live rule's id, text,
and source: ``ccn <answer id>`` when cited, otherwise ``cci #<seq>``.

``lint`` checks that ``## Standing owner rules`` exists and names the current
``standing-rules:<program>`` doc. It checks that rule ids from the previous handoff,
including ``#<seq>`` and old ``R<n>`` ids, are carried or superseded. Lines requiring
owner approval must cite a live answer id. The citation check skips quoted lines
prefixed with ``  >``.
Durable answer titles are not required in the handoff. ``lint`` exits 3 on a finding.
Both commands use only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cci
import ledger
import rulings

ID = r"(?:[A-Z]{1,2}\d+(?:\.\d+)?|#\d+)"
SECTION = re.compile(r"^##\s+standing owner rules\b.*$", re.IGNORECASE | re.MULTILINE)
OWN_RULES = re.compile(r"^#{2,3}\s+standing owner rules\b", re.IGNORECASE | re.MULTILINE)
NEXT_SECTION = re.compile(r"^#{1,2}\s", re.MULTILINE)
BULLET_ID = re.compile(rf"^\s*[-*]\s+`?({ID})`?\b")
OWNER_GATE = re.compile(
    r"\bowner(?:'|’)?s (?:word|approval|sign-?off|go\b|click)|\bowner (?:approval|sign-?off|go\b|click)|reserved for the owner",
    re.IGNORECASE,
)
HEX = re.compile(r"\b[0-9a-f]{7,40}\b")
SHORT = 7
VIOLATIONS = 3
REGISTER_HEADING = "## Standing owner rules"
RULE_LINE = re.compile(r"^\d+\.\s", re.MULTILINE)
QUOTE = "  >"


@dataclass
class Standing:
    rules: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    superseded: dict[str, str] = field(default_factory=dict)


def read(run: Callable[[list[str]], str], drive: str) -> Standing:
    records = cci.records(run, drive, "--topic=standing", "--kind=go", "--kind=correction")
    found = Standing(superseded={f"#{record['re']}": f"#{record['seq']}" for record in records if record["kind"] == "correction" and record.get("re")})
    for record in records:
        rid = f"#{record['seq']}"
        if rid not in found.superseded:
            found.rules[rid] = f"{rid} {record['text']}"
            found.sources[rid] = f"ccn {ccn_id}" if (ccn_id := record.get("refs", {}).get("ccn")) else f"cci {rid}"
    return found


def section(body: str) -> list[str] | None:
    if not (heading := SECTION.search(body)):
        return None
    following = NEXT_SECTION.search(body, heading.end())
    return body[heading.end() : following.start() if following else len(body)].strip("\n").splitlines()


def carried(lines: list[str]) -> dict[str, str]:
    return {match[1]: line for line in lines if (match := BULLET_ID.match(line))}


def cites(line: str, live: set[str]) -> bool:
    return any(any(answer.startswith(token) for answer in live) for token in HEX.findall(line))


def gated(line: str, live: set[str]) -> bool:
    return OWNER_GATE.search(line) is not None and not cites(line, live)


def rule_findings(body: str, previous: str | None, register: dict | None) -> list[str]:
    problems = []
    if (lines := section(body)) is None:
        problems.append("no `## Standing owner rules` section; regenerate the handoff with `handoff.py generate`")
        lines = []
    if register and pointer(register) not in lines:
        problems.append(
            f"the standing rules section does not name register `{register['id'][:SHORT]}`; regenerate the handoff with `handoff.py generate`"
        )
    if previous is not None:
        now = carried(lines)
        problems += [
            f"dropped `{rid}` since the previous handoff ({line.strip()}); carry it, or write `- {rid} superseded by <id>`"
            for rid, line in carried(section(previous) or []).items()
            if rid not in now and "superseded by" not in line
        ]
    return problems


def lint(body: str, previous: str | None, register: dict | None, live: set[str]) -> list[str]:
    return rule_findings(body, previous, register) + [
        f"owner-gate line cites no live answer id: {line.strip()[:200]}"
        for line in body.splitlines()
        if not line.startswith(QUOTE) and gated(line, live)
    ]


def ccn(repo: str, *args: str) -> str:
    return subprocess.run(["ccn", "-R", repo, *args], capture_output=True, text=True, check=True).stdout


def answers(repo: str, *labels: str) -> list[dict]:
    flags = [arg for label in labels for arg in ("--label", label)]
    return json.loads(ccn(repo, "answer", "list", *flags, "--limit", "0", "--json") or "[]")


def doc_body(repo: str, doc_id: str) -> str:
    return json.loads(ccn(repo, "doc", "show", doc_id, "--json"))["body"]


def pointer(register: dict) -> str:
    return f"Register `ccn doc show {register['id'][:SHORT]}`: {rule_count(register)} owner-approved rules, delivered verbatim after every compaction."


def rule_count(register: dict | None) -> int:
    return len(RULE_LINE.findall(register["body"])) if register else 0


def section_of(register: dict | None, lines: list[str], narrative: str = "") -> str:
    out = [REGISTER_HEADING, ""]
    if register:
        out += [pointer(register), ""]
    elif OWN_RULES.search(narrative):
        out += [f"- no `standing-rules` register doc; the root's own `{REGISTER_HEADING}` follows verbatim under `## Root narrative`", ""]
    else:
        out += ["- no `standing-rules` register doc", ""]
    return "\n".join(out + lines) + "\n"


def cmd_live(args: argparse.Namespace) -> int:
    found = read(ledger.Shell().run, args.drive)
    print(f"live standing: {', '.join(found.rules) or 'none'}")
    for rid, text in found.rules.items():
        print(f"{text} [{found.sources[rid]}]")
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    body = Path(args.file).read_text() if args.file else doc_body(args.repo, args.doc)
    previous = (
        Path(args.previous_file).read_text()
        if args.previous_file
        else doc_body(args.repo, args.previous_doc) if args.previous_doc else None
    )
    live = {answer["id"] for answer in answers(args.repo)}
    problems = lint(body, previous, rulings.register(ledger.Shell(), args.repo, args.program), live)
    for problem in problems:
        print(problem)
    return VIOLATIONS if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(required=True)

    live = subparsers.add_parser("live", help="print the live standing rules on a cci drive")
    live.add_argument("--drive", required=True)
    live.set_defaults(handler=cmd_live)

    check = subparsers.add_parser("lint", help="lint a progress doc or handoff file against the durable answers")
    body = check.add_mutually_exclusive_group(required=True)
    body.add_argument("--doc", metavar="ID")
    body.add_argument("--file", metavar="PATH")
    before = check.add_mutually_exclusive_group()
    before.add_argument("--previous-doc", metavar="ID")
    before.add_argument("--previous-file", metavar="PATH")
    check.add_argument("--program", required=True, metavar="SLUG")
    check.set_defaults(handler=cmd_lint)

    check.add_argument("--repo", default=".", metavar="PATH")
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
