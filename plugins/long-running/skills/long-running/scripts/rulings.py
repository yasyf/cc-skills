#!/usr/bin/env python3
"""Read and write the owner's rulings: the standing-rules doc and the register ledger.

    rulings.py register     (--program SLUG | --drive ID) [--repo PATH]
    rulings.py match        (--program SLUG | --drive ID) [-k N] [--budget BYTES] [--exclude ID]... [--repo PATH] < ACTION
    rulings.py rows         (--program SLUG | --drive ID) [--json]
    rulings.py classify     (--program SLUG | --drive ID) [--answer ID] [CLASSIFICATION...]
    rulings.py implications (--program SLUG | --drive ID) KEY
    rulings.py confirm      (--program SLUG | --drive ID) KEY --capture DIGEST [--tick N,...|all] [--strike N,...]
    rulings.py slice        (--program SLUG | --drive ID) (--lane NAME --step N [--paths GLOB]... [--codex] | --root | --refresh BRIEF) [--plan PATH]
    rulings.py slice        (--program SLUG | --drive ID) --required --pr [OWNER/NAME#]N --head SHA [--base REF]
    rulings.py sweep        (--program SLUG | --drive ID) [KEY] [--artifact PATH]... [--paths GLOB]... [--root DIR] [--diff FILE] [--plan PATH] [--posted REF]
    rulings.py readback     (--program SLUG | --drive ID) --lane NAME --contract SEQ [--root DIR]
    rulings.py accepted     (--program SLUG | --drive ID) --lane NAME
    rulings.py pr-block     (--program SLUG | --drive ID) --pr [OWNER/NAME#]N --head SHA [--base REF] --lane NAME
    rulings.py table        (--program SLUG | --drive ID) [--since-last-update] [--full]
    rulings.py glossary     (--program SLUG | --drive ID)
    rulings.py authorize    (--program SLUG | --drive ID) --row KEY (--action TS | --prompt DIGEST)
    rulings.py status       (--program SLUG | --drive ID) KEY STATE --evidence REF
    rulings.py anchor       (--program SLUG | --drive ID) KEY [--paths P]... [--symbols S]... [--ops O]...
    rulings.py supersede    (--program SLUG | --drive ID) OLD --by NEW
    rulings.py check-pr     (--program SLUG | --drive ID) --pr [OWNER/NAME#]N --head SHA [--base REF] [--incident] [--plan PATH]

The standing-rules doc is the newest cc-notes doc labelled ``standing-rules:<program>``.
``register`` prints it as JSON ``{id, body}``, or ``null`` when none exists.

``match`` mirrors the drive's ``scope:durable`` answers into ``<state dir>/rulings/<id7>.md``:
answers labelled ``program:<program>`` plus answers anchored to the repo's current branch.
It reads an action from stdin, searches the mirror with ``ccx code search --semantic``,
drops answers the standing-rules doc cites and each ``--exclude`` id, and prints each
match as ``- <id7> <title>`` with its body quoted under ``  >``, within ``--budget`` bytes.

The register is the cc-notes ledger labelled ``register`` and ``program:<program>``: one row
per owner ruling, board answer, design rule and design exception, holding the owner's words
verbatim. This script is its only writer. Owner captures are read from
``<state dir>/owner-prompts.jsonl``; per-ruling cards are written to ``<state dir>/cards/``.

``--program`` uses ``~/.claude/scratch/<program>`` as the state directory. ``--drive``
resolves the program and the state directory from the drive registry. The register is read
from the checkout the drive's registry entry names; ``--repo``, which defaults to the current
directory, names it only when no drive runs the program. ``check-pr`` reads the register only
through the registry entry and uses ``--repo`` for the PR's own checkout. ``--paths`` globs
resolve under ``--root``, which defaults to ``--repo``; an absolute glob stands alone.
Standard library only.
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache, cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

import cci
import drive
import ledger

SHORT = 7
MATCHES = 8
BUDGET = 8000
QUERY_CHARS = 2000
QUOTE = "  >"
HIT = re.compile(r"^([0-9a-f]{7})\.md:", re.MULTILINE)
CITED = re.compile(r"\b[0-9a-f]{7,40}\b")

PACIFIC = ZoneInfo("America/Los_Angeles")
KINDS = ("order", "pick", "design", "exception", "scope", "reserved", "gate")
GLOBAL_KINDS = frozenset({"reserved", "scope", "design", "exception"})
ANCHORED_LATER = frozenset({"order", "pick", "gate"})
STATES = ("in-force", "pending-owner", "at-risk", "broken", "superseded", "owed", "met")
STANDING = {"in-force": "in force", "pending-owner": "waiting for you", "at-risk": "at risk", "broken": "broken", "owed": "owed", "met": "met"}
TROUBLE = frozenset({"at-risk", "broken"})
KIND_ORDER = {kind: rank for rank, kind in enumerate(("reserved", "scope", "exception", "design", "gate", "order", "pick"))}
NOT_RULINGS = ("corpus/", "no/")
CORPUS_KEY = "corpus/b-tests"
UNANCHORED = frozenset({"pending", "none", "all"})
IMPLICATION_COUNTS = range(3, 7)
DIRECTIONS = ("removes", "permits")
CONFIRM_SOURCES = frozenset({"slack-action", "slack-reply", "prompt", "board"})
RECOMMENDED = " (Recommended)"
OTHER = "Other"
CARD_OPTIONS = [f"Right{RECOMMENDED}", "Some wrong", "Open board", OTHER]
CONFIRMS_OPTIONS = ["Yes", "No"]
PRODUCT_NOUNS = ("chat", "Mac", "cloud", "share link", "pod", "API", "SandSQL", "journal", "attachment", "update", "team", "project")
UNCONFIRMED_AFTER = timedelta(hours=4)
SLICE_LINE = "Register slice:"
SLICE_HEADER = re.compile(r"^Register slice: lane=(?P<lane>\S+) step=(?P<step>\S+)(?: paths=(?P<paths>\S+))?(?P<codex> codex=yes)?[ \t]*$", re.MULTILINE)
SLICE_HASH = re.compile(r"^slice-hash: (?P<hash>[0-9a-f]{12})$", re.MULTILINE)
INCIDENT_STEP = "incident"
REPEAT_MARKERS = re.compile(
    r"\bi\b\W+(?:\w+\W+){0,2}?t[oi]ld you\b|\bi have said this\b|\bhow many times\b|\bwhy are you ignoring\b|\bwe already said\b",
    re.IGNORECASE,
)
QUOTED = re.compile(r"[\"“]([^\"”\n]+)[\"”]")
QUOTED_WORDS = 4
PLACEMENT = re.compile(
    r"\b(?:lives?|belongs?|goes|put|puts|placed?|home)\b(?:\W+\w+){0,6}?\W+(?:in|into|inside|outside|under)\b"
    r"|\b(?:not in|out of|in) scope\b|\bonly (?:in|on)\b|\bnever (?:in|on)\b"
    r"|\bjargon\b|\bterms?\b|\bcall(?:ed|ing)? (?:it|them)\b|\bplain (?:words|english)\b",
    re.IGNORECASE,
)
DIGEST_PROOF = re.compile(r"proof:[\w-]+:([0-9a-f]{16})\b")
DIGEST_CHARS = 16
OWNER_ANSWER = "from:owner"
ANSWER_SOURCE = "answer"
STRUCK_SPAN = re.compile(r"~~.+?~~")
STRUCK_MARK = re.compile(r"\[STRUCK\b[^\]]*\]?")
CLAUSE_ENDS = (";", ". ", "\n")
AFFIRMATIVE = re.compile(r"^(?:merge|yes|approve|authorize)\b", re.IGNORECASE)
RIGHT = "right"
NUMBER = re.compile(r"\b\d+\b")
ANCHOR_SUFFIX = re.compile(r"(?::\d+(?:-\d+)?)?(?:\s+@\s+\S+)?$")
DATE = re.compile(r"^([A-Z][a-z]{2} \d{1,2})")
PR_REF = re.compile(r"[\w.-]+/[\w.-]+#\d+")
DIFF_GIT = re.compile(r"^diff --git a/(?P<old>\S+) b/(?P<new>\S+)$")
HUNK = re.compile(r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")
WORD = re.compile(r"[a-z][a-z'-]+")
SLACK_ACTION = re.compile(r"(\{\"type\":\s*\"slack\.action\".*?\})\s*</channel>", re.DOTALL)
CARD_FILE = re.compile(r"@(\S+\.json)")
DIFF_MEDIA = "Accept: application/vnd.github.diff"
HIT_LINE = re.compile(r"^(?:open|deleted|authorized [0-9a-f]+) \S+ \S+:\d+ \S+")
VERDICT = re.compile(r"\b(?:delete|keep)\b", re.IGNORECASE)
CCI_TEXT = 400
READ_ERRORS = (OSError, ValueError, KeyError, subprocess.SubprocessError, SystemExit)
RULINGS_SECTION = re.compile(r"^## Rulings\b.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
RULINGS_LINES = re.compile(r"^## Rulings\b.*?(?=^##+ |\Z)", re.MULTILINE | re.DOTALL)
IN_YOUR_WORDS = re.compile(r"^### In your words\b.*?(?=^##+ |\Z)", re.MULTILINE | re.DOTALL)
APPENDIX = re.compile(r"^## Appendix\b", re.MULTILINE)
RUST_TEST = re.compile(r"^\s*#\[(?:\w+::)?test\b")
RUST_TEST_FILE = re.compile(r"(?:^|/)tests/[^/]+\.rs$|_test\.rs$")
TEST_DEFINITION = re.compile(
    r"^\s*(?:func\s+(?:\([^)]*\)\s*)?(?P<go>Test\w+)\s*\(|(?:async\s+)?def\s+(?P<py>test_\w+)\s*\("
    r"|(?:pub\s+)?(?:async\s+)?fn\s+(?P<rs>\w+)\s*\(|(?:it|test)\s*\(\s*(?P<quote>[\"'`])(?P<ts>.+?)(?P=quote))"
)
FINDER = re.compile(r"^finder: (?P<verdict>\S+) (?P<head>[0-9a-f]{7,40})\b")
TOUCHES = re.compile(r"\btouches ((?:rule|prompt|design|exception|reserved)/\S+)")
LANE_KINDS = ("head", "opened")
READBACK_GRADE = re.compile(r"^readback: (?P<verdict>ok|missing) (?P<lane>\S+) (?P<rest>.*)$")
CHECK_PR_ORDER = ("RULINGS-UNCITED", "RESERVED-NO-ANSWER", "TEST-REMOVED", "FINDER-MAJOR", "NAME-HIT", "RULINGS-PLAIN", "LANE-DECIDED", "CONTRACT-MISSING", "TESTS-CORPUS")
OWNER_QUOTED = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”")
PARENTHESIZED = re.compile(r"\([^()\n]*\)")
FENCED = re.compile(r"```.*?```", re.DOTALL)
DECISION = re.compile(r"\b(?:I|we)\s+(?:decided|chose|picked|went with|settled on|opted for)\b|\bdecided to\b|\b(?:my|our) (?:pick|choice|decision)\b", re.IGNORECASE)
UNPLAIN = (
    ("an internal id", re.compile(r"\b[GRL]\d{3,4}\b|\b(?:answer|grant|ruling|note|doc|log) [0-9a-f]{7}\b|\b(?:ctx|msg)_\w+")),
    ("a plan row number", re.compile(r"\b(?:C|SI|E|X)-?\d+(?:\.\d+)*\b")),
    ("an answer id", re.compile(r"\b(?=[a-f]*\d)[0-9a-f]{7,40}\b")),
    ("a file path", re.compile(r"(?<![\w/.:~-])(?:[\w.-]+(?:/[\w.-]+){2,}|[\w-]+(?:/[\w.-]+)*\.(?:py|go|rs|ts|tsx|js|json|md|toml|ya?ml|sh|sql)\b)")),
    ("a code symbol", re.compile(r"`[^`\n]+`|\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b|\b[a-z]{2,}(?:[A-Z][a-z0-9]+)+\b|\b\w+\(\)")),
    ("a drive word", re.compile(r"\b(?:lanes?|desks?|slices?|sidecars?|rosters?|inbox pulls?|mount leases?|adapter lines?|verbs?|primary keys?|orphan(?:s|ed)?)\b", re.IGNORECASE)),
)


def ccn(shell: ledger.Shell, repo: str, *args: str) -> object:
    return json.loads(shell.run(["ccn", "-R", repo, *args, "--json"]) or "null")


def entry_of(args: argparse.Namespace) -> dict | None:
    if args.drive:
        return drive.load(drive.drives_dir() / f"{args.drive}.json")
    return next((entry for entry in drive.drives() if entry.get("program") == args.program), None)


def register_repo(args: argparse.Namespace) -> str:
    return (entry_of(args) or {}).get("checkout") or args.repo


def program_of(args: argparse.Namespace) -> str:
    return args.program or drive.load(drive.drives_dir() / f"{args.drive}.json")["program"]


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
            write_atomic(path, text)


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}")
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


def listed(value: str | None) -> list:
    if not value:
        return []
    return json.loads(value) if value.startswith("[") else [value]


def stored(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def first_sentence(words: str) -> str:
    text = " ".join(words.split())
    return match[1] if (match := re.match(r"(.+?[.?!])(?:\s|$)", text)) else text


@cache
def name_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){re.escape(name)}(?:e?s)?(?![\w-])", re.IGNORECASE)


def named(text: str, name: str) -> bool:
    return bool(name_pattern(name).search(text))


def names_of(fields: dict[str, str]) -> list[str]:
    return [str(name) for name in listed(fields.get("names")) if str(name).strip()]


def is_global(fields: dict[str, str]) -> bool:
    return fields.get("kind") in GLOBAL_KINDS or fields.get("global") == "yes" or fields.get("paths") == "pending"


def anchors(fields: dict[str, str]) -> list[str]:
    return [ANCHOR_SUFFIX.sub("", str(value)) for value in listed(fields.get("paths")) if value not in UNANCHORED]


def symbols(fields: dict[str, str]) -> list[str]:
    return [str(value) for value in listed(fields.get("symbols")) if value not in UNANCHORED]


def card_id(key: str) -> str:
    return key.replace("/", ":")


def pacific(at: str) -> str:
    moment = datetime.fromisoformat(at).astimezone(PACIFIC)
    return f"{moment:%b %d} {moment.hour % 12 or 12}:{moment:%M %p} PT"


def utc_now() -> datetime:
    return datetime.now(UTC)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def binding(implication: dict) -> str:
    if implication.get("state") == "struck":
        return "struck"
    if implication.get("direction") == "removes" or implication.get("state") == "ticked":
        return "binds now"
    return "waits for the owner's tick"


def captures(state: Path) -> dict[str, dict]:
    path = state / "owner-prompts.jsonl"
    if not path.exists():
        return {}
    return {entry["digest"]: entry for line in path.read_text().splitlines() if line.strip() and (entry := json.loads(line))}


def ledger_of(shell: ledger.Shell, repo: str, program: str) -> str:
    found = ccn(shell, repo, "ledger", "list", "--label", "register", "--label", f"program:{program}") or []
    if not found:
        raise SystemExit(f"no register ledger labelled register and program:{program} in {repo}")
    return max(found, key=lambda entry: entry["updated_at"])["id"]


@contextmanager
def writing(args: argparse.Namespace, shell: ledger.Shell) -> Iterator[Register]:
    with ledger.locked(ledger.default_lock(ledger_of(shell, register_repo(args), program_of(args)))):
        yield Register.load(args, shell)


@dataclass
class Register:
    shell: ledger.Shell
    repo: str
    program: str
    state: Path
    ledger: str
    rows: dict[str, dict[str, str]]

    @classmethod
    def load(cls, args: argparse.Namespace, shell: ledger.Shell, repo: str | None = None) -> Register:
        program = program_of(args)
        repo = repo or register_repo(args)
        identity = ledger_of(shell, repo, program)
        shown = ccn(shell, repo, "ledger", "show", identity)
        rows = {row["key"]: row["fields"] | {"updated_at": row["updated_at"]} for row in shown["rows"]}
        return cls(shell, repo, program, state_dir(args), identity, rows)

    def write(self, key: str, **fields: object) -> None:
        values = {name: stored(value) for name, value in fields.items()}
        argv = ["ccn", "-R", self.repo, "ledger", "row", "set", self.ledger, "--key", key]
        for name, value in values.items():
            argv += ["--field", f"{name}={value}"]
        self.shell.run(argv)
        self.rows[key] = self.rows.get(key, {}) | values | {"updated_at": stamp()}

    def row(self, key: str) -> dict[str, str]:
        if key not in self.rows:
            raise SystemExit(f"no register row {key}")
        return self.rows[key]

    def rulings(self) -> dict[str, dict[str, str]]:
        return {key: fields for key, fields in sorted(self.rows.items()) if not key.startswith(NOT_RULINGS) and fields.get("status") != "superseded"}

    def scope(self) -> dict[str, dict[str, str]]:
        return {key: fields for key, fields in self.rulings().items() if fields.get("kind") == "scope"}

    def package(self) -> Path | None:
        corpus = self.rows.get(CORPUS_KEY) or {}
        return Path(corpus["path"]).parent.parent if corpus.get("path") else None

    def plan(self, given: str | None) -> Path | None:
        if given:
            return Path(given)
        return package / "plan" / "plan.md" if (package := self.package()) else None

    def classified(self) -> list[str]:
        found = {key.removeprefix("no/") for key in self.rows if key.startswith("no/")}
        for fields in self.rows.values():
            proofs = [fields.get("proof") or "", *(str(repeat.get("proof") or "") for repeat in listed(fields.get("repeats")))]
            found.update(match for proof in proofs for match in DIGEST_PROOF.findall(proof))
        return sorted(found)

    def answer_words(self) -> dict[str, list[tuple[str, frozenset[str]]]]:
        words: dict[str, list[tuple[str, frozenset[str]]]] = {}
        for fields in self.rulings().values():
            banned = frozenset(name.lower() for name in names_of(fields))
            for answer in listed(fields.get("answer")):
                words.setdefault(str(answer)[:SHORT], []).append((fields.get("words") or "", banned))
            for repeat in listed(fields.get("repeats")):
                for answer in repeat.get("answer") if isinstance(repeat.get("answer"), list) else [repeat.get("answer")]:
                    if answer:
                        words.setdefault(str(answer)[:SHORT], []).append((repeat.get("words") or "", banned))
        return words


def rendered_row(key: str, fields: dict[str, str]) -> list[str]:
    lines = [f"### {key} | {fields.get('kind', '')} | {fields.get('status', '')} | said {fields.get('said') or 1} | {fields.get('at', '')}"]
    lines += [f"> {line}" for line in (fields.get("words") or "").splitlines()]
    lines += [f"> also, {repeat.get('at', '')}: {repeat.get('words', '')}" for repeat in listed(fields.get("repeats"))]
    if implications := listed(fields.get("implications")):
        lines.append("This also means:")
        lines += [f"{item['n']}. {item['text']} ({item['direction']}, {item['state']}, {binding(item)})" for item in implications]
    for label, name in (("Names", "names"), ("Surfaces", "surfaces"), ("Tests", "tests")):
        if values := [str(value) for value in listed(fields.get(name)) if value not in UNANCHORED]:
            lines.append(f"{label}: {', '.join(values)}")
    return lines


@dataclass(frozen=True)
class Hit:
    status: str
    key: str
    file: str
    line: int
    name: str

    def __str__(self) -> str:
        return f"{self.status} {self.key} {self.file}:{self.line} {self.name}"


@dataclass
class Diff:
    files: list[str]
    added: list[tuple[str, int, str]]
    removed: list[tuple[str, int, str]]
    kept_before: list[tuple[str, int, str]]
    kept_after: list[tuple[str, int, str]]


def parse_diff(text: str) -> Diff:
    diff = Diff([], [], [], [], [])
    old_file = new_file = ""
    old_line = new_line = 0
    in_hunk = False
    for line in text.splitlines():
        if header := DIFF_GIT.match(line):
            old_file, new_file, in_hunk = header["old"], header["new"], False
            diff.files.append(new_file)
        elif hunk := HUNK.match(line):
            old_line, new_line, in_hunk = int(hunk["old"]), int(hunk["new"]), True
        elif not in_hunk:
            continue
        elif line.startswith("+"):
            diff.added.append((new_file, new_line, line[1:]))
            new_line += 1
        elif line.startswith("-"):
            diff.removed.append((old_file, old_line, line[1:]))
            old_line += 1
        elif line.startswith(" "):
            diff.kept_before.append((old_file, old_line, line[1:]))
            diff.kept_after.append((new_file, new_line, line[1:]))
            old_line += 1
            new_line += 1
    return diff


def struck(text: str, name: str) -> bool:
    spans = [(match.start(), match.end()) for match in STRUCK_SPAN.finditer(text)]
    spans += [(max(text.rfind(end, 0, mark.start()) for end in CLAUSE_ENDS) + 1, mark.end()) for mark in STRUCK_MARK.finditer(text)]
    found = list(name_pattern(name).finditer(text))
    return bool(found) and all(any(start <= match.start() and match.end() <= end for start, end in spans) for match in found)


def hit_status(text: str, name: str, authorizers: dict[str, list[tuple[str, frozenset[str]]]], removed: bool) -> str:
    if removed or struck(text, name):
        return "deleted"
    for cited in CITED.findall(text):
        if any(name.lower() not in banned and named(words, name) for words, banned in authorizers.get(cited[:SHORT], [])):
            return f"authorized {cited[:SHORT]}"
    return "open"


class Sweeper:
    def __init__(self, loaded: Register, rows: dict[str, dict[str, str]]) -> None:
        self.wanted = [(key, name) for key, fields in rows.items() for name in names_of(fields)]
        names = sorted({name for _, name in self.wanted}, key=len, reverse=True)
        self.anything = re.compile("|".join(name_pattern(name).pattern for name in names), re.IGNORECASE) if names else None
        self.authorizers = loaded.answer_words()

    def lines(self, source: str, numbered: Iterable[tuple[int, str]], removed: bool = False) -> list[Hit]:
        if not self.anything:
            return []
        return [
            Hit(hit_status(text, name, self.authorizers, removed), key, source, number, name)
            for number, text in numbered
            if self.anything.search(text)
            for key, name in self.wanted
            if named(text, name)
        ]

    def file(self, path: Path, shown: str) -> list[Hit]:
        if not path.is_file() or (text := readable(path)) is None:
            return []
        return self.lines(shown, enumerate(text.splitlines(), start=1))

    def files(self, paths: Iterable[Path], repo: str | None = None) -> list[Hit]:
        root = Path(repo).resolve() if repo else None
        hits = []
        for path in paths:
            resolved = path.resolve()
            hits += self.file(path, str(resolved.relative_to(root)) if root and resolved.is_relative_to(root) else str(path))
        return hits

    def diff(self, diff: Diff) -> list[Hit]:
        added = [hit for file, number, text in diff.added for hit in self.lines(file, [(number, text)])]
        return added + [hit for file, number, text in diff.removed for hit in self.lines(file, [(number, text)], removed=True)]


def globbed(root: str, globs: list[str]) -> list[Path]:
    found = (Path(path) for pattern in globs for path in (glob.glob(pattern, recursive=True) if os.path.isabs(pattern) else Path(root).glob(pattern)))
    return sorted(path for path in found if path.is_file())


def readable(path: Path) -> str | None:
    data = path.read_bytes()
    return None if b"\0" in data[:4096] else data.decode(errors="replace")


def default_files(loaded: Register, plan: Path | None) -> list[Path]:
    files = [plan] if plan else []
    if package := loaded.package():
        files += sorted(path for path in (package / "boards").rglob("*") if path.is_file())
        files += sorted((package / "designs").glob("*/design.md"))
    if corpus := (loaded.rows.get(CORPUS_KEY) or {}).get("path"):
        files.append(Path(corpus))
    if loaded.state.is_dir():
        files += sorted(path for path in loaded.state.rglob("*.md") if any("brief" in part for part in path.relative_to(loaded.state).parts))
    return list(dict.fromkeys(files))


def step_bounds(lines: list[str], step: str) -> tuple[int, int] | None:
    major, _, minor = step.partition(".")
    heading = re.compile(rf"^(#+) Step {re.escape(step)}\.(?:\s|$)")
    start = next((index for index, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        heading = re.compile(rf"^(#+) Step {re.escape(major)}\.(?:\s|$)")
        start = next((index for index, line in enumerate(lines) if heading.match(line)), None)
    else:
        minor = ""
    if start is None:
        return None
    level = len(heading.match(lines[start])[1])
    end = next((index for index in range(start + 1, len(lines)) if re.match(rf"^#{{1,{level}}} ", lines[index])), len(lines))
    if not minor:
        return start, end
    item = next((index for index in range(start, end) if lines[index].startswith(f"{minor}. ")), None)
    if item is None:
        return None
    stop = next((index for index in range(item + 1, end) if re.match(r"^\d+\. ", lines[index])), end)
    return item, stop


def steps_match(row_steps: list, step: str) -> bool:
    return any(value == "all" or value == step or str(value).startswith(f"{step}.") or step.startswith(f"{value}.") for value in map(str, row_steps))


@dataclass
class Want:
    lane: str
    step: str
    paths: list[str]
    codex: bool

    def header(self) -> str:
        line = f"{SLICE_LINE} lane={self.lane} step={self.step}"
        if self.paths:
            line += f" paths={','.join(self.paths)}"
        return line + (" codex=yes" if self.codex else "")

    def matches(self, fields: dict[str, str]) -> bool:
        if self.lane in listed(fields.get("lanes")) or steps_match(listed(fields.get("steps")), self.step):
            return True
        return any(fnmatch.fnmatch(anchor, pattern) or fnmatch.fnmatch(pattern, anchor) for anchor in anchors(fields) for pattern in self.paths)

    @classmethod
    def parse(cls, header: re.Match[str]) -> Want:
        return cls(header["lane"], header["step"], [pattern for pattern in (header["paths"] or "").split(",") if pattern], bool(header["codex"]))


def write_in_first(item: tuple[str, dict[str, str]]) -> tuple[bool, str]:
    key, fields = item
    return "write-in" not in " ".join((fields.get("proof") or "", fields.get("evidence") or "", fields.get("note") or "")).lower(), key


def hashed(lines: list[str]) -> str:
    body = "\n".join(lines).rstrip() + "\n"
    return body + f"slice-hash: {digest(body)}\n"


def section(title: str, rows: Iterable[tuple[str, dict[str, str]]]) -> list[str]:
    rows = list(rows)
    lines = ["", f"## {title} ({len(rows)})"]
    for key, fields in rows:
        lines += ["", *rendered_row(key, fields)]
    return lines


def lane_slice(loaded: Register, want: Want, plan: Path | None) -> str:
    rulings = loaded.rulings()
    lines = [want.header(), f"Program {loaded.program}, register ledger {loaded.ledger[:12]}. Every row is the owner's words, verbatim."]
    if want.codex and (doc := register(loaded.shell, loaded.repo, loaded.program)):
        lines += ["", "## Standing rules", doc["body"].rstrip()]
    global_rows = {key: fields for key, fields in rulings.items() if is_global(fields)}
    lines += section("Every global row", global_rows.items())
    if want.step == INCIDENT_STEP:
        done = set(loaded.classified())
        pending = [capture for item, capture in captures(loaded.state).items() if item not in done]
        lines += ["", f"## Owner captures not yet classified ({len(pending)})"]
        for capture in pending:
            lines += ["", f"### {capture['digest']} | {capture.get('source', '')} | {capture.get('at', '')}", *(f"> {line}" for line in capture["text"].splitlines())]
        return hashed(lines)
    matched = {key: fields for key, fields in rulings.items() if key not in global_rows and want.matches(fields)}
    lines += section(f"Rows matched for lane {want.lane} and step {want.step}", ((key, fields) for key, fields in matched.items() if fields.get("kind") != "pick"))
    lines += section(f"Board answers for step {want.step}, write-ins first", sorted(((key, fields) for key, fields in matched.items() if fields.get("kind") == "pick"), key=write_in_first))
    text = plan.read_text() if plan and plan.is_file() else ""
    plan_lines = text.splitlines()
    bounds = step_bounds(plan_lines, want.step)
    lines += ["", f"## Sweep hits in plan step {want.step}"]
    if bounds:
        start, end = bounds
        lines += [str(hit) for hit in Sweeper(loaded, loaded.scope()).lines(str(plan), ((number, plan_lines[number - 1]) for number in range(start + 1, end + 1)))]
        lines += ["", f"## Plan step {want.step}", *plan_lines[start:end]]
    return hashed(lines)


def root_slice(loaded: Register) -> str:
    rows = {key: fields for key, fields in loaded.rulings().items() if fields.get("kind") == "scope" or "root" in listed(fields.get("lanes"))}
    lines = [f"{SLICE_LINE} lane=root step=all", f"Program {loaded.program}, register ledger {loaded.ledger[:12]}. Every scope row and every row naming the root, verbatim."]
    return hashed(lines + section("Scope rows and rows naming the root", rows.items()))


def compare(shell: ledger.Shell, repo: str, base: str, head: str) -> Diff:
    return parse_diff(shell.run(["gh", "api", f"repos/{repo}/compare/{base}...{head}", "-H", DIFF_MEDIA]))


def pull(args: argparse.Namespace, shell: ledger.Shell) -> tuple[str, str]:
    if not (match := ledger.PR_KEY.fullmatch(args.pr)):
        raise SystemExit(f"--pr {args.pr!r} is neither N nor OWNER/NAME#N")
    return match["repo"] or ledger.origin_repo(shell, Path(args.repo)), match["pr"]


def symbol_lines(fields: dict[str, str], diff: Diff, at_home: bool) -> list[tuple[str, str, int]]:
    homes = anchors(fields) if at_home else []
    found = []
    for symbol in symbols(fields):
        pattern = name_pattern(symbol.rsplit(".", 1)[-1])
        found += [
            (symbol, file, number)
            for file, number, text in diff.added
            if pattern.search(text) and (not homes or any(fnmatch.fnmatch(file, home) for home in homes))
        ]
    return found


def touched(fields: dict[str, str], diff: Diff) -> bool:
    return any(fnmatch.fnmatch(file, anchor) for anchor in anchors(fields) for file in diff.files) or bool(symbol_lines(fields, diff, at_home=False))


def required_slice(loaded: Register, diff: Diff) -> str:
    lines = [f"{key}\t{'|'.join(names_of(fields))}" for key, fields in loaded.rulings().items() if is_global(fields) or touched(fields, diff)]
    body = "\n".join(lines) + "\n"
    return body + f"slice-hash: {digest(body)}\n"


def refresh(loaded: Register, brief: Path, plan: Path | None) -> str:
    text = brief.read_text()
    if not (header := SLICE_HEADER.search(text)):
        raise SystemExit(f"{brief} has no `{SLICE_LINE} lane=<name> step=<n>` line")
    fresh = lane_slice(loaded, Want.parse(header), plan)
    end = header.end()
    old = None
    if (stamp := SLICE_HASH.search(text, header.end())) and not SLICE_HEADER.search(text, header.end(), stamp.start()):
        old, end = stamp["hash"], stamp.end()
    new = SLICE_HASH.search(fresh)["hash"]
    if old == new and text[header.start() : end] == fresh.rstrip("\n"):
        return f"fresh {brief} slice-hash {new}"
    write_atomic(brief, text[: header.start()] + fresh.rstrip("\n") + text[end:])
    return f"refreshed {brief} slice-hash {old or 'none'} -> {new}"


def cmd_register(args: argparse.Namespace, shell: ledger.Shell) -> int:
    print(json.dumps(register(shell, register_repo(args), program_of(args))))
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


def cmd_rows(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    print(json.dumps({"ledger": loaded.ledger, "program": loaded.program, "state_dir": str(loaded.state), "rows": loaded.rows, "classified": loaded.classified()}, ensure_ascii=False))
    return 0


def classifications(path: Path) -> list[dict]:
    text = path.read_text()
    return json.loads(text) if text.lstrip().startswith("[") else [json.loads(line) for line in text.splitlines() if line.strip()]


def quoted(words: str) -> list[str]:
    return [span for span in QUOTED.findall(words) if len(span.split()) >= QUOTED_WORDS]


def repeat_markers(loaded: Register, words: str) -> list[str]:
    found = [match.group(0) for match in REPEAT_MARKERS.finditer(words)]
    return found + [f'a quoted sentence "{span}"' for span in quoted(words)]


def named_rows(loaded: Register, words: str) -> list[str]:
    return [key for key, fields in loaded.rulings().items() if any(named(words, name) for name in names_of(fields))]


def widened(loaded: Register, entry: dict) -> tuple[str, list[str]] | None:
    names = {str(name).lower() for name in entry.get("names") or []}
    for key, fields in loaded.scope().items():
        if names & {name.lower() for name in names_of(fields)}:
            have = {str(surface).lower() for surface in listed(fields.get("surfaces"))}
            if added := [str(surface) for surface in entry.get("surfaces") or [] if str(surface).lower() not in have]:
                return key, added
    return None


def new_key(entry: dict, capture: dict) -> str:
    if entry.get("key"):
        return entry["key"]
    if answers := entry.get("answer"):
        return f"rule/{answers[0][:SHORT]}"
    return f"prompt/{datetime.fromisoformat(capture['at']).astimezone(PACIFIC):%m%d-%H%M%S}"


@dataclass
class Batch:
    keys: set[str] = field(default_factory=set)
    digests: set[str] = field(default_factory=set)
    added: dict[str, int] = field(default_factory=dict)


def refusals(loaded: Register, captured: dict[str, dict], entry: dict, batch: Batch) -> list[str]:
    item = entry["digest"]
    if item not in captured:
        return [f"UNKNOWN-CAPTURE {item}: no capture with this digest in {loaded.state / 'owner-prompts.jsonl'}"]
    if item in loaded.classified() or (item in batch.digests and captured[item]["source"] != ANSWER_SOURCE):
        return [f"CLASSIFIED {item}: this capture is already in the register or earlier in this batch"]
    batch.digests.add(item)
    if "no" in entry:
        return []
    words, extends = entry.get("words") or "", entry.get("extends")
    extended = loaded.rulings().get(extends) or {} if extends else {}
    kind = entry.get("kind") or extended.get("kind")
    found = []
    if not words or words not in captured[item]["text"]:
        found.append(f"NOT-VERBATIM {item}: words must be a verbatim substring of the capture, never a reading")
    if kind not in KINDS:
        found.append(f"KIND {item}: {kind!r} is none of {', '.join(KINDS)}")
    implications = entry.get("implications") or []
    carried = len(listed(extended.get("implications"))) + batch.added.get(extends or "", 0) + len(implications)
    if extends:
        batch.added[extends] = batch.added.get(extends, 0) + len(implications)
    if kind == "scope" and carried not in IMPLICATION_COUNTS:
        found.append(f"SCOPE-IMPLICATIONS {item}: a scope row carries 3 to 6 implications, not {carried}")
    if kind == "scope" and not (entry.get("names") or names_of(extended)):
        found.append(f"SCOPE-NAMES {item}: a scope row lists every name the thing has in the plan, the boards and the code")
    if any(implication.get("direction") not in DIRECTIONS or not implication.get("text") for implication in implications):
        found.append(f"IMPLICATION {item}: each implication is one plain sentence marked removes or permits")
    markers = repeat_markers(loaded, words)
    supersedes = entry.get("supersedes")
    repeated = [key for key in named_rows(loaded, words) if kind != "pick" and key != supersedes]
    if kind == "order" and (markers or PLACEMENT.search(words) or named_rows(loaded, words)):
        because = ", ".join(markers) or (f"a placement rule ({match.group(0)})" if (match := PLACEMENT.search(words)) else f"names of {', '.join(named_rows(loaded, words))}")
        found.append(f"ORDER-IS-SCOPE {item}: the words carry {because}; a repeat extends its row and a placement is a scope row")
    if extends and extends not in loaded.rulings():
        found.append(f"EXTENDS {item}: {extends} is no in-force row")
    if (markers or repeated) and not extends:
        candidates = ", ".join(named_rows(loaded, words)) or "the row it repeats"
        because = ", ".join(markers) or f"names of {', '.join(repeated)}"
        found.append(f"REPEAT {item}: the words carry {because}; extend {candidates} with extends=<key>, never a second row")
    if not extends:
        key = new_key(entry, captured[item])
        if key in loaded.rows or key in batch.keys:
            found.append(f"KEY-TAKEN {item}: {key} already exists or comes twice in this batch; extend it or pass another key")
        batch.keys.add(key)
    if supersedes and not (target := loaded.rulings().get(supersedes)):
        found.append(f"SUPERSEDES {item}: {supersedes} is no in-force row")
    elif supersedes and not any(named(words, name) for name in [*names_of(target), *map(str, listed(target.get("surfaces")))]):
        found.append(f"SUPERSEDES-UNNAMED {item}: only owner words naming what {supersedes} covers supersede it")
    if kind == "pick" and (widening := widened(loaded, entry)):
        key, surfaces = widening
        if entry.get("supersedes") != key or not all(named(words, surface) for surface in surfaces):
            found.append(f"PICK-WIDENS {item}: the pick adds {', '.join(surfaces)} to {key}; pass supersedes={key} with owner words naming each surface")
    return found


def stamp() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def numbered(implications: list[dict], start: int, proof: str) -> list[dict]:
    return [
        {"n": start + index, "text": item["text"], "direction": item["direction"], "state": "proposed", "proof": proof, "at": stamp()}
        for index, item in enumerate(implications)
    ]


def union(left: list, right: list) -> list:
    return list(dict.fromkeys([*left, *right]))


def card_of(key: str, fields: dict[str, str]) -> dict:
    lines = [f"{item['n']}. {item['text']}" for item in listed(fields.get("implications"))]
    body = "\n".join(["This also means:", *lines]) if lines else f"You said: {fields['words']}"
    return {"status": "needs_you", "title": f"“{first_sentence(fields['words'])}”", "body": body, "options": CARD_OPTIONS, "waits": card_id(key)}


def write_cards(loaded: Register, key: str, confirms: list[str]) -> list[Path]:
    cards = loaded.state / "cards"
    written = [cards / f"{card_id(key)}.json"]
    write_atomic(written[0], json.dumps(card_of(key, loaded.rows[key]), ensure_ascii=False, indent=2) + "\n")
    for index, line in enumerate(confirms, start=1):
        ident = f"confirms:{card_id(key)}:{index}"
        path = cards / f"{ident}.json"
        write_atomic(path, json.dumps({"status": "needs_you", "title": line, "body": f"Your words: {loaded.rows[key]['words']}", "options": CONFIRMS_OPTIONS, "waits": ident}, ensure_ascii=False, indent=2) + "\n")
        written.append(path)
    return written


def live_lanes(shell: ledger.Shell, cci: str) -> list[str]:
    records = [json.loads(line) for line in shell.run(["cci", "state", "--drive", cci, "--json", "--budget", "1000000"]).splitlines() if line.strip()]
    return sorted({record["lane"] for record in records} - {"root", "main"})


def announce(loaded: Register, args: argparse.Namespace, key: str, how: str) -> None:
    if not (entry := entry_of(args)):
        return
    fields = loaded.rows[key]
    cci = drive.cci_drive(entry)
    lanes = live_lanes(loaded.shell, cci)
    if not is_global(fields) and "all" not in listed(fields.get("lanes")):
        lanes = [lane for lane in lanes if lane in listed(fields.get("lanes"))]
    if not lanes:
        return
    path = loaded.state / "register" / f"announce-{card_id(key)}.md"
    write_atomic(path, "\n".join([f"Register {how} {key} ({fields.get('kind')}): the owner's words and what they mean, verbatim.", *rendered_row(key, fields)]) + "\n")
    argv = ["cci", "post", "--drive", cci, "--lane", "root", "--kind", "decision", "--path", str(path)]
    for lane in lanes:
        argv += ["--to", lane]
    loaded.shell.run(argv)


def repeat_file(loaded: Register, key: str) -> Path:
    return loaded.state / "register" / f"repeat-{card_id(key)}.json"


def applied(loaded: Register, args: argparse.Namespace, captured: dict[str, dict], entry: dict) -> dict:
    item = entry["digest"]
    capture = captured[item]
    if "no" in entry:
        loaded.write(f"no/{item}", no=entry["no"], at=capture.get("at", ""))
        return {"digest": item, "no": entry["no"]}
    proof = f"proof:{capture.get('source', 'prompt')}:{item}"
    at = pacific(capture["at"])
    words = entry["words"]
    if extends := entry.get("extends"):
        key, how = extends, "extended"
        fields = loaded.rows[key]
        kept = listed(fields.get("implications"))
        kind = entry.get("kind") or fields.get("kind")
        reclassified = {"kind": kind, "global": "yes", "steps": ["all"], "lanes": ["all"]} if kind == "scope" else {"kind": kind}
        loaded.write(
            key,
            **reclassified,
            repeats=[*listed(fields.get("repeats")), {"key": new_key(entry, capture), "answer": entry.get("answer") or [], "at": at, "words": words, "proof": proof}],
            said=str(int(fields.get("said") or 1) + 1),
            names=union(names_of(fields), entry.get("names") or []),
            surfaces=union(listed(fields.get("surfaces")), entry.get("surfaces") or []),
            implications=kept + numbered(entry.get("implications") or [], len(kept) + 1, item),
        )
    else:
        key, how, kind = new_key(entry, capture), "new", entry["kind"]
        names = entry.get("names") or []
        status = "pending-owner" if kind == "exception" and not any(named(words, name) for name in names) else "in-force"
        fields = {
            "kind": kind,
            "words": words,
            "answer": entry.get("answer") or [],
            "proof": proof,
            "at": at,
            "said": "1",
            "implications": numbered(entry.get("implications") or [], 1, item),
            "names": names,
            "surfaces": entry.get("surfaces") or [],
            "steps": ["all"] if kind == "scope" else entry.get("steps") or ["all"],
            "lanes": ["all"] if kind == "scope" else entry.get("lanes") or ["all"],
            "tests": entry.get("tests") or [],
            "status": status,
            "evidence": f"classified from capture {item}",
            "global": "yes" if kind in GLOBAL_KINDS else "no",
            "source": "capture",
        }
        if kind in ANCHORED_LATER:
            fields |= {"paths": "pending", "symbols": "pending"}
        if kind == "reserved":
            fields |= {"ops": "pending", "checks": "none"}
        supersedes = entry.get("supersedes")
        loaded.write(key, **fields)
        if supersedes:
            superseded(loaded, supersedes, key)
    hits = Sweeper(loaded, {key: loaded.rows[key]}).files(default_files(loaded, loaded.plan(args.plan)))
    if how == "extended":
        write_atomic(repeat_file(loaded, key), json.dumps({"key": key, "opened_at": stamp(), "hits": [str(hit) for hit in hits], "posted": None}, indent=2) + "\n")
    cards = write_cards(loaded, key, entry.get("confirms") or [])
    announce(loaded, args, key, how)
    row = loaded.rows[key]
    return {
        "digest": item,
        "key": key,
        "how": how,
        "kind": row.get("kind"),
        "status": row.get("status"),
        "words": words,
        "implications": listed(row.get("implications")),
        "names": names_of(row),
        "surfaces": listed(row.get("surfaces")),
        "supersedes": entry.get("supersedes"),
        "extends": extends,
        "cards": [str(path) for path in cards],
        "hits": [str(hit) for hit in hits],
    }


def answer_capture(loaded: Register, identity: str) -> dict:
    answer = ccn(loaded.shell, loaded.repo, "answer", "show", identity)
    if OWNER_ANSWER not in (answer.get("tags") or []):
        raise SystemExit(f"NOT-OWNER {identity}: classify --answer reads only an answer labelled {OWNER_ANSWER}, whose body holds the owner's clicked words")
    return {"digest": answer["id"][:DIGEST_CHARS], "at": answer["created_at"], "source": ANSWER_SOURCE, "text": answer["body"], "answer": answer["id"]}


def cmd_classify(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    sourced = answer_capture(loaded, args.answer) if args.answer else None
    captured = {sourced["digest"]: sourced} if sourced else captures(loaded.state)
    if not args.files:
        done = set(loaded.classified())
        for item, capture in captured.items():
            if item not in done:
                print(json.dumps(capture, ensure_ascii=False))
        return 0
    entries = [entry for path in args.files for entry in classifications(Path(path))]
    if sourced:
        entries = [{"answer": [sourced["answer"]]} | entry | {"digest": sourced["digest"]} for entry in entries]
    with writing(args, shell) as loaded:
        batch = Batch()
        if refused := [line for entry in entries for line in refusals(loaded, captured, entry, batch)]:
            print("\n".join(refused))
            return 1
        for entry in entries:
            print(json.dumps(applied(loaded, args, captured, entry), ensure_ascii=False))
    return 0


def implication_lines(fields: dict[str, str]) -> list[str]:
    return [f"{item['n']}. [{item['direction']}, {item['state']}, {binding(item)}] {item['text']}" for item in listed(fields.get("implications"))]


def cmd_implications(args: argparse.Namespace, shell: ledger.Shell) -> int:
    fields = Register.load(args, shell).row(args.key)
    print("\n".join(implication_lines(fields)) or f"{args.key} carries no implications")
    return 0


def numbers(value: str | None, implications: list[dict]) -> set[int]:
    if not value:
        return set()
    return {item["n"] for item in implications} if value == "all" else {int(part) for part in value.split(",")}


def cmd_confirm(args: argparse.Namespace, shell: ledger.Shell) -> int:
    with writing(args, shell) as loaded:
        return confirmed(args, loaded)


def confirmed(args: argparse.Namespace, loaded: Register) -> int:
    fields = loaded.row(args.key)
    capture = captures(loaded.state).get(args.capture)
    card = card_id(args.key)
    text = (capture or {}).get("text", "")
    on_card = capture and (capture.get("block") == card or args.key in text or card in text)
    if not capture or capture.get("source") not in CONFIRM_SOURCES or not on_card:
        print(f"CAPTURE-NOT-ON-CARD {args.capture}: confirm takes only a captured tap or reply on {args.key}'s card")
        return 1
    implications = listed(fields.get("implications"))
    ticks, strikes = numbers(args.tick, implications), numbers(args.strike, implications)
    if capture["source"] == "slack-action" and (text.strip().lower().removesuffix(RECOMMENDED.lower()) != RIGHT or args.tick != "all" or strikes):
        print(f"CONFIRM-TAP {args.capture}: a Right tap ticks every line with --tick all; Some wrong and Other wait for the owner's reply naming the lines")
        return 1
    named_lines = {int(number) for number in NUMBER.findall(text)}
    if capture["source"] != "slack-action" and (args.tick == "all" or not (ticks | strikes) <= named_lines):
        print(f"CONFIRM-UNNAMED {args.capture}: a reply ticks or strikes only the lines it names by number")
        return 1
    for item in implications:
        if item["n"] in strikes:
            item |= {"state": "struck", "proof": args.capture}
        elif item["n"] in ticks:
            item |= {"state": "ticked", "proof": args.capture}
    loaded.write(args.key, implications=implications)
    print("\n".join(implication_lines(loaded.rows[args.key])))
    return 0


def plan_path(args: argparse.Namespace, loaded: Register) -> Path | None:
    return loaded.plan(args.plan)


def cmd_slice(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    if args.required:
        if not (args.pr and args.head):
            raise SystemExit("slice --required needs --pr and --head")
        repo, number = pull(args, shell)
        base = args.base or json.loads(shell.run(["gh", "pr", "view", number, "--repo", repo, "--json", "baseRefName"]))["baseRefName"]
        sys.stdout.write(required_slice(loaded, compare(shell, repo, base, args.head)))
    elif args.root:
        sys.stdout.write(root_slice(loaded))
    elif args.refresh:
        print(refresh(loaded, Path(args.refresh), plan_path(args, loaded)))
    else:
        if not (args.lane and args.step):
            raise SystemExit("slice needs --lane and --step, --root, --refresh BRIEF, or --required")
        sys.stdout.write(lane_slice(loaded, Want(args.lane, args.step, args.paths, args.codex), plan_path(args, loaded)))
    return 0


def cmd_sweep(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    if args.posted:
        if not args.key:
            raise SystemExit("sweep --posted names the row: sweep KEY --posted REF")
        path = repeat_file(loaded, args.key)
        opened = json.loads(path.read_text())
        write_atomic(path, json.dumps(opened | {"posted": args.posted}, indent=2) + "\n")
        print(f"posted {args.posted} for {args.key}")
        return 0
    sweeper = Sweeper(loaded, {args.key: loaded.row(args.key)} if args.key else loaded.scope())
    if args.artifact or args.paths or args.diff:
        hits = sweeper.files([Path(path) for path in args.artifact])
        hits += sweeper.files(globbed(args.root or args.repo, args.paths), args.root or args.repo)
        hits += sweeper.diff(parse_diff(Path(args.diff).read_text())) if args.diff else []
    else:
        hits = sweeper.files(default_files(loaded, plan_path(args, loaded)))
    for hit in hits:
        print(hit)
    return 0


def record_of(shell: ledger.Shell, cci: str, seq: int) -> dict:
    for line in shell.run(["cci", "grep", "--drive", cci, "--json", "--budget", "10000000", "--since", str(seq - 1), "."]).splitlines():
        if line.strip() and (record := json.loads(line))["seq"] == seq:
            return record
    raise SystemExit(f"no cci record #{seq} in drive {cci}")


@dataclass
class ReadBack:
    record: dict
    body: str
    want: Want
    stamp: str
    attached: str | None


def read_back(shell: ledger.Shell, cci: str, seq: int, lane: str) -> ReadBack | str:
    record = record_of(shell, cci, seq)
    if record.get("kind") != "contract" or record.get("lane") != lane:
        return f"READBACK-NOT-LANES #{seq}: the read-back is {lane}'s own contract record"
    attached = (record.get("refs") or {}).get("path")
    body = "\n".join([record.get("text", ""), Path(attached).read_text() if attached and Path(attached).is_file() else ""])
    if not (header := SLICE_HEADER.search(body)) or not (stamp := SLICE_HASH.search(body)):
        return f"READBACK-NO-SLICE #{seq}: the read-back names no `{SLICE_LINE}` line and `slice-hash:` line"
    if (want := Want.parse(header)).lane != lane:
        return f"READBACK-NOT-LANES #{seq}: the read-back covers lane {want.lane}'s slice, not {lane}'s"
    return ReadBack(record, body, want, stamp["hash"], attached)


def current_hash(loaded: Register, want: Want, plan: Path | None) -> str:
    return SLICE_HASH.search(lane_slice(loaded, want, plan))["hash"]


def cmd_readback(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    if not (entry := entry_of(args)):
        raise SystemExit(f"no drive runs program {loaded.program}; readback reads the lane's contract from its cci drive")
    read = read_back(shell, drive.cci_drive(entry), args.contract, args.lane)
    if isinstance(read, str):
        print(read)
        return 1
    fresh = lane_slice(loaded, read.want, plan_path(args, loaded))
    current = SLICE_HASH.search(fresh)["hash"]
    problems = []
    if read.stamp != current:
        problems.append(f"READBACK-STALE {args.lane}: the read-back covers slice-hash {read.stamp}; the lane's slice is now {current}")
    keys = re.findall(r"^### (\S+) \|", fresh, re.MULTILINE)
    lines = read.body.splitlines()
    problems += [f"READBACK-ROW-MISSING {key}: no read-back line names it" for key in keys if not any(key in line for line in lines if not line.startswith("### "))]
    root = args.root or args.repo
    expected = [line for line in fresh.splitlines() if HIT_LINE.match(line)]
    expected += [str(hit) for hit in Sweeper(loaded, loaded.scope()).files(globbed(root, read.want.paths), root)]
    problems += [f"READBACK-HIT-UNMARKED {hit}" for hit in expected if not any(line.startswith(hit) and VERDICT.search(line[len(hit) :]) for line in lines)]
    if problems:
        print("\n".join(problems))
        return 1
    print(f"readback request {args.lane} {current}: {len(keys)} rows, contract #{args.contract}{f' at {read.attached}' if read.attached else ''}; a fidelity desk grades it against each row's words and implications")
    return 0


def fidelity_lanes(loaded: Register) -> set[str]:
    lanes = json.loads((loaded.state / "runner.json").read_text())["landing"]["fidelity_lane"]
    return {lanes} if isinstance(lanes, str) else set(lanes)


def unaccepted(loaded: Register, cci: str, lane: str, plan: Path | None, reviews: list[dict]) -> str | None:
    desks = fidelity_lanes(loaded)
    graded = [
        (record, found)
        for record in reviews
        if record.get("kind") == "review" and record.get("lane") in desks and (found := READBACK_GRADE.match(record.get("text") or "")) and found["lane"] == lane
    ]
    if not graded:
        return "no fidelity desk has graded a read-back"
    record, newest = max(graded, key=lambda item: item[0]["seq"])
    if newest["verdict"] != "ok":
        return f"the newest grade, #{record['seq']}, is missing: {newest['rest']}"
    if not record.get("re"):
        return f"the newest grade, #{record['seq']}, names no read-back record"
    read = read_back(loaded.shell, cci, int(record["re"]), lane)
    if isinstance(read, str):
        return read
    if (current := current_hash(loaded, read.want, plan)) != newest["rest"].split()[0]:
        return f"the grade #{record['seq']} covers slice-hash {newest['rest'].split()[0]}; the lane's slice is now {current}"
    return None


def cmd_accepted(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    if not (entry := entry_of(args)):
        raise SystemExit(f"no drive runs program {loaded.program}; accepted reads the lane's read-back from its cci drive")
    cci = drive.cci_drive(entry)
    if why := unaccepted(loaded, cci, args.lane, plan_path(args, loaded), pr_records(shell, cci, "--kind", "review")):
        print(f"CONTRACT-MISSING {args.lane}: {why}")
        return 1
    print(f"accepted {args.lane}")
    return 0


def answers_for(loaded: Register, *labels: str) -> list[dict]:
    flags = [arg for label in labels for arg in ("--label", label)]
    return ccn(loaded.shell, loaded.repo, "answer", "list", *flags, "--limit", "0") or []


def authorizing(loaded: Register, key: str, number: str) -> str | None:
    found = [answer for answer in answers_for(loaded, f"authorizes:{key}") if re.search(rf"#{number}\b", f"{answer.get('title', '')}\n{answer.get('body', '')}")]
    return found[0]["id"][:SHORT] if found else None


def date_of(fields: dict[str, str]) -> str:
    at = fields.get("at") or ""
    return match[1] if (match := DATE.match(at)) else at


def cmd_pr_block(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    repo, number = pull(args, shell)
    base = args.base or json.loads(shell.run(["gh", "pr", "view", number, "--repo", repo, "--json", "baseRefName"]))["baseRefName"]
    diff = compare(shell, repo, base, args.head)
    rulings, words = ["## Rulings", ""], ["### In your words", ""]
    matched = [(key, fields) for key, fields in loaded.rulings().items() if (anchors(fields) or symbols(fields)) and (touched(fields, diff) or args.lane in listed(fields.get("lanes")))]
    for key, fields in matched:
        sentence = f"On {date_of(fields)} you said “{first_sentence(fields.get('words') or '')}”"
        if found := symbol_lines(fields, diff, at_home=True):
            symbol, file, line = found[0]
            rulings.append(f"- {key}: met by {symbol} at {file}:{line}")
            words.append(f"- {sentence}; this change does it.")
        elif answer := authorizing(loaded, key, number):
            rulings.append(f"- {key}: authorized by {answer}")
            words.append(f"- {sentence}; this change deletes under it, on your answer {answer}.")
        else:
            rulings.append(f"- {key}: leaves out {(names_of(fields) or symbols(fields) or anchors(fields))[0]}")
            words.append(f"- {sentence}; this change leaves it alone.")
    if not matched:
        rulings.append("- No anchored ruling matches this change.")
        words.append("- None of your rulings names the code this change touches.")
    print("\n".join([*rulings, "", *words]))
    return 0


def table_cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def stands(fields: dict[str, str]) -> str:
    if fields.get("status") == "superseded":
        return f"superseded on {fields.get('updated_at', '')[:10]} by {', '.join(listed(fields.get('superseded_by')))}"
    return STANDING.get(fields.get("status") or "", fields.get("status") or "")


def meaning(fields: dict[str, str]) -> str:
    state = {"ticked": "ticked", "struck": "struck", "proposed": "unconfirmed"}
    return "; ".join(f"{item['n']} {state.get(item['state'], item['state'])}: {item['text']}" for item in listed(fields.get("implications")))


def table(rows: Iterable[tuple[str, dict[str, str]]]) -> list[str]:
    lines = ["| What you said | Times said | What it means for us | Where it stands | PRs landed under it | Record |", "|---|---|---|---|---|---|"]
    for key, fields in rows:
        said = f"“{first_sentence(fields.get('words') or '')}” ({date_of(fields)})"
        landed = ", ".join(dict.fromkeys(PR_REF.findall(fields.get("evidence") or ""))) or "none yet"
        cells = [said, fields.get("said") or "1", meaning(fields) or "nothing beyond your words", stands(fields), landed, key]
        lines.append("| " + " | ".join(table_cell(cell) for cell in cells) + " |")
    return lines


def ordered(rows: dict[str, dict[str, str]]) -> list[tuple[str, dict[str, str]]]:
    return sorted(rows.items(), key=lambda item: (item[1].get("status") not in TROUBLE, KIND_ORDER.get(item[1].get("kind") or "", len(KIND_ORDER)), item[0]))


def unconfirmed(fields: dict[str, str], now: datetime) -> bool:
    return any(
        item["state"] == "proposed" and datetime.fromisoformat(item.get("at") or fields["updated_at"]) < now - UNCONFIRMED_AFTER
        for item in listed(fields.get("implications"))
    )


def cmd_table(args: argparse.Namespace, shell: ledger.Shell) -> int:
    now = utc_now()
    loaded = Register.load(args, shell)
    rulings = {key: fields for key, fields in loaded.rows.items() if not key.startswith(NOT_RULINGS)}
    if not args.since_last_update:
        shown = rulings if args.full else {key: fields for key, fields in rulings.items() if fields.get("status") != "superseded"}
        print("\n".join(table(ordered(shown))))
        return 0
    cursor = loaded.state / "register" / "table-cursor.json"
    last = json.loads(cursor.read_text())["at"] if cursor.exists() else ""
    parts = [
        ("Changed since the last update", {key: fields for key, fields in rulings.items() if fields.get("updated_at", "") > last}),
        ("In trouble", {key: fields for key, fields in rulings.items() if fields.get("status") in TROUBLE}),
        ("Unconfirmed for more than four hours", {key: fields for key, fields in rulings.items() if fields.get("status") != "superseded" and unconfirmed(fields, now)}),
    ]
    lines = []
    for title, rows in parts:
        lines += [f"**{title}**", "", *(table(ordered(rows)) if rows else ["None."]), ""]
    print("\n".join(lines).rstrip())
    write_atomic(cursor, json.dumps({"at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}) + "\n")
    return 0


def cmd_glossary(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    texts = [capture["text"] for capture in captures(loaded.state).values()]
    for fields in loaded.rulings().values():
        texts += [fields.get("words") or "", *(str(repeat.get("words") or "") for repeat in listed(fields.get("repeats")))]
    owner = sorted({word for text in texts for word in WORD.findall(text.lower())} - {noun.lower() for noun in PRODUCT_NOUNS})
    print("\n".join([*PRODUCT_NOUNS, *owner]))
    return 0


def transcript_entries(entry: dict) -> list[dict]:
    projects = drive.drives_dir().parent.parent / "projects"
    found = []
    for session in entry["sessions"]:
        for path in projects.glob(f"*/{session}.jsonl"):
            found += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return found


def strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def contents(entry: dict) -> list:
    content = (entry.get("message") or {}).get("content")
    return [{"type": "text", "text": content}] if isinstance(content, str) else content or []


def card_text(tool_input: dict) -> str:
    text = "\n".join(strings(tool_input))
    files = [Path(path).read_text() for path in CARD_FILE.findall(text) if Path(path).is_file()]
    return "\n".join([text, *files])


def quotes_row(text: str, key: str, fields: dict[str, str]) -> bool:
    return (key in text or card_id(key) in text) and first_sentence(fields.get("words") or "") in " ".join(text.split())


def tapped_card(entries: list[dict], ts: str) -> tuple[dict, str] | None:
    action = None
    for entry in entries:
        for text in strings(entry):
            for raw in SLACK_ACTION.findall(text):
                payload = json.loads(raw)
                if ts in (payload.get("action_ts"), payload.get("ts")):
                    action = payload
    if not action:
        return None
    uses = {item["id"]: item.get("input") or {} for entry in entries for item in contents(entry) if isinstance(item, dict) and item.get("type") == "tool_use"}
    for entry in entries:
        for item in contents(entry):
            if isinstance(item, dict) and item.get("type") == "tool_result" and action["ts"] in "\n".join(strings(item.get("content"))):
                return action, card_text(uses.get(item.get("tool_use_id"), {}))
    return action, ""


def texts_of(entry: dict) -> str:
    return "\n".join(item.get("text", "") for item in contents(entry) if isinstance(item, dict) and item.get("type") == "text")


def replied_to(entries: list[dict], text: str) -> str | None:
    said = ""
    for entry in entries:
        if entry.get("type") == "assistant":
            said = texts_of(entry) or said
        elif entry.get("type") == "user" and text in texts_of(entry):
            return said
    return None


def cmd_authorize(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    key = args.row
    if key not in loaded.rulings():
        print(f"AUTHORIZE-NO-ROW {key}: no in-force register row")
        return 1
    fields = loaded.rows[key]
    if not (entry := entry_of(args)):
        raise SystemExit(f"no drive runs program {loaded.program}; authorize reads its root transcript")
    entries = transcript_entries(entry)
    owner = answers_for(loaded, f"program:{loaded.program}", "from:owner")
    if args.action:
        if not (tapped := tapped_card(entries, args.action)):
            print(f"AUTHORIZE-NO-TAP {key}: the root transcript holds no slack.action tap with ts {args.action}; a root go is no authorization")
            return 1
        action, shown = tapped
        if not AFFIRMATIVE.match(action.get("label") or ""):
            print(f"AUTHORIZE-NOT-AFFIRMATIVE {key}: the tap was {action.get('label')!r}, which grants nothing")
            return 1
        if not quotes_row(shown, key, fields):
            print(f"AUTHORIZE-CARD-NAMES-NO-ROW {key}: the tapped card does not quote {key} and its words")
            return 1
        permalink = f"p{action['ts'].replace('.', '')}"
        found = [
            answer
            for answer in owner
            if f"proof:slack:{action.get('action_ts')}" in (answer.get("tags") or [])
            or (permalink in (answer.get("body") or "") and action["label"] in (answer.get("body") or ""))
        ]
    else:
        capture = captures(loaded.state).get(args.prompt)
        if not capture or (said := replied_to(entries, capture["text"])) is None or not quotes_row(said, key, fields):
            print(f"AUTHORIZE-PROMPT-NAMES-NO-ROW {key}: capture {args.prompt} replies to no root message quoting {key} and its words")
            return 1
        found = [answer for answer in owner if f"proof:prompt:{args.prompt}" in (answer.get("tags") or [])]
    named_row = [answer for answer in found if key in f"{answer.get('title', '')}\n{answer.get('body', '')}" or card_id(key) in f"{answer.get('title', '')}\n{answer.get('body', '')}"]
    if not named_row:
        print(f"AUTHORIZE-ANSWER-NAMES-NO-ROW {key}: no from:owner answer for this capture names {key}")
        return 1
    answer = named_row[0]["id"]
    shell.run(["ccn", "-R", loaded.repo, "answer", "edit", answer, "--add-label", f"authorizes:{key}"])
    print(f"authorized {key} by {answer[:SHORT]}")
    return 0


def cmd_status(args: argparse.Namespace, shell: ledger.Shell) -> int:
    with writing(args, shell) as loaded:
        loaded.row(args.key)
        loaded.write(args.key, status=args.state, evidence=args.evidence)
    print(f"{args.key} {args.state} ({args.evidence})")
    return 0


def cmd_anchor(args: argparse.Namespace, shell: ledger.Shell) -> int:
    fields = {name: values for name, values in (("paths", args.paths), ("symbols", args.symbols), ("ops", args.ops)) if values}
    if not fields:
        raise SystemExit("anchor needs --paths, --symbols or --ops")
    with writing(args, shell) as loaded:
        loaded.row(args.key)
        loaded.write(args.key, **fields)
    print(f"{args.key} anchored: {', '.join(f'{name} {len(values)}' for name, values in fields.items())}")
    return 0


def superseded(loaded: Register, old_key: str, replacement: str) -> None:
    old, new = loaded.row(old_key), loaded.row(replacement)
    if (old_answers := listed(old.get("answer"))) and (new_answers := listed(new.get("answer"))):
        loaded.shell.run(["ccn", "-R", loaded.repo, "answer", "supersede", str(old_answers[0]), "--by", str(new_answers[0])])
    loaded.write(old_key, status="superseded", superseded_by=union(listed(old.get("superseded_by")), [replacement]), evidence=f"superseded by {replacement}: “{first_sentence(new.get('words') or '')}”")
    loaded.write(replacement, supersedes=union(listed(new.get("supersedes")), [old_key]))


def cmd_supersede(args: argparse.Namespace, shell: ledger.Shell) -> int:
    with writing(args, shell) as loaded:
        superseded(loaded, args.old, args.by)
    print(f"{args.old} superseded by {args.by}")
    return 0


@dataclass
class Findings:
    refusals: list[str]
    blockers: list[str]

    def read(self, what: str, load: Callable[[], object]) -> object:
        try:
            return load()
        except READ_ERRORS as error:
            self.blockers.append(f"BLOCKER {what}: {' '.join(str(error).split())[:CCI_TEXT]}")
            return None


@dataclass
class Head:
    repo: str
    number: str
    sha: str
    body: str
    diff: Diff

    @cached_property
    def rulings(self) -> str:
        return found[0] if (found := RULINGS_SECTION.search(self.body)) else ""

    @cached_property
    def in_your_words(self) -> str | None:
        return found[0] if (found := IN_YOUR_WORDS.search(self.body)) else None

    @cached_property
    def cited(self) -> set[str]:
        return {token[:SHORT] for token in CITED.findall(self.rulings)}


def cites(head: Head, key: str, fields: dict[str, str]) -> bool:
    return key in head.rulings or bool({str(answer)[:SHORT] for answer in listed(fields.get("answer"))} & head.cited)


def op_hits(op: str, diff: Diff) -> bool:
    if "/" in op:
        return any(fnmatch.fnmatchcase(file, op) for file, _, _ in diff.added)
    if any(char in op for char in "*?["):
        return any(fnmatch.fnmatchcase(text.strip().lower(), op.lower()) for _, _, text in diff.added)
    return any(named(text, op) for _, _, text in diff.added)


def authorized(loaded: Register, head: Head, key: str) -> bool:
    return any(answer["id"][:SHORT] in head.cited for answer in answers_for(loaded, f"authorizes:{key}")) or bool(authorizing(loaded, key, head.number))


def defined_tests(lines: list[tuple[str, int, str]], kept: list[tuple[str, int, str]]) -> dict[str, str]:
    texts = {(file, number): text for file, number, text in [*kept, *lines]}
    found = {}
    for file, number, text in lines:
        match = TEST_DEFINITION.match(text)
        rust = RUST_TEST_FILE.search(file) or RUST_TEST.match(texts.get((file, number - 1), ""))
        if match and (name := match["go"] or match["py"] or match["ts"] or (match["rs"] if rust else None)):
            found[name] = file
    return found


def appendix(plan: Path | None) -> str:
    text = plan.read_text() if plan and plan.is_file() else ""
    return text[found.start() :] if (found := APPENDIX.search(text)) else ""


def corpus_text(loaded: Register, findings: Findings, incident: bool) -> str:
    pin = loaded.rows.get(CORPUS_KEY) or {}
    path = Path(pin.get("path") or "")
    if not path.is_file():
        why = f"TESTS-CORPUS {path} missing"
    elif hashlib.sha256(path.read_bytes()).hexdigest() != pin.get("sha256"):
        why = f"TESTS-CORPUS {path} sha256 differs from the register's pin"
    else:
        return path.read_text()
    (findings.blockers if incident else findings.refusals).append(f"BLOCKER {why}" if incident else why)
    return ""


def removed_tests(loaded: Register, head: Head, findings: Findings, incident: bool, plan: Path | None) -> list[str]:
    kept = defined_tests(head.diff.added, head.diff.kept_after)
    gone = [name for name in defined_tests(head.diff.removed, head.diff.kept_before) if name not in kept]
    if not gone:
        return []
    corpus = corpus_text(loaded, findings, incident)
    named_in = appendix(plan) if incident else f"{corpus}\n{appendix(plan)}"
    authorizers = loaded.answer_words()
    return [
        f"TEST-REMOVED {name}"
        for name in gone
        if named(named_in, name)
        and not any(named(words, name) for cited in head.cited for words, _ in authorizers.get(cited, []))
    ]


def pr_records(shell: ledger.Shell, bus: str, *flags: str) -> list[dict]:
    return cci.records(shell.run, bus, *flags)


def finder_major(records: list[dict], sha: str) -> str | None:
    verdicts = [(record["seq"], found) for record in records if (found := FINDER.match(record.get("text") or "")) and sha.startswith(found["head"])]
    newest = max(verdicts, key=lambda item: item[0])[1] if verdicts else None
    return newest["head"] if newest and newest["verdict"] == "major" else None


def lane_of(records: list[dict]) -> str | None:
    for kind in LANE_KINDS:
        if owned := [(record["seq"], record["lane"]) for record in records if record.get("kind") == kind and record.get("lane")]:
            return max(owned)[1]
    return None


def plain_problems(text: str) -> list[str]:
    seen = PARENTHESIZED.sub(" ", OWNER_QUOTED.sub(" ", text))
    return list(dict.fromkeys(f'RULINGS-PLAIN {kind} "{hit[0]}"' for kind, pattern in UNPLAIN for hit in pattern.finditer(seen)))


def card_answers(loaded: Register, findings: Findings) -> set[str] | None:
    if (listed_answers := findings.read("the owner's answers", lambda: answers_for(loaded))) is None:
        return None
    return set(loaded.answer_words()) | {str(answer["id"])[:SHORT] for answer in listed_answers}


def cites_answer(line: str, answers: set[str] | None) -> bool:
    cited = {token[:SHORT] for token in CITED.findall(line)}
    return bool(cited) if answers is None else bool(cited & answers)


def decided(body: str, answers: set[str] | None) -> list[str]:
    own = found[0] if (found := RULINGS_LINES.search(body)) else ""
    prose = FENCED.sub(" ", body.replace(own, " ") if own else body)
    lines = [(line, OWNER_QUOTED.sub(" ", line)) for line in prose.splitlines()]
    return [f"LANE-DECIDED {line.strip()[:CCI_TEXT]}" for line, own in lines if DECISION.search(own) and not cites_answer(line, answers)]


def check_pr(loaded: Register, args: argparse.Namespace, head: Head, findings: Findings) -> None:
    rulings = loaded.rulings()
    cci = drive.cci_drive(entry_of(args))
    records = findings.read("the PR's cci records", lambda: pr_records(loaded.shell, cci, "--pr", f"{head.repo}#{head.number}"))
    uncited = [key for key, fields in rulings.items() if (anchors(fields) or symbols(fields)) and touched(fields, head.diff) and not cites(head, key, fields)]
    if uncited:
        findings.refusals.append(f"RULINGS-UNCITED {','.join(uncited)}")
    touches = {found[1] for record in records or [] if record.get("kind") == "review" for found in TOUCHES.finditer(record.get("text") or "")}
    for key, fields in rulings.items():
        ops = [op for op in map(str, listed(fields.get("ops"))) if op not in UNANCHORED]
        if fields.get("kind") == "reserved" and (key in touches or any(op_hits(op, head.diff) for op in ops)):
            if findings.read(f"the authorizes:{key} answers", lambda key=key: authorized(loaded, head, key)) is False:
                findings.refusals.append(f"RESERVED-NO-ANSWER {key}\t{first_sentence(fields.get('words') or '')}")
    findings.refusals += findings.read("the removed tests", lambda: removed_tests(loaded, head, findings, args.incident, loaded.plan(args.plan))) or []
    if args.incident:
        return
    if records is not None and (major := finder_major(records, head.sha)):
        findings.refusals.append(f"FINDER-MAJOR {major[:SHORT]}")
    authorizers = loaded.answer_words()
    for hit in Sweeper(loaded, loaded.scope()).diff(head.diff):
        if hit.status == "open" and not any(hit.name.lower() not in banned and named(words, hit.name) for cited in head.cited for words, banned in authorizers.get(cited, [])):
            findings.refusals.append(f"NAME-HIT {hit.key} {hit.file}:{hit.line} {hit.name}")
    words = head.in_your_words
    findings.refusals += ["RULINGS-PLAIN no ### In your words section"] if words is None else plain_problems(words)
    findings.refusals += decided(head.body, card_answers(loaded, findings))
    if records is None:
        return
    if not (lane := lane_of(records)):
        findings.blockers.append(f"BLOCKER no cci record names the lane of {head.repo}#{head.number}")
    elif (reviews := findings.read("the read-back reviews", lambda: pr_records(loaded.shell, cci, "--kind", "review"))) is not None:
        why = findings.read(f"the read-back of {lane}", lambda: unaccepted(loaded, cci, lane, loaded.plan(args.plan), reviews))
        if why:
            findings.refusals.append(f"CONTRACT-MISSING {lane}: {why}")


def drive_checkout(args: argparse.Namespace) -> str:
    if not (entry := entry_of(args)):
        raise SystemExit(f"no drive registry entry runs {args.drive or args.program}")
    return entry["checkout"]


def cmd_check_pr(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not ledger.PR_KEY.fullmatch(args.pr):
        print(f"--pr {args.pr!r} is neither N nor OWNER/NAME#N", file=sys.stderr)
        return 2
    findings = Findings([], [])
    repo, number = pull(args, shell)
    loaded = findings.read("the register", lambda: Register.load(args, shell, drive_checkout(args)))
    view = findings.read(f"{repo}#{number}", lambda: json.loads(shell.run(["gh", "pr", "view", number, "--repo", repo, "--json", "body,baseRefName"])))
    diff = findings.read(f"the compare of {repo}#{number}", lambda: compare(shell, repo, args.base or view["baseRefName"], args.head)) if view else None
    if loaded and view and diff:
        check_pr(loaded, args, Head(repo, number, args.head, view.get("body") or "", diff), findings)
    for line in [*sorted(findings.refusals, key=lambda line: CHECK_PR_ORDER.index(line.split()[0])), *findings.blockers]:
        print(line)
    return 1 if findings.refusals else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rulings.py", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    verbs = []

    def verb(name: str, handler, summary: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=summary)
        sub.set_defaults(handler=handler)
        verbs.append(sub)
        return sub

    verb("register", cmd_register, "print the newest standing-rules:<program> doc as {id, body}, or null")
    match = verb("match", cmd_match, "print the durable rulings the brief on stdin is about")
    match.add_argument("-k", type=int, default=MATCHES)
    match.add_argument("--budget", type=int, default=BUDGET, metavar="BYTES")
    match.add_argument("--exclude", action="append", default=[], metavar="ID")
    rows = verb("rows", cmd_rows, "print the register as JSON {ledger, program, state_dir, rows, classified}")
    rows.add_argument("--json", action="store_true")
    classify = verb("classify", cmd_classify, "write classified owner captures as register rows, or list the unclassified captures")
    classify.add_argument("files", nargs="*", metavar="CLASSIFICATION")
    classify.add_argument("--answer", metavar="ID", help="classify the owner's words in this cc-notes answer's body instead of a captured prompt")
    classify.add_argument("--plan", metavar="PATH")
    implications = verb("implications", cmd_implications, "print a row's implications with their state")
    implications.add_argument("key")
    confirm = verb("confirm", cmd_confirm, "tick or strike a row's implications from a captured tap or reply on its card")
    confirm.add_argument("key")
    confirm.add_argument("--capture", required=True, metavar="DIGEST")
    confirm.add_argument("--tick", metavar="N,...|all")
    confirm.add_argument("--strike", metavar="N,...")
    sliced = verb("slice", cmd_slice, "print the rows a lane, the root, or a head's grade must carry, verbatim")
    sliced.add_argument("--lane")
    sliced.add_argument("--step")
    sliced.add_argument("--paths", action="append", default=[], metavar="GLOB")
    sliced.add_argument("--codex", action="store_true")
    sliced.add_argument("--root", action="store_true")
    sliced.add_argument("--refresh", metavar="BRIEF")
    sliced.add_argument("--required", action="store_true")
    sliced.add_argument("--pr", metavar="[OWNER/NAME#]N")
    sliced.add_argument("--head", metavar="SHA")
    sliced.add_argument("--base", metavar="REF")
    sliced.add_argument("--plan", metavar="PATH")
    swept_verb = verb("sweep", cmd_sweep, "grep every scope row's names, or one row's, and print each hit as open, deleted or authorized")
    swept_verb.add_argument("key", nargs="?")
    swept_verb.add_argument("--artifact", action="append", default=[], metavar="PATH")
    swept_verb.add_argument("--paths", action="append", default=[], metavar="GLOB")
    swept_verb.add_argument("--root", metavar="DIR")
    swept_verb.add_argument("--diff", metavar="FILE")
    swept_verb.add_argument("--plan", metavar="PATH")
    swept_verb.add_argument("--posted", metavar="REF")
    readback = verb("readback", cmd_readback, "check a lane's read-back against its current slice and print the grade request")
    readback.add_argument("--lane", required=True)
    readback.add_argument("--contract", required=True, type=int, metavar="SEQ")
    readback.add_argument("--plan", metavar="PATH")
    readback.add_argument("--root", metavar="DIR")
    accepted = verb("accepted", cmd_accepted, "exit 0 when a fidelity desk's newest grade of the lane's read-back is ok for its current slice")
    accepted.add_argument("--lane", required=True)
    accepted.add_argument("--plan", metavar="PATH")
    block = verb("pr-block", cmd_pr_block, "print a PR body's ## Rulings and ### In your words sections")
    block.add_argument("--pr", required=True, metavar="[OWNER/NAME#]N")
    block.add_argument("--head", required=True, metavar="SHA")
    block.add_argument("--base", metavar="REF")
    block.add_argument("--lane", required=True)
    shown = verb("table", cmd_table, "print one plain row per ruling, or the delta since the last update")
    shown.add_argument("--since-last-update", action="store_true")
    shown.add_argument("--full", action="store_true")
    verb("glossary", cmd_glossary, "print the product nouns and the owner's own captured words")
    authorize = verb("authorize", cmd_authorize, "label the owner's answer authorizes:<key> from a captured tap or reply quoting the row")
    authorize.add_argument("--row", required=True, metavar="KEY")
    tap = authorize.add_mutually_exclusive_group(required=True)
    tap.add_argument("--action", metavar="TS")
    tap.add_argument("--prompt", metavar="DIGEST")
    status = verb("status", cmd_status, "set a row's status with the evidence behind it (graders only)")
    status.add_argument("key")
    status.add_argument("state", choices=STATES)
    status.add_argument("--evidence", required=True, metavar="REF")
    anchor = verb("anchor", cmd_anchor, "write a row's code anchors (anchor and seed lanes only)")
    anchor.add_argument("key")
    anchor.add_argument("--paths", nargs="+", default=[])
    anchor.add_argument("--symbols", nargs="+", default=[])
    anchor.add_argument("--ops", nargs="+", default=[])
    supersede = verb("supersede", cmd_supersede, "record that one row's answer replaces another's")
    supersede.add_argument("old")
    supersede.add_argument("--by", required=True)
    checked = verb("check-pr", cmd_check_pr, "print one refusal line per way a PR head breaks the register, and exit 1 on any")
    checked.add_argument("--pr", required=True, metavar="[OWNER/NAME#]N")
    checked.add_argument("--head", required=True, metavar="SHA")
    checked.add_argument("--base", metavar="REF")
    checked.add_argument("--incident", action="store_true")
    checked.add_argument("--plan", metavar="PATH")
    for sub in verbs:
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
