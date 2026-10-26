#!/usr/bin/env python3
"""The merge-row gate: every merge-triage row the owner sees names its PR's current head, graded at that head, in words written after it.

    board_rows.py --config RUNNER_JSON --check [--json] BLOCK_JSON
    board_rows.py --config RUNNER_JSON [--json] BLOCK_JSON -- cc-present update-block|push [ARGS...]

STDLIB ONLY. `--config` is the landing runner's config, which names the drive, `landing.repo`, and
`landing.fidelity_lane`. A row is a triage item whose id is `merge-row-<pr>[-<sha7>]`, anywhere in
the block; its head is the `commit <sha>` its hint names, and its `textAt` is the RFC 3339 time
the writer last wrote its words. Each row fails, with one line `<row-id> <pr> <head> <reasons>`, when:

    stale     its head is not the PR's head on GitHub; the reason names the new head
    ungraded  no fidelity grade or no finder review on the drive's bus names its head, read as
              desk-runner.py reads them
    old text  its `textAt` is missing or not newer than its head's commit time
    not open  the PR is closed or merged on GitHub, or a root go, decide or decision record says it
              is superseded or closed, naming that record's seq, and no later root go names it at
              its head

Two rows whose first sentences of five words or more, the label when there is none, share a
token Jaccard above 0.8 or their first 60 characters are flagged together as `FLAG` lines for the
root; a flag fails nothing. A GitHub or cci read that fails prints a `WARN` line and fails no row
on the checks it would have read. Any failing row exits 1. Otherwise `--check` exits 0, and the
push form runs its command with a copy of the block, `textAt` removed, as its last argument and
exits with the command's status. `--json` prints rows, flags, and warnings as one object.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import cci
import grades

ROW_ID = re.compile(r"^merge-row-(?P<pr>\d+)(?:-[0-9a-f]{7,40})?$")
ROW_HEAD = re.compile(r"\bcommit (?P<sha>[0-9a-f]{7,40})\b")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
WORD = re.compile(r"[a-z0-9]+")
LABEL_NUMBER = re.compile(r"^#\d+:\s*")
WITHDRAWN = re.compile(r"^\s*withdrawn\b", re.IGNORECASE)
TEXT_AT = "textAt"
PRS_PER_QUERY = 40
READERS = 8
SENTENCE_WORDS = 5
SAME_PREFIX = 60
SAME_JACCARD = 0.8
OPEN = "OPEN"


@dataclass(frozen=True)
class Done:
    code: int
    out: str
    err: str


class Shell:
    """The single side-effect boundary: gh, cci, the push command, and blob reads."""

    def run(self, argv: list[str]) -> Done:
        done = subprocess.run(argv, capture_output=True, text=True)
        return Done(done.returncode, done.stdout, done.stderr)

    def push(self, argv: list[str]) -> int:
        return subprocess.run(argv).returncode

    def read(self, path: str) -> str | None:
        try:
            return Path(path).read_text(errors="replace")
        except OSError:
            return None


class ReadFailed(Exception):
    pass


@dataclass
class Row:
    id: str
    pr: str
    head: str
    text_at: str
    sentence: str
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Pull:
    state: str
    head: str
    committed: str


def triage_items(node: object) -> Iterator[dict]:
    if isinstance(node, dict):
        if node.get("type") == "triage":
            yield from node.get("items", [])
        for value in node.values():
            yield from triage_items(value)
    elif isinstance(node, list):
        for value in node:
            yield from triage_items(value)


def first_sentence(item: dict) -> str:
    sentences = [part.strip() for part in SENTENCE.split(item.get("md", "").strip())]
    return next((part for part in sentences if len(WORD.findall(part.lower())) >= SENTENCE_WORDS), LABEL_NUMBER.sub("", item.get("label", "")))


def rows_of(block: dict) -> list[Row]:
    rows = []
    for item in triage_items(block):
        if not (named := ROW_ID.match(item.get("id", ""))):
            continue
        head = ROW_HEAD.search(item.get("hint", ""))
        rows.append(Row(item["id"], named["pr"], head["sha"] if head else "", item.get(TEXT_AT, ""), first_sentence(item)))
    return rows


def pulls_query(repo: str, prs: list[str]) -> str:
    owner, name = repo.split("/")
    fields = " ".join(f"p{pr}: pullRequest(number: {pr}) {{ state headRefOid commits(last: 1) {{ nodes {{ commit {{ committedDate }} }} }} }}" for pr in prs)
    return f'query {{ repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}'


def read_pulls(shell: Shell, repo: str, prs: list[str], warnings: list[str]) -> dict[str, Pull | None]:
    """Each PR's state, head, and head commit time from GitHub, None for one GitHub does not know; a refused read leaves its PRs out and warns."""
    pulls: dict[str, Pull | None] = {}
    for start in range(0, len(prs), PRS_PER_QUERY):
        batch = prs[start : start + PRS_PER_QUERY]
        done = shell.run(["gh", "api", "graphql", "-f", f"query={pulls_query(repo, batch)}"])
        try:
            found = json.loads(done.out)["data"]["repository"]
        except (ValueError, KeyError, TypeError):
            warnings.append(f"WARN GitHub refused the PR read for {' '.join(f'#{pr}' for pr in batch)}: {(done.err or done.out).strip()[:200] or f'exit {done.code}'}; their stale, old-text and closed checks are skipped")
            continue
        for pr in batch:
            node = found.get(f"p{pr}")
            pulls[pr] = Pull(node["state"], node["headRefOid"], node["commits"]["nodes"][0]["commit"]["committedDate"]) if node else None
    return pulls


