"""Fidelity grades and finder reviews on a drive's bus, read the one way for desk-runner.py's landing checks and board_rows.py's merge-row gate."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import cci

FIDELITY_OK = "fidelity: ok"
FIDELITY_MISSING = "fidelity: missing"
FIDELITY_GRADES = (FIDELITY_OK, FIDELITY_MISSING)
FINDER_PASS = "ok"
FINDER_FAIL = "major"
FINDER_NAMED = re.compile(r"\bfinder\b", re.IGNORECASE)
FINDER_SAYS = re.compile(r"\bfinder(?:\s*\[\d+\])?:?\s+(ok|lgtm|major|issues)\b", re.IGNORECASE)
FINDER_ISSUES = "issues"
FINDER_LGTM = "lgtm"
FINDER_MAJOR = re.compile(r"\b[1-9]\d* majors?\b", re.IGNORECASE)
FINDER_CLEAN = re.compile(r"\bno (?:majors?|findings)\b|\b0 majors?\b|\bok\b", re.IGNORECASE)
SHORT_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
GROUND_KEY = re.compile(r"(?:plan(?::\d+(?:-\d+)?)?|F\d+|(?!design/)[a-z]+/[\w./-]+)")
GO_PAIR = r"#\d+\s+at\s+[0-9a-f]{7,40}\b"
GO_CLAUSE = re.compile(rf"(?:^\s*|[.:;!?]\s+)(?:go|merge)\s+({GO_PAIR}(?:(?:\s*,\s*|\s*,?\s+and\s+){GO_PAIR})*)", re.IGNORECASE)
GO_HEAD = re.compile(r"#(\d+)\s+at\s+([0-9a-f]{7,40})\b", re.IGNORECASE)
PRECONDITION = re.compile(r"\b(?:lands?|merges?) only after\b|\bbefore it (?:lands|merges)\b|\bdo not (?:land|merge) until\b|\bnot for landing\b", re.IGNORECASE)
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
WITHDRAWN = re.compile(r"^\s*withdrawn\b", re.IGNORECASE)


def names_head(named: str, sha: str) -> bool:
    return bool(sha) and (sha.startswith(named) or named.startswith(sha))


def missing_grounds(text: str) -> str:
    """The grounds a `fidelity: missing` line names: its first word after the head, the plan item or register row key `rulings.py grade` writes there."""
    words = [word for word in text.removeprefix(FIDELITY_MISSING).split() if word != "at" and not SHORT_SHA.fullmatch(word)]
    return words[0].rstrip(".,:;") if words else ""


def owner_grounded(text: str) -> bool:
    """Whether a `fidelity: missing` line rests on plan text or the owner's words: every comma-joined key it names is a plan line or item, or a register row other than a `design/` row, never a bare word or a cci seq."""
    return all(GROUND_KEY.fullmatch(key) for key in missing_grounds(text).split(",")) if missing_grounds(text) else False


def graded_head(text: str) -> str:
    """The head a grade or finder line names: its first short sha, so no register key it lists after the head reads as one."""
    found = SHORT_SHA.search(text)
    return found[0] if found else ""


def finder_grade(text: str) -> str:
    if said := FINDER_SAYS.search(text):
        return {FINDER_ISSUES: FINDER_FAIL, FINDER_LGTM: FINDER_PASS}.get(said[1].lower(), said[1].lower())
    if FINDER_MAJOR.search(text):
        return FINDER_FAIL
    return FINDER_PASS if FINDER_CLEAN.search(text) else ""


def newest_finder(records: list[dict], sha: str) -> dict | None:
    return max((record for record in records if names_head(record["head"], sha)), key=lambda record: record["seq"], default=None)


def newest_naming(grades: list[dict], sha: str) -> dict | None:
    return max((record for record in grades if (head := graded_head(record["text"])) and names_head(head, sha)), key=lambda record: record["seq"], default=None)


def repo_prs(record: dict, repo: str) -> list[tuple[str, bool]]:
    """Each PR `record` names that may be in `repo`, with whether its ref says so: `owner/repo#N` refs to `repo`, and bare numbers, which name no repo."""
    named = []
    for ref in record.get("refs", {}).get("prs", []):
        if isinstance(ref, int):
            named.append((str(ref), False))
        elif (where := str(ref).rpartition("#"))[0].lower() == repo.lower():
            named.append((where[2], True))
    return named


def names_pr(record: dict, pr: str) -> bool:
    return pr in {str(ref).rsplit("#", 1)[-1] for ref in record.get("refs", {}).get("prs", [])} or re.search(rf"#{pr}\b", record["text"]) is not None


