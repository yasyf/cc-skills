"""Answer what code a lane holds: every worktree it owns, and each one's branch, dirty files, commits ahead of trunk, last commit, push state, and PRs.

A lane's worktrees come from four places, deduplicated by path: its Orca launch receipt
``<receipts>/<lane>.worktree``; its own cci records, a ``worktree`` attr or an absolute
worktree path in the text of a ``WORKTREE``, ``FIRST-REPORT``, ``started``, or any other record;
``git worktree list`` in the drive's checkout, a worktree whose directory or branch is the lane's
name; and the head branch of each PR the lane's records name. Only paths that exist count.
Every call goes through ``run``, which returns stdout and raises CalledProcessError on a
nonzero exit. STDLIB ONLY.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import cci

Run = Callable[[list[str]], str]
WORKTREE_PATH = re.compile(r"(?<![\w/])(/[\w.~/-]*/(?:worktrees|workspaces)/[\w.-]+(?:/[\w.-]+)*)")
DIRTY_NAMED = 5
PULLS_SHOWN = 3
SHORT = 10
PR_REF = re.compile(r"(?:^|#)(\d+)$")
CHECK_FAILED = frozenset({"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"})
CHECK_PASSED = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
ROLE = re.compile(r"^ccx:(?:.*[ \t])?role=([\w-]+)", re.MULTILINE)
TITLE_ROLES = frozenset({"evidence", "proof", "closeout", "audit"})
NOT_FIX_ROLES = frozenset({"evidence", "proof", "closeout", "desk", "audit"})


@dataclass
class Worktree:
    path: Path
    sources: list[str] = field(default_factory=list)
    branch: str = ""
    dirty: list[str] = field(default_factory=list)
    ahead: int | None = None
    commit: str = ""
    commit_minutes: int | None = None
    pushed: str = "not pushed"
    pr: str = ""
    error: str = ""

    def line(self) -> str:
        if self.error:
            return f"- {self.path} ({', '.join(self.sources)}): {self.error}"
        more = f" and {len(self.dirty) - DIRTY_NAMED} more" if len(self.dirty) > DIRTY_NAMED else ""
        dirty = f"{len(self.dirty)} dirty file{'' if len(self.dirty) == 1 else 's'}" + (f": {', '.join(self.dirty[:DIRTY_NAMED])}{more}" if self.dirty else "")
        ahead = "ahead unknown" if self.ahead is None else f"{self.ahead} ahead of trunk"
        commit = f"last commit {self.commit_minutes} min ago {self.commit}" if self.commit_minutes is not None else "no commit"
        return f"- {self.path} ({', '.join(self.sources)}): {self.branch}, {dirty}, {ahead}, {commit}, {self.pushed}, {self.pr or 'no PR'}"


def output(run: Run, argv: list[str]) -> str | None:
    try:
        return run(argv)
    except subprocess.CalledProcessError:
        return None


def record_paths(records: Iterable[dict]) -> list[str]:
    paths: list[str] = []
    for record in records:
        if attr := ((record.get("fields") or {}).get("attrs") or {}).get("worktree"):
            paths.append(attr)
        paths += WORKTREE_PATH.findall(record.get("text") or "")
    return paths


def record_prs(records: Iterable[dict]) -> list[str]:
    return list(dict.fromkeys(match[1] for record in records for pr in (record.get("refs") or {}).get("prs") or [] if (match := PR_REF.search(str(pr)))))


def listed_worktrees(run: Run, checkout: Path) -> dict[str, str]:
    """Each worktree of the checkout's repository mapped to its branch, empty when detached."""
    listing = output(run, ["git", "-C", str(checkout), "worktree", "list", "--porcelain"]) or ""
    found: dict[str, str] = {}
    for block in listing.strip().split("\n\n"):
        fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
        if "worktree" in fields:
            found[fields["worktree"]] = fields.get("branch", "").removeprefix("refs/heads/")
    return found


def lane_worktrees(lane: str, records: list[dict], receipts: Iterable[Path], checkout: Path, repo: str, run: Run) -> list[Worktree]:
    """Every existing worktree the lane owns, each naming the sources that found it."""
    found: dict[str, Worktree] = {}

    def add(path: str, source: str) -> None:
        resolved = str(Path(path).expanduser())
        if Path(resolved).is_dir():
            found.setdefault(resolved, Worktree(Path(resolved))).sources.append(source)

    for receipt in receipts:
        if (file := receipt / f"{lane}.worktree").is_file():
            add(file.read_text().strip(), "launch receipt")
    for path in record_paths(records):
        add(path, "cci record")
    listed = listed_worktrees(run, checkout)
    for path, branch in listed.items():
        if Path(path).name == lane or branch == lane or branch.endswith(f"/{lane}"):
            add(path, "git worktree list")
    for pr in record_prs(records) if repo else []:
        head = json.loads(output(run, ["gh", "pr", "view", pr, "--repo", repo, "--json", "headRefName"]) or "{}").get("headRefName")
        for path, branch in listed.items():
            if head and branch == head:
                add(path, f"PR #{pr} head")
    for worktree in found.values():
        worktree.sources = list(dict.fromkeys(worktree.sources))
    return list(found.values())


