#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Write a branch's PR title, PR body, and commit message with a headless Claude Opus 5.5, for a lane on a model that writes no prose.

    pr-prose.py [--base REF] [--brief PATH] [--notes PATH]... [--out DIR]

STDLIB ONLY. Run from the lane's worktree once its change is committed. It runs
`claude -p --model claude-opus-5-5` in that worktree with read-only tools, so the writer
loads the repository's own CLAUDE.md and AGENTS.md and reads the PR body spec they name,
the diff and log against `--base` (default `origin/HEAD`), the lane's brief, and each
notes file of findings the PR must carry. It writes `pr-title.txt`, `pr-body.md`, and
`commit-message.txt` to `--out` (default `<git dir>/pr-prose`) and prints the three
paths; the lane ships them verbatim and never asks another lane or the root for prose.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

MODEL = "claude-opus-5-5"
SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {"title": {"type": "string"}, "body": {"type": "string"}, "commit_message": {"type": "string"}},
        "required": ["title", "body", "commit_message"],
    }
)
PROMPT = """You write the pull request title, the pull request body, and the commit message for the branch checked out here.
Follow this repository's conventions exactly: its CLAUDE.md and AGENTS.md, and the PR body and commit message spec they name;
read that spec in full before writing (in Forge-AI/monorepo it is .agents/skills/submit-pr/references/pr-body.md).
Read the change with `git log {base}..HEAD` and `git diff {base}...HEAD`.
{brief}{notes}
The body carries a `## Regression tests` section that lists each test id the change adds or edits
and links one CI run where that test failed against the old tree, and its Risk profile states Load and Data in prose.
State only what the diff, the brief, and the notes support. Return the title, the whole markdown body, and the whole commit message,
subject line first, as the structured output.
"""


def git(*argv: str) -> str:
    return subprocess.run(["git", *argv], capture_output=True, text=True, check=True).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pr-prose.py", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--base", default="origin/HEAD")
    parser.add_argument("--brief", type=Path, help="the lane's brief")
    parser.add_argument("--notes", type=Path, action="append", default=[], help="a file of findings, evidence, or design the PR must carry")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    base = git("rev-parse", "--abbrev-ref", args.base) if args.base == "origin/HEAD" else args.base
    out = args.out or Path(git("rev-parse", "--absolute-git-dir")) / "pr-prose"
    brief = f"The lane's brief, which says what the change is for, is {args.brief.resolve()}.\n" if args.brief else ""
    notes = "".join(f"Read {path.resolve()} for findings the PR must carry.\n" for path in args.notes)
    done = subprocess.run(
        ["claude", "-p", "--model", MODEL, "--effort", "high", "--no-session-persistence", "--strict-mcp-config", "--tools", "Read,Grep,Glob,Bash", "--allowedTools", "Read", "Grep", "Glob", "Bash(git log:*)", "Bash(git diff:*)", "Bash(git show:*)", "--output-format", "json", "--json-schema", SCHEMA],
        input=PROMPT.format(base=base, brief=brief, notes=notes),
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        sys.exit(f"claude -p exited {done.returncode}: {(done.stderr or done.stdout).strip()[-500:]}")
    payload = json.loads(done.stdout)
    prose = (payload[-1] if isinstance(payload, list) else payload)["structured_output"]
    out.mkdir(parents=True, exist_ok=True)
    paths = {"title": out / "pr-title.txt", "body": out / "pr-body.md", "commit_message": out / "commit-message.txt"}
    for field, path in paths.items():
        path.write_text(prose[field].strip() + "\n")
        print(path)
    return 0


if __name__ == "__main__":
    from long_running import drive

    drive.run_installed(__file__)
    sys.exit(main())
