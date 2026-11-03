#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""What desk-runner.py still reads about a finder pass: its lane and the heads a record names. The pass itself is `lr grade finder`."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from long_running.grades import FINDER_DESK, head_entries, repo_prs

LANE = FINDER_DESK
HEAD_SHA = re.compile(r"(?<!was )\b[0-9a-f]{40}\b")
SHORT_SHA = re.compile(r"(?<!was )\b(?=[0-9]*[a-f])[0-9a-f]{7,39}\b")
FULL_SHA = re.compile(r"\b[0-9a-f]{40}\b")
PR_HEAD = re.compile(r"#(\d+):? (?:at )?((?=[0-9]*[a-f])[0-9a-f]{7,40})\b")
TEXT_PR = re.compile(r"\b([\w.-]+/[\w.-]+)#(\d+)\b|(?<![\w/])#(\d{4,})\b")
RULINGS_HEADING = re.compile(r"^#{1,6}\s*Rulings\b", re.IGNORECASE | re.MULTILINE)


def heads(record: dict, repo: str) -> list[tuple[str, str]]:
    """Each `(pr, head)` a `head` record names: each `head:<pr>` attribute of a multi-head record in `repo`, else its `commit` field, else the sha its text pairs with each PR as `#<pr> <sha>`, as a restack posted in prose names one head per PR (coordinator #304381), else the first full sha of its text that no `was` precedes, else the first abbreviated one, which the caller spells whole from the PR's live head (#220098), for each PR its refs name in `repo`, or with no PR refs the PR its text names, as a lane that posts a head without `--pr` still gets its finder pass (coordinator #158365)."""
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


def text_pr(text: str, repo: str) -> list[str]:
    """The first `repo#N` the text names, else its first bare `#N` of four or more digits, as a bare `#5` is an audit or step number (#170072)."""
    return text_prs(text, repo)[:1]


def text_prs(text: str, repo: str) -> list[str]:
    """Every `repo#N` the text names, else every bare `#N` of four or more digits, in order and once each."""
    named = TEXT_PR.findall(text)
    return list(dict.fromkeys([number for owner, number, _ in named if owner.lower() == repo.lower()] or [bare for _, _, bare in named if bare]))


if __name__ == "__main__":
    lr = Path(__file__).resolve().parents[3] / "bin" / "lr"
    os.execv(lr, [str(lr), "grade", "finder", *sys.argv[1:]])
