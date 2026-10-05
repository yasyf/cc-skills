# /// script
# requires-python = ">=3.13"
# ///
"""Tag and release every push to the default branch of a binary-pinned repo.

A tag pushed with the workflow's GITHUB_TOKEN starts no workflow, so the
release workflow is dispatched on the new tag instead.

Env contract:
  RELEASE_WORKFLOW   release workflow file the new tag is dispatched to
  RELEASE_DRY_RUN    "true"/"false" — plan only, never tag or dispatch
  GITHUB_ACTIONS     "true" in CI; tagging needs this or RELEASE_FORCE
  RELEASE_FORCE      "1" to allow tagging outside CI (deliberate local use)
  GITHUB_STEP_SUMMARY  optional path to append the verdict
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
TAG_PATTERN = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
RELEASE_SUBJECT = re.compile(r"^chore\((release|plugins)\)")


@dataclass(frozen=True)
class Plan:
    tag: str | None
    reason: str


def _git_out(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def version_tags(repo: Path) -> list[tuple[int, int, int]]:
    versions = []
    for name in _git_out(repo, "tag", "--list", "v*").split():
        if m := TAG_PATTERN.fullmatch(name):
            versions.append((int(m[1]), int(m[2]), int(m[3])))
    return sorted(versions)


def released_subjects(repo: Path, latest: str) -> list[str]:
    subjects = _git_out(repo, "log", "--first-parent", "--format=%s", f"{latest}..HEAD").splitlines()
    return [s for s in subjects if not RELEASE_SUBJECT.match(s)]


def next_version(current: tuple[int, int, int], subjects: list[str]) -> tuple[int, int, int]:
    major, minor, patch = current
    if any("💥" in s for s in subjects):
        return (major + 1, 0, 0)
    if any("✨" in s for s in subjects):
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def plan(repo: Path) -> Plan:
    if tagged := _git_out(repo, "tag", "--points-at", "HEAD", "--list", "v*"):
        return Plan(None, f"HEAD is already released as {tagged.split()[0]}")
    versions = version_tags(repo)
    if not versions:
        return Plan(None, "no v* tag to version from; push the first release tag by hand")
    latest = "v%d.%d.%d" % versions[-1]
    subjects = released_subjects(repo, latest)
    if not subjects:
        return Plan(None, f"nothing landed since {latest} beyond release bookkeeping")
    return Plan("v%d.%d.%d" % next_version(versions[-1], subjects), f"{len(subjects)} change(s) since {latest}")


def tag_and_dispatch(repo: Path, tag: str, workflow: str) -> None:
    env = {
        **os.environ,
        "GIT_COMMITTER_NAME": BOT_NAME,
        "GIT_COMMITTER_EMAIL": BOT_EMAIL,
    }
    subprocess.run(["git", "tag", "-a", tag, "-m", tag], cwd=repo, env=env, check=True)
    subprocess.run(["git", "push", "origin", f"refs/tags/{tag}"], cwd=repo, check=True)
    subprocess.run(["gh", "workflow", "run", workflow, "--ref", tag], cwd=repo, check=True)


def main() -> int:
    repo = Path.cwd()
    workflow = os.environ["RELEASE_WORKFLOW"]
    dry_run = os.environ.get("RELEASE_DRY_RUN", "false").strip().lower() == "true"
    in_ci = os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true"
    forced = os.environ.get("RELEASE_FORCE", "").strip() == "1"

    p = plan(repo)
    if p.tag is None:
        line = f"release: not tagging — {p.reason}"
    elif dry_run or not (in_ci or forced):
        line = f"release: would tag {p.tag} and dispatch {workflow} ({p.reason})"
    else:
        tag_and_dispatch(repo, p.tag, workflow)
        line = f"release: tagged {p.tag} and dispatched {workflow} ({p.reason})"
    print(line)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
