"""bounded.py <seconds> -- <command> [<arg>...]: GNU timeout for poll loops.

Runs the command for at most <seconds>, then ends its whole tree. Unlike `timeout`, the
tree includes descendants that started their own process group (a nested `timeout`) or
were reparented to launchd, so a loop never outlives its bound. Killing bounded.py ends
the tree too. A command already running above this one, in this process's own ancestry,
is refused with exit 3 instead of stacking one more copy per pass. Exits with the
command's status, 124 on the deadline like `timeout`.
"""

from __future__ import annotations

import subprocess
import sys

from long_running import proctree

USAGE = "usage: bounded.py <seconds> -- <command> [<arg>...]"
TIMED_OUT = 124
RECURSIVE = 3


def main(argv: list[str]) -> int:
    if len(argv) < 3 or not argv[0].isdigit() or argv[1] != "--":
        print(USAGE, file=sys.stderr)
        return 2
    try:
        run = proctree.spawn(argv[2:])
    except proctree.RecursivePoll as already:
        print(f"bounded.py: refusing to start {already}: it already runs above this process, so each pass would stack another copy", file=sys.stderr)
        return RECURSIVE
    proctree.end_on_signals(run)
    try:
        code = run.child.wait(timeout=int(argv[0]))
    except subprocess.TimeoutExpired:
        code = TIMED_OUT
    proctree.end(run)
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