def tail(shell: Shell) -> Callable[[list[str]], str]:
    def run(argv: list[str]) -> str:
        done = shell.run(argv)
        if done.code != 0:
            raise ReadFailed(f"cci tail exited {done.code}: {(done.err or done.out).strip()[:200]}")
        return done.out

    return run


def ruling_text(shell: Shell, record: dict) -> str:
    path = record.get("refs", {}).get("path")
    return (shell.read(path) if path else None) or record["text"]


def closes(pr: str, text: str) -> bool:
    verb = rf"\b(?:clos(?:e|es|ed|ing)|supersed(?:es|ing))\s+(?:#\d+(?:\s*,\s*|\s*,?\s+and\s+))*#{pr}\b"
    ruled = rf"#{pr}\b[^.;:()\n]{{0,40}}?\b(?:superseded|closed)\b"
    return re.search(verb, text, re.IGNORECASE) is not None or re.search(ruled, text, re.IGNORECASE) is not None


def closing_ruling(rulings: list[dict], pr: str, head: str) -> dict | None:
    """The newest root ruling that says `pr` is superseded or closed, unless a later root `go` names it at `head`."""
    ruling = max((record for record in rulings if closes(pr, record["full"])), key=lambda record: record["seq"], default=None)
    if ruling is None:
        return None
    revived = [record for record in grades.gone_at([record for record in rulings if record["kind"] == "go" and record["seq"] > ruling["seq"]], pr, head) if not WITHDRAWN.match(record["text"])]
    return None if revived else ruling


def root_rulings(shell: Shell, drive: str) -> list[dict]:
    kinds = [f"--kind={kind}" for kind in sorted(cci.ROOT_ANSWER_KINDS)]
    records = [record for record in cci.records(tail(shell), drive, "--lane", "root", *kinds) if record["lane"] == "root" and record["drive"] == drive and record["kind"] in cci.ROOT_ANSWER_KINDS]
    return [record | {"full": ruling_text(shell, record)} for record in records]


def graded_reasons(shell: Shell, drive: str, lanes: list[str], repo: str, row: Row) -> list[str]:
    reasons = []
    if not grades.standing(grades.fidelity_grades(tail(shell), drive, lanes, repo, row.pr), lanes, row.head):
        reasons.append(f"ungraded: no fidelity grade from {' or '.join(lanes)} names {row.head}")
    if not grades.newest_finder(grades.finder_records(grades.finder_reviews(tail(shell), drive, repo, row.pr)), row.head):
        reasons.append(f"ungraded: no finder review names {row.head}")
    return reasons


