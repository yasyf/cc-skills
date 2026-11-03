#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""What desk-runner.py still reads about a finder pass: its job's file names and the head records it queues from. The pass itself is `lr grade finder`, which this script execs."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from long_running.grades import FINDER_DESK, head_entries, names_head, repo_prs

LANE = FINDER_DESK
JOB = "job.json"
LOG = "job.log"
PID = "pid"
POSTS = "posts.jsonl"
CANCELLED = "cancelled"
WHOLE_ASKED = "finder: whole"
REGRADE_ORDER = re.compile(rf"\b{re.escape(LANE)}:\s*re-?grade\b([^;.]*)", re.IGNORECASE)
WHOLE_READ = "over whole "
HEAD_SHA = re.compile(r"(?<!was )\b[0-9a-f]{40}\b")
SHORT_SHA = re.compile(r"(?<!was )\b(?=[0-9]*[a-f])[0-9a-f]{7,39}\b")
FULL_SHA = re.compile(r"\b[0-9a-f]{40}\b")
PR_HEAD = re.compile(r"#(\d+):? (?:at )?((?=[0-9]*[a-f])[0-9a-f]{7,40})\b")
TEXT_PR = re.compile(r"\b([\w.-]+/[\w.-]+)#(\d+)\b|(?<![\w/])#(\d{4,})\b")
WAS = re.compile(r"\(was ([0-9a-f]{7,40})\b")
RULINGS_HEADING = re.compile(r"^#{1,6}\s*Rulings\b", re.IGNORECASE | re.MULTILINE)


def heads(record: dict, repo: str) -> list[tuple[str, str]]:
    """Each `(pr, head)` a `head` record names: each `head:<pr>` attribute of a multi-head record in `repo`, else its `commit` field, else the sha its text pairs with each PR as `#<pr> <sha>`, as a restack posted in prose names one head per PR (root #304381), else the first full sha of its text that no `was` precedes, else the first abbreviated one, which the caller spells whole from the PR's live head (#220098), for each PR its refs name in `repo`, or with no PR refs the PR its text names, as a lane that posts a head without `--pr` still gets its finder pass (root #158365)."""
    if (entries := head_entries(record, repo)) is not None:
        return [(pr, entry["commit"]) for pr, entry in entries.items()]
    prs = [pr for pr, _ in repo_prs(record, repo)] if (record.get("refs") or {}).get("prs") else text_pr(record["text"], repo)
    if commit := (record.get("fields") or {}).get("commit"):
        return [(pr, commit) for pr in prs]
    paired = dict(PR_HEAD.findall(record["text"]))
    if named := [(pr, paired[pr]) for pr in prs if pr in paired]:
        return named
    head = next(iter(HEAD_SHA.findall(record["text"])), "") or next(iter(SHORT_SHA.findall(record["text"])), "")
    return [(pr, head) for pr in prs] if head else []


def head_gap(record: dict, repo: str) -> str:
    """Why a `head` record names no head a grader can queue: it names a PR of `repo` and no commit sha, or no pull request at all; empty when it names a head, or only PRs of other repos."""
    if heads(record, repo) or head_entries(record, repo) is not None:
        return ""
    refs = (record.get("refs") or {}).get("prs")
    if prs := [pr for pr, _ in repo_prs(record, repo)] if refs else text_pr(record["text"], repo):
        return f"names #{prs[0]} but no commit sha"
    return "" if refs or TEXT_PR.search(record["text"]) else "names no pull request"


def text_pr(text: str, repo: str) -> list[str]:
    """The first `repo#N` the text names, else its first bare `#N` of four or more digits, as a bare `#5` is an audit or step number (#170072)."""
    return text_prs(text, repo)[:1]


def text_prs(text: str, repo: str) -> list[str]:
    """Every `repo#N` the text names, else every bare `#N` of four or more digits, in order and once each."""
    named = TEXT_PR.findall(text)
    return list(dict.fromkeys([number for owner, number, _ in named if owner.lower() == repo.lower()] or [bare for _, _, bare in named if bare]))


def requested(record: dict, repo: str) -> list[str]:
    """The PRs an `ask` to the finder desk asks a whole pass over by saying `finder: whole`, every one it names, and those a root `decide` or `defect` orders regraded as `finder-desk: regrade #N ...` up to the clause's end (root #289998); any other `decide` asks none, since root settles findings in one (root #182759)."""
    if LANE not in (record.get("to") or []):
        return []
    if record["kind"] in ("decide", "defect"):
        return list(dict.fromkeys(pr for order in REGRADE_ORDER.findall(record["text"]) for pr in text_prs(order, repo))) if record.get("lane") == "root" else []
    if record["kind"] != "ask" or WHOLE_ASKED not in record["text"].casefold():
        return []
    return [pr for pr, _ in repo_prs(record, repo)] if (record.get("refs") or {}).get("prs") else text_prs(record["text"], repo)


def since(reviews: list[dict], records: list[dict], head: str, repo: str) -> tuple[str, dict | None]:
    """The full head the PR's newest finder record at another head read, empty when no `head` record spells it out, and that record."""
    prior = max((review for review in reviews if not names_head(review["head"], head)), key=lambda review: review["seq"], default=None)
    if prior is None:
        return "", None
    spelled = [prior["head"], *(sha for record in records if record["kind"] == "head" for sha in [(record.get("fields") or {}).get("commit", ""), *(entry[key] for entry in (head_entries(record, repo) or {}).values() for key in ("commit", "was")), *FULL_SHA.findall(record["text"])])]
    full = next((sha for sha in spelled if FULL_SHA.fullmatch(sha) and sha.startswith(prior["head"])), "")
    return full, {"seq": prior["seq"], "lane": prior["lane"], "head": prior["head"], "text": prior["text"]}


def previous(record: dict | None, repo: str, pr: str) -> str:
    """The head a `head` record says `pr`'s head replaced: its `head:<pr>` attribute's `was`, else its text's `(was <sha>)`, empty when it names none."""
    if record is None:
        return ""
    if (entries := head_entries(record, repo)) is not None:
        return entries.get(pr, {}).get("was", "")
    return found[1] if (found := WAS.search(record["text"])) else ""


if __name__ == "__main__":
    lr = Path(__file__).resolve().parents[3] / "bin" / "lr"
    os.execv(lr, [str(lr), "grade", "finder", *sys.argv[1:]])
