#!/usr/bin/env python3
"""The merge-row gate: every merge row the owner sees names its PR's current head, carries what he needs to decide, in words written after that head, and keeps his verdict.

    board_rows.py --config RUNNER_JSON --check [--json] [--session ID] [--finders DIR] BLOCK_JSON
    board_rows.py --config RUNNER_JSON [--json] [--session ID] [--finders DIR] BLOCK_JSON -- cc-present update-block|push [ARGS...]

STDLIB ONLY. `--config` is the landing runner's config, which names the drive, `landing.repo`, and
`landing.fidelity_lane`. A row is a triage item whose id is `merge-row-<pr>[-<sha7>]`, anywhere in
the block, its head the `commit <sha>` its hint names; or a `pr-groups` row, its id its PR number
and its head its `head`. Its `textAt` is the RFC 3339 time the writer last wrote its words. Each row
fails, with one line `<row-id> <pr> <head> <reasons>`, when:

    stale     its head is not the PR's head on GitHub; the reason names the new head
    old text  its `textAt` is missing or not newer than its head's commit time
    not open  the PR is closed or merged on GitHub, or a root go, decide or decision record has it
              as the subject of superseded, closed or landed, or a verb that closes or supersedes
              it, naming that record's seq, and no later root go names it at its head
    missing   a triage row's label has no words after `#<pr>:`, or a fact in `FIELDS` is absent or
              empty: what it changes for users, how it differs from the plan, its risk, the
              finder's verdict, CI, and what gates its landing; a `pr-groups` row's field in
              `PR_FIELDS` is absent or empty, `review` carrying the finder and CI
    moved     the board's outcomes record a verdict on its id under another block, and none
              under this one
    renamed   the board's outcomes record a verdict on another id for its PR that this push drops,
              none on this id, and no root record that re-keys names this id or its head; a
              `pr-groups` row clears it by `cc-present verdict import` at the head the owner tapped
    approved  a root `go` names the PR at its head, no root `withdraw` closes that go, and the board
              records no approval for the row at that head, so the row asks a tap he already gave;
              `cc-present verdict import` at the head, or dropping the row, clears it
    rejected  the board records his reject for the row at its head, so the row asks again what he
              refused; it returns only at a new head
    landed    its blocker, the `blocker` field or the `Gate` fact, names a PR GitHub shows merged
    one tap   a row's or its group's words say one tap approves several PRs, and one they name or
              group with it is rejected on the board or no longer open on GitHub

A markdown block of the pushed document that lists a PR as a `- #<pr>:` bullet fails as
`<block>#<pr>`, with `listed`, when GitHub shows that PR merged or closed: a landed PR leaves every
list the owner reads in the same push.

A row with no fidelity grade or no finder review at its head gets a `WARN` line and fails
nothing: the grade gates the enqueue, never the row. Grades and finder reviews are read from one
scan of the drive's `review` and `head` records, as desk-runner.py reads them, whatever verdict a
finder posted; a codex finder reply saved as `<finders dir>/<pr>-<sha7>.md` counts as a finder
review too. `--finders` names that directory, `finders/` beside the config by default. A carry
counts as both at its head: a `review`, `head` or root record that says carried or patch-identical
in the sentence or the stretch of text where `#<pr> <head>` or `#<pr> <old>-><head>` appears, or a
record whose one PR ref is the PR, naming the head and saying carried or patch-identical. Two rows whose first sentences of five words or more, the label when there is none, share
a token Jaccard above 0.8 or their first 60 characters are flagged together as `FLAG` lines for the
root; a flag fails nothing. A GitHub, cci, or cc-present read that fails prints a `WARN` line and
fails no row on the checks it would have read. The verdicts are read with `cc-present outcomes`
for `--session`, else the push command's `--session`, else cc-present's default session. A last
line counts rows, failing rows, ungraded rows, and flags. Any failing row exits 1. Otherwise
`--check` exits 0, and the push form runs its command with a copy of the block, `textAt` removed,
as its last argument and exits with the command's status. `--json` prints it all as one object.
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
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import cci
import grades

ROW_ID = re.compile(r"^merge-row-(?P<pr>\d+)(?:-(?P<sha>[0-9a-f]{7,40}))?$")
ROW_HEAD = re.compile(r"\bcommit (?P<sha>[0-9a-f]{7,40})\b")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
WORD = re.compile(r"[a-z0-9]+")
LABEL_NUMBER = re.compile(r"^#\d+:\s*")
SAVED_FINDER = re.compile(r"^(?P<pr>\d+)-(?P<sha>[0-9a-f]{7,40})\.md$")
FINDERS = "finders"
CARRY = re.compile(r"\bcarr(?:y|ies|ied|ying)\b|\bpatch-identical\b", re.IGNORECASE)
PR_SHA = re.compile(r"#(?P<pr>\d+)\s+(?:[\w-]+\s+)?(?P<sha>[0-9a-f]{7,40})\b(?:\s*(?:->|→)\s*(?P<to>[0-9a-f]{7,40})\b)?")
REKEY = re.compile(r"\bre-?keys?\b|\bre-?keyed\b", re.IGNORECASE)
ONE_TAP = re.compile(r"\bone tap\b[^.]*?\bapprov(?:es|e)\b|\bapprov(?:es|e) (?:all|both|every)\b(?: (?:\d+|two|three|four|five|six|seven|eight|nine|ten))?", re.IGNORECASE)
PR_REF = re.compile(r"#(\d+)\b")
LISTED = re.compile(r"^\s*[-*] #(\d+):", re.MULTILINE)
MERGED = "MERGED"
FIELDS = ("For users", "Differs from plan", "Risk", "Finder", "Build (CI)", "Gate")
PR_FIELDS = ("change", "plan", "risk", "review", "blocker")
PR_GROUPS = "pr-groups"
TEXT_AT = "textAt"
PRS_PER_QUERY = 40
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
    """The single side-effect boundary: gh, cci, cc-present, the push command, and blob reads."""

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

    def files(self, directory: Path) -> list[str]:
        return [path.name for path in directory.iterdir()] if directory.is_dir() else []


class ReadFailed(Exception):
    pass


@dataclass
class Row:
    id: str
    block: str
    pr: str
    head: str
    text_at: str
    label: str
    sentence: str
    facts: dict[str, str]
    words: str = ""
    required: tuple[str, ...] = FIELDS
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Pull:
    state: str
    head: str
    committed: str


def triage_items(node: object) -> Iterator[tuple[str, dict]]:
    if isinstance(node, dict):
        if node.get("type") == "triage":
            yield from ((node["id"], item) for item in node.get("items", []))
        for value in node.values():
            yield from triage_items(value)
    elif isinstance(node, list):
        for value in node:
            yield from triage_items(value)


def pr_group_rows(node: object) -> Iterator[tuple[str, dict]]:
    if isinstance(node, dict):
        if node.get("type") == PR_GROUPS:
            yield from ((node["id"], pr) for group in node.get("groups", []) for pr in group.get("prs", []))
        for value in node.values():
            yield from pr_group_rows(value)
    elif isinstance(node, list):
        for value in node:
            yield from pr_group_rows(value)


def first_sentence(md: str, fallback: str) -> str:
    sentences = [part.strip() for part in SENTENCE.split(md.strip())]
    return next((part for part in sentences if len(WORD.findall(part.lower())) >= SENTENCE_WORDS), fallback)


def rows_of(block: dict) -> list[Row]:
    rows = []
    for block_id, item in triage_items(block):
        if not (named := ROW_ID.match(item.get("id", ""))):
            continue
        head = ROW_HEAD.search(item.get("hint", ""))
        facts = {fact["label"]: str(fact.get("value", "")).strip() for fact in item.get("facts", [])}
        label = item.get("label", "")
        words = "\n".join([label, item.get("md", ""), *facts.values()])
        rows.append(Row(item["id"], block_id, named["pr"], head["sha"] if head else "", item.get(TEXT_AT, ""), label, first_sentence(item.get("md", ""), LABEL_NUMBER.sub("", label)), facts, words))
    for block_id, pr in pr_group_rows(block):
        facts = {name: str(pr.get(name, "")).strip() for name in PR_FIELDS}
        words = "\n".join([pr.get("title", ""), pr.get("md", ""), *facts.values()])
        rows.append(Row(str(pr["number"]), block_id, str(pr["number"]), pr.get("head", ""), pr.get(TEXT_AT, ""), pr.get("title", ""), first_sentence(facts["change"], facts["change"]), facts, words, PR_FIELDS))
    return rows


def group_claims(node: object) -> Iterator[tuple[str, str, str, set[str]]]:
    """Each pr-groups group whose title or summary says one tap approves several PRs: its block, its first PR, the words, and every PR it groups or names."""
    if isinstance(node, dict):
        if node.get("type") == PR_GROUPS:
            for group in node.get("groups", []):
                words = f"{group.get('title', '')}\n{group.get('summary', '')}"
                prs = [str(pr["number"]) for pr in group.get("prs", [])]
                if prs and ONE_TAP.search(words):
                    yield node["id"], prs[0], words, set(prs) | set(PR_REF.findall(words))
        for value in node.values():
            yield from group_claims(value)
    elif isinstance(node, list):
        for value in node:
            yield from group_claims(value)


def listed_prs(node: object) -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        if node.get("type") == "markdown":
            yield from ((node["id"], pr) for pr in LISTED.findall(node.get("md", "")))
        for value in node.values():
            yield from listed_prs(value)
    elif isinstance(node, list):
        for value in node:
            yield from listed_prs(value)


def blocker_prs(row: Row) -> set[str]:
    return set(PR_REF.findall(row.facts.get("blocker" if row.required == PR_FIELDS else "Gate", ""))) - {row.pr}


def missing_fields(row: Row) -> list[str]:
    absent = [] if row.required == PR_FIELDS or LABEL_NUMBER.sub("", row.label).strip() else ["label"]
    return absent + [name for name in row.required if not row.facts.get(name)]


def pr_of(row_id: str) -> str | None:
    if row_id.isdigit():
        return row_id
    return named["pr"] if (named := ROW_ID.match(row_id)) else None


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


def root_rulings(shell: Shell, drive: str) -> list[dict]:
    kinds = [f"--kind={kind}" for kind in sorted(cci.ROOT_ANSWER_KINDS)]
    records = [record for record in cci.records(tail(shell), drive, "--lane", "root", *kinds) if record["lane"] == "root" and record["drive"] == drive and record["kind"] in cci.ROOT_ANSWER_KINDS]
    return [record | {"full": ruling_text(shell, record)} for record in records]


def closes(pr: str, text: str) -> bool:
    verb = rf"\b(?:clos(?:e|es|ed|ing)|supersed(?:es|ing))\s+(?:#\d+(?:\s*,\s*|\s*,?\s+and\s+))*#{pr}\b"
    ruled = rf"#{pr}\b(?:\s+\(?[0-9a-f]{{7,40}}\)?)?(?:\s+(?:is|was|now|stays|has\s+been))*\s+(?:superseded|closed|landed)\b"
    return re.search(verb, text, re.IGNORECASE) is not None or re.search(ruled, text, re.IGNORECASE) is not None


def closing_ruling(rulings: list[dict], pr: str, head: str) -> dict | None:
    """The newest root ruling that says `pr` is superseded or closed, unless a later root `go` names it at `head`."""
    ruling = max((record for record in rulings if closes(pr, record["full"])), key=lambda record: record["seq"], default=None)
    if ruling is None:
        return None
    revived = [record for record in grades.gone_at([record for record in rulings if record["kind"] == "go" and record["seq"] > ruling["seq"]], pr, head) if not grades.WITHDRAWN.match(record["text"])]
    return None if revived else ruling


def rekeyed(rulings: list[dict], row: Row) -> dict | None:
    """The newest root record that re-keys a row and names `row`'s id, or its PR with its head or id's short sha."""
    named = ROW_ID.match(row.id)
    keyed = (named["sha"] or "") if named else ""
    shas = [sha for sha in (row.head, keyed) if sha]

    def names(text: str) -> bool:
        if named and row.id in text:
            return True
        if not re.search(rf"#{row.pr}\b|\bmerge-row-{row.pr}\b", text):
            return False
        return any(grades.names_head(named, sha) for named in grades.SHORT_SHA.findall(text) for sha in shas)

    return max((record for record in rulings if REKEY.search(record["full"]) and names(record["full"])), key=lambda record: record["seq"], default=None)


def outcomes_args(session: str | None, command: list[str]) -> list[str]:
    if session is None and "--session" in command[:-1]:
        session = command[command.index("--session") + 1]
    return ["cc-present", "outcomes", "--no-doc", *(["--session", session] if session else [])]


def read_verdicts(shell: Shell, argv: list[str]) -> dict[str, dict[str, dict]]:
    """Each triage or pr-groups block's recorded verdicts, a row id or PR number to its entry of verdict, head and time, from the board's outcomes."""
    done = shell.run(argv)
    start = done.out.find("{")
    if done.code != 0 or start < 0:
        raise ReadFailed(f"cc-present outcomes exited {done.code}: {(done.err or done.out).strip()[:200]}")
    try:
        triage = json.loads(done.out[start:])["interactions"].get("triage") or {}
    except (ValueError, KeyError) as error:
        raise ReadFailed(f"cc-present outcomes printed no interactions: {error}") from error
    return {block: {row: entry for row, entry in rows.items() if entry.get("verdict")} for block, rows in triage.items()}


def verdict_reasons(rows: list[Row], verdicts: dict[str, dict[str, dict]], rulings: list[dict] | None) -> dict[str, list[str]]:
    """Why each row would hide a recorded verdict: its id decided under another block, or its PR decided under an id this push drops, with no root re-key."""
    pushed = {row.id for row in rows}
    decided = [(block, row_id) for block, by_row in verdicts.items() for row_id in by_row]
    reasons: dict[str, list[str]] = {}
    for row in rows:
        if row.id in verdicts.get(row.block, {}):
            continue
        if elsewhere := sorted(block for block, row_id in decided if row_id == row.id):
            reasons.setdefault(row.id, []).append(f"moved: decided under block {', '.join(elsewhere)}, pushed under {row.block}")
            continue
        dropped = sorted({row_id for _, row_id in decided if row_id != row.id and row_id not in pushed and pr_of(row_id) == row.pr})
        if dropped and rulings is not None and not rekeyed(rulings, row):
            reasons.setdefault(row.id, []).append(f"renamed: decided as {', '.join(dropped)}, and no root re-key record names {row.id}")
    return reasons


def at_head(entry: dict | None, verdict: str, head: str) -> bool:
    return entry is not None and entry.get("verdict") == verdict and (not entry.get("head") or grades.names_head(entry["head"], head))


def standing_gos(rulings: list[dict], withdrawn: set[int], pr: str, head: str) -> list[dict]:
    return grades.gone_at([record for record in rulings if record["kind"] == "go" and record["seq"] not in withdrawn], pr, head)


def tap_reasons(row: Row, entry: dict | None, gos: list[dict]) -> list[str]:
    if at_head(entry, "rejected", row.head):
        return [f"rejected: he rejected it at {row.head[:7]}{' at ' + entry['at'] if entry and entry.get('at') else ''}; it returns only at a new head"]
    if gos and not at_head(entry, "approved", row.head):
        return [f"approved: root go cci #{gos[-1]['seq']} names it at {row.head[:7]} and the board holds no approval there; import it with cc-present verdict import or drop the row"]
    return []


def newest_by_pr(verdicts: dict[str, dict[str, dict]]) -> dict[str, dict]:
    newest: dict[str, dict] = {}
    for by_row in verdicts.values():
        for row_id, entry in by_row.items():
            if (pr := pr_of(row_id)) and entry.get("at", "") >= newest.get(pr, {}).get("at", ""):
                newest[pr] = entry
    return newest


def claim_reason(members: set[str], newest: dict[str, dict], pulls: dict[str, Pull | None]) -> str | None:
    rejected = sorted((pr for pr in members if newest.get(pr, {}).get("verdict") == "rejected"), key=int)
    closed = sorted((pr for pr in members if pr in pulls and (pulls[pr] is None or pulls[pr].state != OPEN)), key=int)
    if not rejected and not closed:
        return None
    parts = ([f"{' '.join(f'#{pr}' for pr in rejected)} rejected on the board"] if rejected else []) + ([f"{' '.join(f'#{pr}' for pr in closed)} no longer open"] if closed else [])
    return f"one tap: its words say one tap approves {len(members)} PRs, but {' and '.join(parts)}"


def sentence_at(text: str, at: int) -> str:
    start = max((cut.end() for cut in SENTENCE.finditer(text) if cut.end() <= at), default=0)
    end = next((cut.start() for cut in SENTENCE.finditer(text) if cut.start() >= at), len(text))
    return text[start:end]


def carries(record: dict, repo: str, pr: str, head: str) -> bool:
    """Whether `record` says `pr`'s grade and finder carry to `head`: carried or patch-identical in the sentence or the stretch that ties `#<pr>` to `head`, or a record about only `pr` that names `head`."""
    text = record["text"]
    if not CARRY.search(text):
        return False
    pairs = list(PR_SHA.finditer(text))
    for at, pair in enumerate(pairs):
        if pair["pr"] != pr or not grades.names_head(pair["to"] or pair["sha"], head):
            continue
        stretch = text[pair.start() : pairs[at + 1].start() if at + 1 < len(pairs) else len(text)]
        if CARRY.search(stretch) or CARRY.search(sentence_at(text, pair.start())):
            return True
    if pairs:
        return False
    return [named for named, _ in grades.repo_prs(record, repo)] == [pr] and any(grades.names_head(sha, head) for sha in grades.SHORT_SHA.findall(text))