def parsed(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return None


def github_reasons(row: Row, pull: Pull | None) -> list[str]:
    if pull is None:
        return ["not open: GitHub knows no such PR"]
    reasons = []
    if pull.state != OPEN:
        reasons.append(f"not open: the PR is {pull.state.lower()} on GitHub")
    if not grades.names_head(row.head, pull.head):
        reasons.append(f"stale: GitHub's head is {pull.head[:7]}")
    elif not (written := parsed(row.text_at)):
        reasons.append(f"old text: no {TEXT_AT} on the row")
    elif written <= datetime.fromisoformat(pull.committed):
        reasons.append(f"old text: written {row.text_at}, not after {pull.head[:7]} was committed at {pull.committed}")
    return reasons


def flags(rows: list[Row]) -> list[dict]:
    found = []
    for one, other in itertools.combinations(rows, 2):
        mine, theirs = set(WORD.findall(one.sentence.lower())), set(WORD.findall(other.sentence.lower()))
        jaccard = len(mine & theirs) / len(mine | theirs) if mine | theirs else 0.0
        if jaccard > SAME_JACCARD or one.sentence.lower()[:SAME_PREFIX] == other.sentence.lower()[:SAME_PREFIX]:
            found.append({"rows": [one.id, other.id], "prs": [one.pr, other.pr], "jaccard": round(jaccard, 2), "sentences": [one.sentence, other.sentence]})
    return found


def check(shell: Shell, config: dict, block: dict) -> tuple[list[Row], list[dict], list[str]]:
    drive, landing = config["drive"], config["landing"]
    repo, configured = landing["repo"], landing["fidelity_lane"]
    lanes = [configured] if isinstance(configured, str) else list(configured)
    rows = rows_of(block)
    warnings: list[str] = []
    for row in rows:
        if not row.head:
            row.reasons.append("stale: the hint names no `commit <sha>`")
    named = [row for row in rows if row.head]
    pulls = read_pulls(shell, repo, sorted({row.pr for row in named}, key=int), warnings)
    try:
        rulings = root_rulings(shell, drive)
    except ReadFailed as failed:
        rulings = None
        warnings.append(f"WARN the root rulings did not read ({failed}); no row is checked for a superseded or closed ruling")
    with ThreadPoolExecutor(READERS) as pool:
        graded = dict(zip((row.id for row in named), pool.map(lambda row: read_graded(shell, drive, lanes, repo, row), named), strict=True))
    for row in named:
        if row.pr in pulls:
            row.reasons += github_reasons(row, pulls[row.pr])
        if rulings is not None and (ruling := closing_ruling(rulings, row.pr, row.head)):
            row.reasons.append(f"not open: root ruled it superseded or closed on cci #{ruling['seq']}")
        reasons = graded[row.id]
        if isinstance(reasons, ReadFailed):
            warnings.append(f"WARN #{row.pr}: the grades did not read ({reasons}); its ungraded check is skipped")
        else:
            row.reasons += reasons
    return rows, flags(rows), warnings


def read_graded(shell: Shell, drive: str, lanes: list[str], repo: str, row: Row) -> list[str] | ReadFailed:
    try:
        return graded_reasons(shell, drive, lanes, repo, row)
    except ReadFailed as failed:
        return failed


def stripped(block: object) -> object:
    if isinstance(block, dict):
        return {key: stripped(value) for key, value in block.items() if key != TEXT_AT}
    if isinstance(block, list):
        return [stripped(value) for value in block]
    return block


def report(rows: list[Row], found: list[dict], warnings: list[str], as_json: bool) -> None:
    if as_json:
        print(json.dumps({"rows": [asdict(row) | {"ok": not row.reasons} for row in rows], "flags": found, "warnings": warnings}))
        return
    for line in warnings:
        print(line, file=sys.stderr)
    for row in rows:
        if row.reasons:
            print(f"{row.id} {row.pr} {row.head or '-'} {'; '.join(row.reasons)}")
    for flag in found:
        print(f"FLAG {' '.join(flag['rows'])} {' '.join(f'#{pr}' for pr in flag['prs'])} first sentences match (jaccard {flag['jaccard']}): {flag['sentences'][0]}")


def main(argv: list[str], shell: Shell | None = None) -> int:
    shell = shell or Shell()
    ours, command = split(argv)
    parser = argparse.ArgumentParser(prog="board_rows.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("block", type=Path)
    args = parser.parse_args(ours)
    if args.check == bool(command):
        parser.error("pass --check, or a push command after --, not both")
    block = json.loads(args.block.read_text())
    rows, found, warnings = check(shell, json.loads(args.config.expanduser().read_text()), block)
    report(rows, found, warnings, args.json)
    if any(row.reasons for row in rows):
        return 1
    if args.check:
        return 0
    with tempfile.NamedTemporaryFile("w", prefix=f"{args.block.stem}.", suffix=".json") as copy:
        json.dump(stripped(block), copy, indent=1)
        copy.flush()
        return shell.push([*command, copy.name])


def split(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" not in argv:
        return argv, []
    at = argv.index("--")
    return argv[:at], argv[at + 1 :]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