def go_heads(record: dict) -> dict[str, str]:
    """The PRs a root `go` lets merge, each with the head it names: only the `#<n> at <sha>` pairs of its first `go` or `merge` clause that opens the text or a sentence or follows a colon, the word then the pairs joined by commas or `and`, so a PR and sha the text mentions outside that clause, a quoted or negated go, and a record opening `withdrawn` are never a go."""
    if WITHDRAWN.match(record["text"]):
        return {}
    clause = GO_CLAUSE.search(record["text"])
    return {pr: sha.lower() for pr, sha in GO_HEAD.findall(clause[1])} if clause else {}


def goes(record: dict, pr: str) -> bool:
    return pr in go_heads(record) and pr not in record.get("voided", ())


def precondition_sentences(text: str) -> list[str]:
    """Each sentence of `text` that holds a landing back on something outside the PR in so many words: `lands only after`, `merge only after`, `before it lands`, `do not land until`, or `not for landing`."""
    return [" ".join(sentence.split()) for sentence in SENTENCE_BREAK.split(text) if PRECONDITION.search(sentence)]


def full_text(record: dict) -> str:
    """The whole text of `record`: the body file cci spilled it to when it ran past cci's text limit, else its text."""
    spilled = Path(record.get("refs", {}).get("path") or "")
    return spilled.read_text() if spilled.is_file() else record["text"]


def gone_at(gos: list[dict], pr: str, head: str) -> list[dict]:
    """The root `go` records of `gos` whose opening clause names `pr` at `head` by a short sha."""
    return [record for record in gos if (named := go_heads(record).get(pr)) and names_head(named, head) and pr not in record.get("voided", ())]


def closes(record: dict) -> set[int]:
    """The seqs `record` closes: each it resolves, and the one a `withdraw` answers by `--re`."""
    resolves = record.get("resolves") or []
    closed = set(resolves) if isinstance(resolves, list) else {resolves}
    return closed | ({record["re"]} if record["kind"] == "withdraw" and record.get("re") else set())


def fidelity_grades(tail: Callable[[list[str]], str], drive: str, lanes: list[str], repo: str, pr: str) -> list[dict]:
    """Every grade a fidelity lane posted about `pr`, read by its `owner/repo#N` reference, which cci also matches to a post that named the bare number."""
    return fidelity_grades_in([record for lane in lanes for record in cci.records(tail, drive, "--kind=review", "--lane", lane, "--pr", f"{repo}#{pr}")], drive, lanes, repo, pr)


def fidelity_grades_in(reviews: list[dict], drive: str, lanes: list[str], repo: str, pr: str) -> list[dict]:
    return [record for record in reviews if record["kind"] == "review" and record["drive"] == drive and record["lane"] in lanes and record["text"].startswith(FIDELITY_GRADES) and pr in dict(repo_prs(record, repo))]


def standing(grades: list[dict], lanes: list[str], sha: str) -> list[dict]:
    """Each fidelity lane's newest grade naming `sha`."""
    return [grade for lane in lanes if (grade := newest_naming([record for record in grades if record["lane"] == lane], sha))]


def about_pr(record: dict, repo: str, pr: str) -> bool:
    """Whether `record` is about `pr`: its refs name it, or it carries no PR ref and its text names `#<pr>` or `[<pr>]`."""
    if record.get("refs", {}).get("prs"):
        return pr in dict(repo_prs(record, repo))
    return re.search(rf"#{pr}\b|\[{pr}\]", record["text"]) is not None


def finder_reviews_in(found: list[dict], drive: str, repo: str, pr: str) -> list[dict]:
    """Every finder review in `found` about `pr`, with the verdict it reads as and the head it names, or the head the newest `head` record about `pr` before it posted."""
    records = [record for record in found if record["drive"] == drive and record["kind"] in ("review", "head") and about_pr(record, repo, pr)]
    heads = [record for record in records if record["kind"] == "head"]
    reviews = []
    for record in records:
        if record["kind"] != "review" or not FINDER_NAMED.search(record["text"]) or record["text"].startswith(FIDELITY_GRADES):
            continue
        posted = max((head for head in heads if head["seq"] < record["seq"]), key=lambda head: head["seq"], default=None)
        reviews.append(record | {"grade": finder_grade(record["text"]), "head": graded_head(record["text"]) or (graded_head(posted["text"]) if posted else "")})
    return reviews


def finder_records(reviews: list[dict]) -> list[dict]:
    return [record for record in reviews if record["grade"] and record["head"]]
