# /// script
# requires-python = ">=3.13"
# ///
"""Run the long-running plugin's CI controls in parallel.

Every script under .github/lr-controls/compat/ runs on every event: each one
drives the current code against an immutable baseline or an installed build.
A script under .github/lr-controls/negative/ copies current tests onto an
immutable baseline and proves they still fail there; its outcome depends only
on the files it copies, the script, and the shared inputs below, so on a pull
request it runs only when one of those changed, and on every other event it
always runs.

Env contract:
  LR_CONTROLS_SHARD  "i/n" keeps the selected scripts whose path hashes to shard i
  GITHUB_EVENT_NAME  "pull_request" narrows the negative controls to the diff
  GITHUB_WORKSPACE   the checkout, a merge commit on a pull request
  RUNNER_TEMP        where each control's log lands, under lr-controls/
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CONTROLS = Path(".github/lr-controls")
SHARED = {
    ".github/lr-controls/lib.sh",
    ".github/scripts/lr_controls.py",
    ".github/workflows/long-running-ci.yml",
    "plugins/long-running/pyproject.toml",
    "plugins/long-running/uv.lock",
}
COPIED = re.compile(r"\bcp (plugins/\S+)")
COMMIT = re.compile(r"\b[0-9a-f]{40}\b")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def changed() -> set[str] | None:
    if os.environ["GITHUB_EVENT_NAME"] != "pull_request":
        return None
    return set(git("diff", "--name-only", "HEAD^1", "HEAD").split())


def selected(diff: set[str] | None) -> list[Path]:
    scripts = sorted((CONTROLS / "compat").glob("*.sh"))
    for script in sorted((CONTROLS / "negative").glob("*.sh")):
        if diff is None or diff & (SHARED | {str(script), *COPIED.findall(script.read_text())}):
            scripts.append(script)
    index, count = map(int, os.environ["LR_CONTROLS_SHARD"].split("/"))
    return [script for script in scripts if zlib.crc32(str(script).encode()) % count == index - 1]


def run(script: Path, logs: Path) -> tuple[Path, int, float, Path]:
    log = logs / f"{script.parent.name}-{script.stem}.log"
    started = time.monotonic()
    with log.open("w") as sink:
        code = subprocess.run(["bash", str(script)], stdout=sink, stderr=subprocess.STDOUT).returncode
    return script, code, time.monotonic() - started, log


def main() -> int:
    diff = changed()
    scripts = selected(diff)
    print(f"{len(scripts)} controls in shard {os.environ['LR_CONTROLS_SHARD']}{'' if diff is None else ', negative controls narrowed to the diff'}", flush=True)
    if commits := sorted({commit for script in scripts for commit in COMMIT.findall(script.read_text())}):
        subprocess.run(["git", "fetch", "--no-tags", "--quiet", "--depth=1", "origin", *commits], check=True)
    subprocess.run(["uv", "sync", "--locked", "--quiet", "--project", "plugins/long-running"], check=True)
    logs = Path(os.environ["RUNNER_TEMP"]) / "lr-controls"
    logs.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(os.cpu_count()) as pool:
        results = list(pool.map(lambda script: run(script, logs), scripts))
    for script, code, seconds, log in results:
        print(f"::group::{'PASS' if code == 0 else 'FAIL'} {script} ({seconds:.0f}s)")
        print(log.read_text(), end="")
        print("::endgroup::")
    failed = [str(script) for script, code, _, _ in results if code]
    for name in failed:
        print(f"::error::{name} failed; its log is in the lr-controls artifact")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
