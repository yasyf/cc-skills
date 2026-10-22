#!/usr/bin/env python3
"""Read the owner-approved register and feed durable answers to the key-moment judge.

    rulings.py register (--program SLUG | --drive ID) [--repo PATH]
    rulings.py match    (--program SLUG | --drive ID) [-k N] [--budget BYTES] [--exclude ID]... [--repo PATH] < ACTION
    rulings.py slice    (--program SLUG | --drive ID | --ledger FILE) --lane NAME --step STEP [--refresh BRIEF]
    rulings.py classify (--program SLUG | --drive ID | --ledger FILE) KEY --kind KIND [--words TEXT] [--step S]... [--path GLOB]... [--test ID]... [--op RE]...
    rulings.py check-pr (--program SLUG | --drive ID | --ledger FILE) (--pr N | --body-file F --diff-file F --head SHA) [--findings F] [--incident]
    rulings.py summary  (--program SLUG | --drive ID | --ledger FILE)

The register is the newest cc-notes doc labelled ``standing-rules:<program>``.
A consolidation lane proposes at most 30 rules for the owner to approve.
The full answers stay in cc-notes, linked by id. ``register`` reads the doc and
prints JSON with fields ``{id, body}``, or ``null`` when no register exists.

``match`` mirrors the drive's ``scope:durable`` answers into ``<state dir>/rulings/<id7>.md``:
answers labelled ``program:<program>`` plus answers anchored to the repo's current branch.
Another drive's answers never enter the corpus. Nothing injects it in bulk.
The command reads an action from stdin. Its first 2,000 characters become the query
for ``ccx code search --semantic``. Answers the register already cites are excluded,
and so is each ``--exclude`` id. With no answer left, it prints nothing and runs no search.
Each match is printed as ``- <id7> <title>`` followed by its body quoted with ``  >``.
The CLI defaults to ``-k 8`` and an 8,000-byte ``--budget``.
Output stops before an answer would exceed that budget.

The key-moment judge calls ``match`` with ``-k 5`` and a 6,000-byte candidate budget.
It excludes every answer it already injected into the lane, and makes no model call
when no candidate remains. Otherwise a small model selects one answer the action
clearly bears on or would violate, or none. The hook injects only that answer,
verbatim, once per lane per answer.
It is advisory, never blocks, and fails open: a match that outlasts 8 seconds injects nothing.

``slice``, ``classify``, ``check-pr`` and ``summary`` read the register ledger, the
cc-notes ledger labelled ``register:<program>``, or a JSON stub of its rows and answers.

``--program`` uses ``~/.claude/scratch/<program>`` as the state directory.
``--drive`` resolves the program and state directory from the drive registry for
Orca workers carrying ``CLAUDE_LONG_RUNNING_DRIVE``. ``--repo`` defaults to the
current directory. Both commands use only the Python standard library.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Iterator
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
KINDS = ("order", "pick", "design", "exception", "scope", "reserved", "gate")
GLOBAL = frozenset({"reserved", "scope", "design", "exception"})
ANCHORED = frozenset({"order", "pick", "exception"})
ANCHORS = ("steps", "tests", "paths", "symbols")
LISTS = (*ANCHORS, "ops", "lanes", "blocks")
INCIDENT = frozenset({"RULINGS-UNCITED", "RESERVED-NO-ANSWER", "TEST-REMOVED"})
SLICE_END = "End register slice."
SLICE_BLOCK = re.compile(r"^Register slice: .*?^End register slice\.\n?", re.MULTILINE | re.DOTALL)
SECTION = re.compile(r"^## Rulings[ \t]*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
CITE = re.compile(r"^[ \t]*[-*][ \t]+`?([\w./-]+)`?", re.MULTILINE)


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


class Stub:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = json.loads(path.read_text())

    def rows(self) -> list[dict]:
        return self.data["rows"]

    def answers(self, label: str) -> list[dict]:
        return [answer for answer in self.data["answers"] if label in answer["tags"]]

    def record(self, prefix: str) -> dict:
        return next(answer for answer in self.data["answers"] if answer["id"].startswith(prefix))

    def set_row(self, key: str, fields: dict[str, str]) -> None:
        next(row for row in self.data["rows"] if row["key"] == key)["fields"].update(fields)
        self.path.write_text(json.dumps(self.data, indent=2) + "\n")


class Notes:
    def __init__(self, shell: ledger.Shell, repo: str, program: str) -> None:
        self.shell, self.repo = shell, repo
        [found] = ccn(shell, repo, "ledger", "list", "--label", f"register:{program}")
        self.id = found["id"]

    def rows(self) -> list[dict]:
        return ccn(self.shell, self.repo, "ledger", "show", self.id)["rows"]

    def answers(self, label: str) -> list[dict]:
        return ccn(self.shell, self.repo, "answer", "list", "--label", label, "--limit", "0") or []

    def record(self, prefix: str) -> dict:
        return ccn(self.shell, self.repo, "show", prefix)

    def set_row(self, key: str, fields: dict[str, str]) -> None:
        ccn(self.shell, self.repo, "ledger", "row", "set", self.id, "--key", key, *(f"--field={name}={value}" for name, value in fields.items()))


def store_of(args: argparse.Namespace, shell: ledger.Shell) -> Stub | Notes:
    return Stub(args.ledger) if args.ledger else Notes(shell, args.repo, program_of(args))


def field(row: dict, name: str) -> str:
    return row["fields"].get(name, "")


def listed(row: dict, name: str) -> list[str]:
    return [item for item in field(row, name).splitlines() if item]


def squeeze(text: str) -> str:
    return " ".join(text.split())


def live(rows: list[dict]) -> list[dict]:
    return [row for row in rows if field(row, "status") != "superseded"]


def covers(pattern: str, path: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or path.startswith(pattern.rstrip("/") + "/")


def touches(row: dict, paths: dict[str, dict]) -> bool:
    return any(covers(pattern, path) for pattern in listed(row, "paths") for path in paths)


def in_step(step: str, steps: list[str]) -> bool:
    return any(step == anchor or step.startswith(anchor + ".") for anchor in steps)


def sliced(rows: list[dict], lane: str, step: str) -> list[dict]:
    return [
        row
        for row in live(rows)
        if field(row, "kind") in GLOBAL or not field(row, "kind") or in_step(step, listed(row, "steps")) or lane in listed(row, "lanes")
    ]


def render_slice(rows: list[dict], lane: str, step: str) -> str:
    chosen = sliced(rows, lane, step)
    rev = hashlib.sha256(json.dumps([[row["key"], row["fields"]] for row in chosen], sort_keys=True).encode()).hexdigest()[:12]
    lines = [f"Register slice: lane={lane} step={step} rev={rev}"]
    for row in chosen:
        label = "/".join(filter(None, [field(row, "kind") or "unclassified", field(row, "status")]))
        lines.append(f"- {row['key']} [{label}] {squeeze(field(row, 'words'))}")
        lines += [f"  {name}: {', '.join(listed(row, name))}" for name in LISTS if listed(row, name)]
    return "\n".join([*lines, SLICE_END]) + "\n"


def unclassifiable(fields: dict[str, str], store: Stub | Notes) -> str | None:
    capture = store.record(fields["capture"])
    if not fields.get("words") or squeeze(fields["words"]) not in squeeze(f"{capture['title']}\n{capture.get('body') or ''}"):
        return f"the words are not a substring of capture {fields['capture']}"
    if fields["kind"] in ANCHORED and not any(fields.get(name) for name in ANCHORS):
        return f"a {fields['kind']} row needs steps, tests, paths or symbols"
    if fields["kind"] == "reserved" and not fields.get("ops"):
        return "a reserved row needs ops patterns"
    if fields["kind"] == "gate" and not fields.get("blocks"):
        return "a gate row needs the GO or cut it blocks"
    return None


def changes(diff: str) -> dict[str, dict]:
    files: dict[str, dict] = {}
    source, change = "", None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            change = None
        elif change is None and line.startswith("--- "):
            source = line.removeprefix("--- a/")
        elif change is None and line.startswith("+++ "):
            deleted = line == "+++ /dev/null"
            change = files.setdefault(source if deleted else line.removeprefix("+++ b/"), {"added": [], "removed": [], "deleted": deleted})
        elif change is not None and line[:1] in ("+", "-"):
            change["added" if line[0] == "+" else "removed"].append(line[1:])
    return files


def removed_tests(row: dict, files: dict[str, dict]) -> Iterator[str]:
    for test in listed(row, "tests"):
        path, _, name = test.partition("::")
        change = files.get(path)
        if change is None:
            continue
        if change["deleted"] or (name and any(name in line for line in change["removed"]) and not any(name in line for line in change["added"])):
            yield test


def authorized(store: Stub | Notes, key: str, head: str) -> bool:
    for answer in store.answers(f"authorizes:{key}"):
        heads = [tag.removeprefix("head:") for tag in answer["tags"] if tag.startswith("head:")]
        if not heads or any(head.startswith(named) for named in heads):
            return True
    return False


def grade(store: Stub | Notes, title: str, body: str, diff: str, head: str, findings: list[dict]) -> list[tuple[str, str, str]]:
    files = changes(diff)
    section = SECTION.search(body)
    cited = set(CITE.findall(section.group(1))) if section else set()
    rows = store.rows()
    refusals = [] if section else [("RULINGS-UNCITED", "-", "the body has no ## Rulings section")]
    refusals += [("RULINGS-UNCITED", key, "cited but not a register row") for key in sorted(cited - {row["key"] for row in rows})]
    added = [title, *(line for change in files.values() for line in change["added"])]
    for row in live(rows):
        key, touched = row["key"], touches(row, files)
        if touched and key not in cited:
            refusals.append(("RULINGS-UNCITED", key, "the diff touches its paths"))
        hit = next((op for op in listed(row, "ops") if any(re.search(op, line) for line in added)), None)
        if field(row, "kind") == "reserved" and hit and not authorized(store, key, head):
            refusals.append(("RESERVED-NO-ANSWER", key, f"matches {hit!r} with no authorizes:{key} answer for {head[:SHORT]}"))
        refusals += [("TEST-REMOVED", key, test) for test in removed_tests(row, files)]
        if field(row, "status") == "broken" and touched:
            refusals.append(("HELD-BROKEN", key, "the diff touches a broken row's paths"))
        if field(row, "status") == "contested" and (touched or key in cited):
            refusals.append(("RULING-CONTESTED", key, "the row is contested"))
    refusals += [("FINDER-MAJOR", found["id"], found["title"]) for found in findings if found["severity"] == "major" and found["state"] == "open"]
    return refusals


def cmd_register(args: argparse.Namespace, shell: ledger.Shell) -> int:
    print(json.dumps(register(shell, args.repo, program_of(args))))
    return 0


def cmd_match(args: argparse.Namespace, shell: ledger.Shell) -> int:
    program = program_of(args)
    durable = drive_answers(shell, args.repo, program)
    directory = state_dir(args) / "rulings"
    mirror(durable, directory)
    cited = {token[:SHORT] for token in CITED.findall((register(shell, args.repo, program) or {}).get("body", ""))}
    answers = {key: answer for answer in durable if (key := answer["id"][:SHORT]) not in cited and key not in args.exclude}
    query = sys.stdin.read()[:QUERY_CHARS]
    hits = [hit for hit in search(shell, query, directory, args.k) if hit in answers] if answers else []
    sys.stdout.write(within([rendered(answers[hit]) for hit in hits], args.budget))
    return 0


def cmd_slice(args: argparse.Namespace, shell: ledger.Shell) -> int:
    text = render_slice(store_of(args, shell).rows(), args.lane, args.step)
    if not args.refresh:
        sys.stdout.write(text)
        return 0
    brief = args.refresh.read_text()
    args.refresh.write_text(SLICE_BLOCK.sub(lambda _: text, brief, count=1) if SLICE_BLOCK.search(brief) else f"{brief.rstrip()}\n\n{text}")
    return 0


def cmd_classify(args: argparse.Namespace, shell: ledger.Shell) -> int:
    store = store_of(args, shell)
    row = next(row for row in store.rows() if row["key"] == args.key)
    update = {"kind": args.kind, **({"words": args.words} if args.words else {}), **{name: "\n".join(values) for name in LISTS if (values := getattr(args, name))}}
    if problem := unclassifiable({**row["fields"], **update}, store):
        print(f"REFUSED {args.key}: {problem}", file=sys.stderr)
        return 1
    store.set_row(args.key, update)
    print(f"{args.key}\t{args.kind}")
    return 0


def cmd_check_pr(args: argparse.Namespace, shell: ledger.Shell) -> int:
    store = store_of(args, shell)
    if args.pr:
        pr = json.loads(shell.run(["gh", "pr", "view", args.pr, "--json", "title,body,headRefOid"]))
        title, body, head, diff = pr["title"], pr["body"], pr["headRefOid"], shell.run(["gh", "pr", "diff", args.pr])
    else:
        title, body, head, diff = args.title, args.body_file.read_text(), args.head, args.diff_file.read_text()
    findings = json.loads(args.findings.read_text()) if args.findings else []
    refusals = [refusal for refusal in grade(store, title, body, diff, head, findings) if not args.incident or refusal[0] in INCIDENT]
    for refusal in refusals:
        print("\t".join(refusal))
    return 1 if refusals else 0


def cmd_summary(args: argparse.Namespace, shell: ledger.Shell) -> int:
    store = store_of(args, shell)
    rows = store.rows()
    tally = Counter((field(row, "kind") or "unclassified", field(row, "status") or "standing") for row in rows)
    for (kind, status), count in sorted(tally.items()):
        print(f"{kind}\t{status}\t{count}")
    for row in rows:
        if field(row, "status") in ("broken", "contested") or not field(row, "kind"):
            print(f"attention\t{row['key']}\t{field(row, 'status') or 'unclassified'}")
        if field(row, "kind") == "reserved":
            print(f"authorizations\t{row['key']}\t{len(store.answers(f'authorizes:{row["key"]}'))}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rulings.py", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    show = subparsers.add_parser("register", help="print the newest standing-rules:<program> doc as {id, body}, or null")
    show.set_defaults(handler=cmd_register)
    match = subparsers.add_parser("match", help="print the durable rulings the brief on stdin is about")
    match.add_argument("-k", type=int, default=MATCHES)
    match.add_argument("--budget", type=int, default=BUDGET, metavar="BYTES")
    match.add_argument("--exclude", action="append", default=[], metavar="ID")
    match.set_defaults(handler=cmd_match)
    slicer = subparsers.add_parser("slice", help="print the register rows a lane's brief carries, or rewrite them in a brief")
    slicer.add_argument("--lane", required=True)
    slicer.add_argument("--step", required=True)
    slicer.add_argument("--refresh", type=Path, metavar="BRIEF")
    slicer.set_defaults(handler=cmd_slice)
    classify = subparsers.add_parser("classify", help="give a captured row its kind and anchors")
    classify.add_argument("key")
    classify.add_argument("--kind", required=True, choices=KINDS)
    classify.add_argument("--words")
    for name in LISTS:
        classify.add_argument(f"--{name[:-1]}", dest=name, action="append", default=[])
    classify.set_defaults(handler=cmd_classify)
    check = subparsers.add_parser("check-pr", help="print the refusals a PR head draws and exit 1 on any")
    check.add_argument("--pr")
    check.add_argument("--title", default="")
    check.add_argument("--body-file", type=Path)
    check.add_argument("--diff-file", type=Path)
    check.add_argument("--head", default="")
    check.add_argument("--findings", type=Path)
    check.add_argument("--incident", action="store_true")
    check.set_defaults(handler=cmd_check_pr)
    summary = subparsers.add_parser("summary", help="count rows by kind and status and name the rows a card must carry")
    summary.set_defaults(handler=cmd_summary)
    readers = (slicer, classify, check, summary)
    for sub in (show, match, *readers):
        sub.add_argument("--repo", default=".", metavar="PATH")
        which = sub.add_mutually_exclusive_group(required=True)
        which.add_argument("--program", metavar="SLUG")
        which.add_argument("--drive", metavar="ID")
        if sub in readers:
            which.add_argument("--ledger", type=Path, metavar="FILE")
    return parser


def main(argv: list[str] | None = None, shell: ledger.Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, shell or ledger.Shell())


if __name__ == "__main__":
    raise SystemExit(main())
