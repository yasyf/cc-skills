"""Resolve author identity from local tooling.

Emits ``KEY=VALUE`` on stdout (always, even when empty) and ``MISSING: KEY`` notes
on stderr. Exit 2 if git is absent; exit 1 only when all three fields are missing.
"""

from __future__ import annotations

import shutil
import sys

from .common import run


def _git_config(key: str) -> str:
    proc = run(["git", "config", "--get", key])
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _gh_login() -> str:
    proc = run(["gh", "api", "user", "-q", ".login"])
    return proc.stdout.strip() if proc.returncode == 0 else ""


def main() -> int:
    if not shutil.which("git"):
        print("ERROR: git is not installed", file=sys.stderr)
        return 2

    author_name = _git_config("user.name")
    author_email = _git_config("user.email")

    github_user = _gh_login() if shutil.which("gh") else ""
    if not github_user:
        github_user = _git_config("github.user")

    print(f"AUTHOR_NAME={author_name}")
    print(f"AUTHOR_EMAIL={author_email}")
    print(f"GITHUB_USER={github_user}")

    missing = 0
    for key, value in (("AUTHOR_NAME", author_name), ("AUTHOR_EMAIL", author_email), ("GITHUB_USER", github_user)):
        if not value:
            print(f"MISSING: {key} — ask the user", file=sys.stderr)
            missing += 1

    return 1 if missing >= 3 else 0