def ci_state(rollup: list[dict]) -> str:
    states = [check.get("conclusion") or check.get("state") or check.get("status") or "" for check in rollup]
    if any(state in CHECK_FAILED for state in states):
        return "CI red"
    if all(state in CHECK_PASSED for state in states):
        return "CI green"
    return "CI pending"


def inspect(worktree: Worktree, trunk: str, repo: str, now: datetime, run: Run) -> Worktree:
    """Fill in the worktree's branch, dirty files, commits ahead of `origin/<trunk>`, last commit, whether the origin branch holds HEAD, and the branch's newest PRs with an open one's CI state."""
    path = str(worktree.path)
    status = output(run, ["git", "-C", path, "status", "--short", "--branch"])
    if status is None:
        worktree.error = "git status failed"
        return worktree
    head, *dirty = status.splitlines() or [""]
    worktree.branch = head.removeprefix("## ")
    worktree.dirty = [line[3:] for line in dirty]
    ahead = output(run, ["git", "-C", path, "rev-list", "--count", f"origin/{trunk}..HEAD"])
    worktree.ahead = int(ahead) if ahead and ahead.strip().isdigit() else None
    last = (output(run, ["git", "-C", path, "log", "-1", "--format=%ct %h %s"]) or "").strip()
    stamp, _, subject = last.partition(" ")
    if stamp.isdigit():
        worktree.commit, worktree.commit_minutes = subject, int((now - datetime.fromtimestamp(int(stamp), timezone.utc)).total_seconds() // 60)
    branch = head.removeprefix("## ").split("...")[0]
    if branch and not branch.startswith("HEAD "):
        remote = (output(run, ["git", "-C", path, "ls-remote", "origin", f"refs/heads/{branch}"]) or "").split()
        local = (output(run, ["git", "-C", path, "rev-parse", "HEAD"]) or "").strip()
        worktree.pushed = "no origin branch" if not remote else "pushed" if remote[0] == local else f"HEAD not pushed, origin at {remote[0][:SHORT]}"
    if branch and repo and not branch.startswith("HEAD "):
        pulls = json.loads(output(run, ["gh", "pr", "list", "--repo", repo, "--head", branch, "--state", "all", "--limit", str(PULLS_SHOWN), "--json", "number,state,statusCheckRollup"]) or "[]")
        worktree.pr = ", ".join(f"PR #{pull['number']} {pull['state'].lower()}" + (f" {ci_state(pull.get('statusCheckRollup') or [])}" if pull["state"] == "OPEN" else "") for pull in pulls)
    return worktree


def lane_status(lane: str, records: list[dict], receipts: Iterable[Path], checkout: Path, repo: str, trunk: str, now: datetime, run: Run) -> list[Worktree]:
    return [inspect(worktree, trunk, repo, now, run) for worktree in lane_worktrees(lane, records, receipts, checkout, repo, run)]


def summary(lane: str, worktrees: list[Worktree]) -> str:
    """One line for an escalation: each worktree's state, or that none was found."""
    if not worktrees:
        return f"no worktree found for lane {lane}"
    return " | ".join(worktree.line().removeprefix("- ") for worktree in worktrees)


def brief_roles(state: Path, lane: str) -> set[str]:
    """The roles an in-process lane's brief names, from `<state>/briefs/<lane>.md` or `<state>/<lane>/brief.md`: its `ccx:` line's `role=`, and an evidence, proof, closeout, or audit word in its title line."""
    brief = next((path for path in (state / "briefs" / f"{lane}.md", state / lane / "brief.md") if path.is_file()), None)
    if brief is None:
        return set()
    text = brief.read_text()
    title = next((line for line in text.splitlines() if line.startswith("# ")), "")
    return set(ROLE.findall(text)) | (TITLE_ROLES & set(re.findall(r"[a-z]+", title.lower())))


def owes_no_pr(state: Path, lane: str) -> bool:
    """Whether the lane's name or brief makes it an evidence, proof, closeout, desk, or audit lane, which never owes a PR."""
    return bool(NOT_FIX_ROLES & {*lane.split("-"), *brief_roles(state, lane)})


def owed_mechanism(lane: str, records: list[dict]) -> dict | None:
    """The lane's mechanism still owed a PR: its newest MECHANISM or CANDIDATE that no settling record of its own, and no PR, ready, fix-live, or root go from any lane for the same incident, and no root ruling addressed to the lane since, has answered."""
    answered: set[str] = set()
    owed: dict | None = None
    for record in sorted(records, key=lambda record: record["seq"]):
        if answers := cci.answers_mechanism(record):
            answered |= answers
            if owed and answers & owed_keys(owed):
                owed = None
        if record["lane"] != lane:
            continue
        if cci.mechanism_mark(record):
            owed = None if cci.incident_keys(record) & answered else record
        elif cci.settles_mechanism(record):
            owed = None
    return owed


def owed_keys(record: dict) -> set[str]:
    return cci.incident_keys(record) | {f"seq:{record['seq']}", f"lane:{record['lane']}"}
