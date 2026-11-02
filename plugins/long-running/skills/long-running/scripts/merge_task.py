"""Run one merge task of the landing desk, the job `sprite-job.sh` copied to this Sprite: a `stack-enqueue` or `tools/ci`
call whose `argv` names files in the landing checkout, run in that checkout's `.merge` worktree at the `head` the desk's
checkout holds, and write its exit code, standard output and standard error to the job's result.json.

STDLIB ONLY. The worktree is added on the first task, moved to each new head, and made again when that checkout fails,
as on an index a Sprite restart truncated, all under an exclusive lock, which every running task holds shared, so no
task's files change under it.

usage: merge_task.py <job directory>
"""

from __future__ import annotations

import fcntl
import json
import shutil
import subprocess
import sys
from pathlib import Path


def git(*argv: str) -> None:
    subprocess.run(["git", *argv], check=True, capture_output=True, text=True)


def run(job: dict) -> dict:
    checkout = Path(job["checkout"])
    tree = checkout.with_name(f"{checkout.name}.merge")
    with open(f"{tree}.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if not (tree / ".git").exists() or subprocess.run(["git", "-C", str(tree), "checkout", "--quiet", "--force", "--detach", job["head"]], capture_output=True).returncode:
                shutil.rmtree(tree, ignore_errors=True)
                git("-C", str(checkout), "worktree", "prune")
                git("-C", str(checkout), "worktree", "add", "--force", "--detach", str(tree), job["head"])
        except subprocess.CalledProcessError as failed:
            return {"error": f"the merge worktree {tree} did not reach {job['head'][:12]}: {' '.join(failed.cmd)} exited {failed.returncode}: {failed.stderr.strip()[-300:]}"}
        fcntl.flock(lock, fcntl.LOCK_SH)
        argv = [f"{tree}{arg[len(str(checkout)):]}" if arg == str(checkout) or arg.startswith(f"{checkout}/") else arg for arg in job["argv"]]
        done = subprocess.run(argv, cwd=tree, capture_output=True, text=True, errors="replace")
    return {"code": done.returncode, "out": done.stdout, "err": done.stderr}


def main(directory: Path) -> None:
    result = run(json.loads((directory / "job.json").read_text()))
    (directory / "result.json").write_text(json.dumps(result) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
