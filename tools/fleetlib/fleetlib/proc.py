"""Subprocess wrappers: `run` exits loudly on failure, `try_run` reports back."""

import subprocess
import sys


def run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command, exiting the process with a FAIL message on nonzero status."""
    proc = try_run(*args, stdin=stdin)
    if proc.returncode != 0:
        sys.exit(f"FAIL ({' '.join(args[:3])}…): {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def try_run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command where a nonzero exit is an answer, not a failure."""
    return subprocess.run(args, input=stdin, capture_output=True, text=True)
