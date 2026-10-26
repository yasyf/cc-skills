"""Fidelity grades and finder reviews on a drive's bus, read the one way for desk-runner.py's landing checks and board_rows.py's merge-row gate."""

from __future__ import annotations

import re
from collections.abc import Callable

import cci

FIDELITY_OK = "fidelity: ok"
FIDELITY_MISSING = "fidelity: missing"
FIDELITY_GRADES = (FIDELITY_OK, FIDELITY_MISSING)
FINDER_PASS = "ok"
FINDER_FAIL = "major"
FINDER_NAMED = re.compile(r"\bfinder\b", re.IGNORECASE)
FINDER_SAYS = re.compile(r"\bfinder: (ok|major)\b", re.IGNORECASE)
FINDER_MAJOR = re.compile(r"\b[1-9]\d* majors?\b", re.IGNORECASE)
FINDER_CLEAN = re.compile(r"\bno (?:majors?|findings)\b|\b0 majors?\b|\bok\b", re.IGNORECASE)
SHORT_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")


def names_head(named: str, sha: str) -> bool:
    return bool(sha) and (sha.startswith(named) or named.startswith(sha))


def graded_head(text: str) -> str:
    """The head a grade or finder line names: its first short sha, so no register key it lists after the head reads as one."""
    found = SHORT_SHA.search(text)
    return found[0] if found else ""


def finder_grade(text: str) -> str:
    if said := FINDER_SAYS.search(text):
        return said[1].lower()
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


def gone_at(gos: list[dict], pr: str, head: str) -> list[dict]:
    """The root `go` records of `gos` that name `pr` and its `head` by a short sha."""
    return [record for record in gos if names_pr(record, pr) and any(names_head(named, head) for named in SHORT_SHA.findall(record["text"]))]


def fidelity_grades(tail: Callable[[list[str]], str], drive: str, lanes: list[str], repo: str, pr: str) -> list[dict]:
    """Every grade a fidelity lane posted about `pr`, read by its `owner/repo#N` reference, which cci also matches to a post that named the bare number."""
    reviews = [record for lane in lanes for record in cci.records(tail, drive, "--kind=review", "--lane", lane, "--pr", f"{repo}#{pr}")]
    return [record for record in reviews if record["kind"] == "review" and record["drive"] == drive and record["lane"] in lanes and record["text"].startswith(FIDELITY_GRADES) and pr in dict(repo_prs(record, repo))]


def standing(grades: list[dict], lanes: list[str], sha: str) -> list[dict]:
    """Each fidelity lane's newest grade naming `sha`."""
    return [grade for lane in lanes if (grade := newest_naming([record for record in grades if record["lane"] == lane], sha))]


def finder_reviews(tail: Callable[[list[str]], str], drive: str, repo: str, pr: str) -> list[dict]:
    """Every finder review about `pr`, with the verdict it reads as and the head it names, or the head the newest `head` record before it posted."""
    records = [record for record in cci.records(tail, drive, "--kind=review", "--kind=head", "--pr", f"{repo}#{pr}") if record["drive"] == drive and pr in dict(repo_prs(record, repo))]
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