def graded_warnings(reviews: list[dict], carried: list[dict], saved: list[str], drive: str, lanes: list[str], repo: str, row: Row) -> list[str]:
    if any(record["drive"] == drive and carries(record, repo, row.pr, row.head) for record in carried):
        return []
    warnings = []
    if not grades.standing(grades.fidelity_grades_in(reviews, drive, lanes, repo, row.pr), lanes, row.head):
        warnings.append(f"ungraded: no fidelity grade from {' or '.join(lanes)} names {row.head}")
    found = [record for record in grades.finder_reviews_in(reviews, drive, repo, row.pr) if record["head"]]
    replies = [name for name in saved if (named := SAVED_FINDER.match(name)) and named["pr"] == row.pr and grades.names_head(named["sha"], row.head)]
    if not (grades.newest_finder(found, row.head) or replies):
        warnings.append(f"ungraded: no finder review names {row.head}")
    return warnings


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


def check(shell: Shell, config: dict, block: dict, outcomes: list[str], finders: Path) -> tuple[list[Row], list[dict], list[str]]:
    drive, landing = config["drive"], config["landing"]
    repo, configured = landing["repo"], landing["fidelity_lane"]
    lanes = [configured] if isinstance(configured, str) else list(configured)
    rows = rows_of(block)
    warnings: list[str] = []
    for row in rows:
        if missing := missing_fields(row):
            row.reasons.append(f"missing: {', '.join(missing)}")
        if not row.head:
            row.reasons.append("stale: the row names no head" if row.required == PR_FIELDS else "stale: the hint names no `commit <sha>`")
    named = [row for row in rows if row.head]
    claims = [(row.block, row.pr, row.words, {row.pr} | set(PR_REF.findall(row.words))) for row in rows if ONE_TAP.search(row.words)] + list(group_claims(block))
    blockers = {row.id: blocker_prs(row) for row in rows}
    listed = sorted(set(listed_prs(block)))
    asked = {row.pr for row in named} | {pr for prs in blockers.values() for pr in prs} | {pr for *_, members in claims for pr in members} | {pr for _, pr in listed}
    pulls = read_pulls(shell, repo, sorted(asked, key=int), warnings)
    try:
        rulings = root_rulings(shell, drive)
        withdrawn = {seq for record in cci.records(tail(shell), drive, "--lane", "root", "--kind=withdraw") if record["lane"] == "root" for seq in grades.closes(record)}
    except ReadFailed as failed:
        rulings, withdrawn = None, set()
        warnings.append(f"WARN the root rulings did not read ({failed}); no row is checked for a closing ruling or a root go at its head, and no renamed row is excused by a re-key")
    try:
        verdicts = read_verdicts(shell, outcomes)
    except ReadFailed as failed:
        verdicts = None
        warnings.append(f"WARN the board's verdicts did not read ({failed}); no row is checked for a moved, renamed, rejected or already approved verdict, or a one-tap claim over a rejected PR")
    moved = verdict_reasons(rows, verdicts, rulings) if verdicts is not None else {}
    try:
        reviews = cci.records(tail(shell), drive, "--kind=review", "--kind=head")
    except ReadFailed as failed:
        reviews = None
        warnings.append(f"WARN the grades did not read ({failed}); no row is checked for a grade or finder at its head")
    saved = shell.files(finders)
    for row in rows:
        row.reasons += moved.get(row.id, [])
    for row in named:
        if row.pr in pulls:
            row.reasons += github_reasons(row, pulls[row.pr])
        if rulings is not None and (ruling := closing_ruling(rulings, row.pr, row.head)):
            row.reasons.append(f"not open: root ruled it superseded or closed on cci #{ruling['seq']}")
        if verdicts is not None:
            row.reasons += tap_reasons(row, verdicts.get(row.block, {}).get(row.id), standing_gos(rulings, withdrawn, row.pr, row.head) if rulings is not None else [])
    for row in rows:
        if landed := sorted((pr for pr in blockers[row.id] if (pull := pulls.get(pr)) and pull.state == MERGED), key=int):
            row.reasons.append(f"landed: its blocker names {' '.join(f'#{pr}' for pr in landed)}, merged on GitHub")
    if verdicts is not None:
        newest = newest_by_pr(verdicts)
        for block_id, anchor, _, members in claims:
            if (reason := claim_reason(members, newest, pulls)) and (owner := next((row for row in rows if row.block == block_id and row.pr == anchor), None)):
                owner.reasons.append(reason)
        if reviews is not None:
            row.warnings += graded_warnings(reviews, reviews + (rulings or []), saved, drive, lanes, repo, row)
    found = flags(rows)
    for block_id, pr in listed:
        if pr in pulls and (pull := pulls[pr]) is not None and pull.state != OPEN:
            rows.append(Row(f"{block_id}#{pr}", block_id, pr, pull.head, "", "", "", {}, reasons=[f"listed: the PR is {pull.state.lower()} on GitHub and still listed under {block_id}"]))
    return rows, found, warnings


