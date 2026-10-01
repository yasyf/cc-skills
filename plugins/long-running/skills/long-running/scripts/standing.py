#!/usr/bin/env python3
"""Standing owner rules: the live set in a desk inbox, and the handoff lint that carries them.

    standing.py inbox  FILE... [--repo PATH]
    standing.py titles --program SLUG [--repo PATH]
    standing.py lint   (--doc ID | --file PATH) --program SLUG [--previous-doc ID | --previous-file PATH] [--repo PATH]

STDLIB ONLY. A standing rule is one inbox line, ``R<n> (standing) <rule>`` or
``R<n> (<who, when>, standing) <rule>``, carrying no other ruling; it is never done, and
only a later line ``R<k> R<n> superseded by <id>``, or a later standing line saying
``supersedes R<n>``, ends it.
``inbox`` prints the live standing ids with their text and exits 3 on any line that breaks the
convention. ``titles`` prints the program's ``scope:durable`` answers as ``- <id> <title>``, the
verbatim body of a handoff's Standing owner rules section. ``lint`` exits 3 when a progress doc
or handoff file drops a durable title, drops a rule the previous handoff carried without a
``superseded by`` line, or has an owner-gate line that cites no live answer.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ID = r"[A-Z]{1,2}\d+(?:\.\d+)?"
STANDING_TAG = r"\((?:[^()]*,\s*)?standing\)"
STANDING_LINE = re.compile(rf"^\s*(?:[-*]\s+)?`?({ID})`?\s+{STANDING_TAG}:?\s+\S")
MISPLACED_TAG = re.compile(rf"\b{ID}`?[\s/]*{STANDING_TAG}")
SUPERSEDED = re.compile(rf"\b({ID}|[0-9a-f]{{7,40}})`?\s+(?:is\s+)?superseded by\s+`?({ID}|[0-9a-f]{{7,40}})\b")
SUPERSEDES = re.compile(rf"\bsupersedes\s+`?({ID})\b", re.IGNORECASE)
DONE = re.compile(rf"\b({ID})`?\s*(?:[:=—–-]\s*|is\s+)?(?:done|completed?|closed|finished|retired)\b", re.IGNORECASE)
SECTION = re.compile(r"^##\s+standing owner rules\b.*$", re.IGNORECASE | re.MULTILINE)
NEXT_SECTION = re.compile(r"^#{1,2}\s", re.MULTILINE)
BULLET_ID = re.compile(rf"^\s*[-*]\s+`?([0-9a-f]{{7,40}}|{ID})`?\b")
OWNER_GATE = re.compile(
    r"\bowner(?:'|’)?s (?:word|approval|sign-?off|go\b|click)|\bowner (?:approval|sign-?off|go\b|click)|reserved for the owner",
    re.IGNORECASE,
)
HEX = re.compile(r"\b[0-9a-f]{7,40}\b")
SHORT = 7
VIOLATIONS = 3


@dataclass
class Inbox:
    rules: dict[str, str] = field(default_factory=dict)
    at: dict[str, int] = field(default_factory=dict)
    superseded: dict[str, str] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)

    def live(self) -> dict[str, str]:
        return {rid: text for rid, text in self.rules.items() if rid not in self.superseded}


def read_inbox(lines: list[str], source: str = "") -> Inbox:
    inbox = Inbox()
    done: list[tuple[int, str, str]] = []
    superseded_at: dict[str, int] = {}
    for number, line in enumerate(lines, 1):
        where = f"{source}:{number}" if source else str(number)
        if match := STANDING_LINE.match(line):
            inbox.rules[match[1]] = line.strip()
            inbox.at[match[1]] = number
            for old in SUPERSEDES.findall(line):
                inbox.superseded[old] = match[1]
                superseded_at[old] = number
            continue
        if MISPLACED_TAG.search(line):
            inbox.violations.append(f"{where}: `(standing)` must follow the line's own single id: {line.strip()}")
        for old, new in SUPERSEDED.findall(line):
            inbox.superseded[old] = new
            superseded_at[old] = number
        done += [(number, where, rid) for rid in DONE.findall(line) if rid in inbox.rules]
    inbox.violations += [
        f"{where}: standing rule {rid} is marked done; it ends only with `{rid} superseded by <id>`"
        for number, where, rid in done
        if superseded_at.get(rid, 0) <= number
    ]
    return inbox


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


def rule_findings(body: str, previous: str | None, required: list[dict]) -> list[str]:
    problems = []
    if (lines := section(body)) is None:
        problems.append(
            "no `## Standing owner rules` section; paste `standing.py titles --program <slug>` there verbatim, "
            "plus every live `(standing)` inbox id"
        )
        lines = []
    text = "\n".join(lines)
    problems += [
        f"missing durable rule `- {answer['id'][:SHORT]} {answer['title']}`; copy the title verbatim, never re-summarized"
        for answer in required
        if answer["title"] not in text
    ]
    if previous is not None:
        now = carried(lines)
        problems += [
            f"dropped `{rid}` since the previous handoff ({line.strip()}); carry it, or write `- {rid} superseded by <id>`"
            for rid, line in carried(section(previous) or []).items()
            if rid not in now and "superseded by" not in line
        ]
    return problems


def lint(body: str, previous: str | None, required: list[dict], live: set[str]) -> list[str]:
    return rule_findings(body, previous, required) + [
        f"owner-gate line cites no live answer id: {line.strip()[:200]}" for line in body.splitlines() if gated(line, live)
    ]


def ccn(repo: str, *args: str) -> str:
    return subprocess.run(["ccn", "-R", repo, *args], capture_output=True, text=True, check=True).stdout


def answers(repo: str, *labels: str) -> list[dict]:
    flags = [arg for label in labels for arg in ("--label", label)]
    return json.loads(ccn(repo, "answer", "list", *flags, "--limit", "0", "--json") or "[]")


def doc_body(repo: str, doc_id: str) -> str:
    return json.loads(ccn(repo, "doc", "show", doc_id, "--json"))["body"]


def durable(repo: str, program: str) -> list[dict]:
    return answers(repo, "scope:durable", program)


def cmd_inbox(args: argparse.Namespace) -> int:
    violations = []
    live: dict[str, str] = {}
    for path in args.files:
        inbox = read_inbox(Path(path).read_text().splitlines(), path)
        live |= inbox.live()
        violations += inbox.violations
    print(f"live standing: {', '.join(live) or 'none'}")
    for text in live.values():
        print(text)
    for violation in violations:
        print(f"violation {violation}")
    return VIOLATIONS if violations else 0


def cmd_titles(args: argparse.Namespace) -> int:
    for answer in durable(args.repo, args.program):
        print(f"- {answer['id'][:SHORT]} {answer['title']}")
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    body = Path(args.file).read_text() if args.file else doc_body(args.repo, args.doc)
    previous = (
        Path(args.previous_file).read_text()
        if args.previous_file
        else doc_body(args.repo, args.previous_doc) if args.previous_doc else None
    )
    live = {answer["id"] for answer in answers(args.repo)}
    problems = lint(body, previous, durable(args.repo, args.program), live)
    for problem in problems:
        print(problem)
    return VIOLATIONS if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(required=True)

    inbox = subparsers.add_parser("inbox", help="print the live standing ids and every convention violation")
    inbox.add_argument("files", nargs="+", metavar="FILE")
    inbox.set_defaults(handler=cmd_inbox)

    titles = subparsers.add_parser("titles", help="print the program's scope:durable answer titles verbatim")
    titles.add_argument("--program", required=True, metavar="SLUG")
    titles.set_defaults(handler=cmd_titles)

    check = subparsers.add_parser("lint", help="lint a progress doc or handoff file against the durable answers")
    body = check.add_mutually_exclusive_group(required=True)
    body.add_argument("--doc", metavar="ID")
    body.add_argument("--file", metavar="PATH")
    before = check.add_mutually_exclusive_group()
    before.add_argument("--previous-doc", metavar="ID")
    before.add_argument("--previous-file", metavar="PATH")
    check.add_argument("--program", required=True, metavar="SLUG")
    check.set_defaults(handler=cmd_lint)

    for sub in (inbox, titles, check):
        sub.add_argument("--repo", default=".", metavar="PATH")
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
