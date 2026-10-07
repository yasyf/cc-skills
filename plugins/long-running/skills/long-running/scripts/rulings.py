#!/usr/bin/env python3
"""Read the owner-approved register and feed durable answers to the key-moment judge.

    rulings.py register (--program SLUG | --drive ID) [--repo PATH]
    rulings.py match    (--program SLUG | --drive ID) [-k N] [--budget BYTES] [--repo PATH] < ACTION

The register is the newest cc-notes doc labelled ``standing-rules:<program>``.
A consolidation lane proposes at most 30 rules for the owner to approve.
The full answers stay in cc-notes, linked by id. ``register`` reads the doc and
prints JSON with fields ``{id, body}``, or ``null`` when no register exists.

``match`` mirrors the drive's ``scope:durable`` answers into ``<state dir>/rulings/<id7>.md``:
answers labelled ``program:<program>`` plus answers anchored to the repo's current branch.
Another drive's answers never enter the corpus. Nothing injects it in bulk.
The command reads an action from stdin. Its first 2,000 characters become the query
for ``ccx code search --semantic``. Answers the register already cites are excluded.
Each match is printed as ``- <id7> <title>`` followed by its body quoted with ``  >``.
The CLI defaults to ``-k 8`` and an 8,000-byte ``--budget``.
Output stops before an answer would exceed that budget.

The key-moment judge calls ``match`` with ``-k 5`` and a 6,000-byte candidate budget.
A small model selects one answer the action clearly bears on or would violate,
or none. The hook injects only that answer, verbatim, once per lane per answer.
It is advisory, never blocks, and fails open.

``--program`` uses ``~/.claude/scratch/<program>`` as the state directory.
``--drive`` resolves the program and state directory from the drive registry for
Orca workers carrying ``CLAUDE_LONG_RUNNING_DRIVE``. ``--repo`` defaults to the
current directory. Both commands use only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import drive
import ledger

SHORT = 7
MATCHES = 8
BUDGET = 8000
QUERY_CHARS = 2000
QUOTE = "  >"
HIT = re.compile(r"^([0-9a-f]{7})\.md:", re.MULTILINE)
CITED = re.compile(r"\b[0-9a-f]{7,40}\b")


def ccn(shell: ledger.Shell, repo: str, *args: str) -> object:
    return json.loads(shell.run(["ccn", "-R", repo, *args, "--json"]) or "null")


def program_of(args: argparse.Namespace) -> str:
    return args.program or Path(drive.load(drive.drives_dir() / f"{args.drive}.json")["state_dir"]).name


def state_dir(args: argparse.Namespace) -> Path:
    if args.drive:
        return Path(drive.load(drive.drives_dir() / f"{args.drive}.json")["state_dir"])
    return Path.home() / ".claude" / "scratch" / args.program


def register(shell: ledger.Shell, repo: str, program: str) -> dict | None:
    docs = ccn(shell, repo, "doc", "list", "--label", f"standing-rules:{program}") or []
    if not docs:
        return None
    newest = max(docs, key=lambda doc: doc["updated_at"])["id"]
    return {"id": newest, "body": ccn(shell, repo, "doc", "show", newest)["body"]}


def branch_of(shell: ledger.Shell, repo: str) -> str:
    branch = shell.run(["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    return "" if branch == "HEAD" else branch


def drive_answers(shell: ledger.Shell, repo: str, program: str) -> list[dict]:
    durable = ("answer", "list", "--label", "scope:durable", "--limit", "0")
    scoped = ccn(shell, repo, *durable, "--label", f"program:{program}") or []
    if branch := branch_of(shell, repo):
        scoped += ccn(shell, repo, *durable, "--branch", branch) or []
    return list({answer["id"]: answer for answer in scoped}.values())


def mirror(answers: list[dict], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    wanted = {f"{answer['id'][:SHORT]}.md": f"# {answer['title']}\n\n{answer.get('body') or ''}\n" for answer in answers}
    for path in directory.glob("*.md"):
        if path.name not in wanted:
            path.unlink(missing_ok=True)
    for name, text in wanted.items():
        path = directory / name
        if not path.is_file() or path.read_text() != text:
            staged = path.with_name(f".{name}.{os.getpid()}")
            staged.write_text(text)
            staged.replace(path)


def search(shell: ledger.Shell, query: str, directory: Path, k: int) -> list[str]:
    argv = ["ccx", "code", "search", "--semantic", "--content", "docs", "-k", str(k), "--max-snippet-lines", "1", query, str(directory)]
    hits = HIT.findall(shell.run(argv))
    return list(dict.fromkeys(hits))


def rendered(answer: dict) -> str:
    quoted = [f"{QUOTE} {line}".rstrip() for line in (answer.get("body") or "").strip("\n").splitlines()]
    return "\n".join([f"- {answer['id'][:SHORT]} {answer['title']}", *quoted]) + "\n"


def within(blocks: list[str], budget: int) -> str:
    kept, size = [], 0
    for block in blocks:
        if size + len(block.encode()) > budget:
            break
        kept.append(block)
        size += len(block.encode())
    return "".join(kept)


def cmd_register(args: argparse.Namespace, shell: ledger.Shell) -> int:
    print(json.dumps(register(shell, args.repo, program_of(args))))
    return 0


def cmd_match(args: argparse.Namespace, shell: ledger.Shell) -> int:
    program = program_of(args)
    durable = drive_answers(shell, args.repo, program)
    answers = {answer["id"][:SHORT]: answer for answer in durable}
    directory = state_dir(args) / "rulings"
    mirror(durable, directory)
    cited = {token[:SHORT] for token in CITED.findall((register(shell, args.repo, program) or {}).get("body", ""))}
    query = sys.stdin.read()[:QUERY_CHARS]
    hits = [hit for hit in search(shell, query, directory, args.k) if hit in answers and hit not in cited]
    sys.stdout.write(within([rendered(answers[hit]) for hit in hits], args.budget))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rulings.py", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    show = subparsers.add_parser("register", help="print the newest standing-rules:<program> doc as {id, body}, or null")
    show.set_defaults(handler=cmd_register)
    match = subparsers.add_parser("match", help="print the durable rulings the brief on stdin is about")
    match.add_argument("-k", type=int, default=MATCHES)
    match.add_argument("--budget", type=int, default=BUDGET, metavar="BYTES")
    match.set_defaults(handler=cmd_match)
    for sub in (show, match):
        sub.add_argument("--repo", default=".", metavar="PATH")
        which = sub.add_mutually_exclusive_group(required=True)
        which.add_argument("--program", metavar="SLUG")
        which.add_argument("--drive", metavar="ID")
    return parser


def main(argv: list[str] | None = None, shell: ledger.Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, shell or ledger.Shell())


if __name__ == "__main__":
    raise SystemExit(main())