def stripped(block: object) -> object:
    if isinstance(block, dict):
        return {key: stripped(value) for key, value in block.items() if key != TEXT_AT}
    if isinstance(block, list):
        return [stripped(value) for value in block]
    return block


def counts(rows: list[Row], found: list[dict]) -> dict[str, int]:
    return {"rows": len(rows), "failing": sum(bool(row.reasons) for row in rows), "ungraded": sum(bool(row.warnings) for row in rows), "flags": len(found)}


def report(rows: list[Row], found: list[dict], warnings: list[str], as_json: bool) -> None:
    tally = counts(rows, found)
    if as_json:
        print(json.dumps({"rows": [asdict(row) | {"ok": not row.reasons} for row in rows], "flags": found, "warnings": warnings, "counts": tally}))
        return
    for row in rows:
        if row.reasons:
            print(f"{row.id} {row.pr} {row.head or '-'} {'; '.join(row.reasons)}")
    for row in rows:
        if row.warnings:
            print(f"WARN {row.id} {row.pr} {row.head} {'; '.join(row.warnings)}")
    for line in warnings:
        print(line)
    for flag in found:
        print(f"FLAG {' '.join(flag['rows'])} {' '.join(f'#{pr}' for pr in flag['prs'])} first sentences match (jaccard {flag['jaccard']}): {flag['sentences'][0]}")
    print(" ".join(f"{name} {count}" for name, count in tally.items()))


def split(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" not in argv:
        return argv, []
    at = argv.index("--")
    return argv[:at], argv[at + 1 :]


def main(argv: list[str], shell: Shell | None = None) -> int:
    shell = shell or Shell()
    ours, command = split(argv)
    parser = argparse.ArgumentParser(prog="board_rows.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--session")
    parser.add_argument("--finders", type=Path)
    parser.add_argument("block", type=Path)
    args = parser.parse_args(ours)
    if args.check == bool(command):
        parser.error("pass --check, or a push command after --, not both")
    block = json.loads(args.block.read_text())
    config = args.config.expanduser()
    rows, found, warnings = check(shell, json.loads(config.read_text()), block, outcomes_args(args.session, command), (args.finders or config.parent / FINDERS).expanduser())
    report(rows, found, warnings, args.json)
    if any(row.reasons for row in rows):
        return 1
    if args.check:
        return 0
    with tempfile.NamedTemporaryFile("w", prefix=f"{args.block.stem}.", suffix=".json") as copy:
        json.dump(stripped(block), copy, indent=1)
        copy.flush()
        return shell.push([*command, copy.name])


if __name__ == "__main__":
    import drive

    drive.run_installed(__file__)
    sys.exit(main(sys.argv[1:]))
