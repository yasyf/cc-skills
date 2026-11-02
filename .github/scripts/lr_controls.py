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

Shards split the selected scripts longest first by the seconds each took in
the last full run, from .github/lr-controls/seconds.json; a script missing
there weighs the median. Each run writes its own seconds to seconds.json in
its lr-controls artifact, which .github/scripts/lr_durations.py folds back.

Env contract:
  LR_CONTROLS_SHARD  "i/n" keeps shard i of the selected scripts
  GITHUB_EVENT_NAME  "pull_request" narrows the negative controls to the diff
  GITHUB_WORKSPACE   the checkout, a merge commit on a pull request
  RUNNER_TEMP        where each control's log lands, under lr-controls/
"""

from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CONTROLS = Path(".github/lr-controls")
SECONDS = CONTROLS / "seconds.json"
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
    recorded = json.loads(SECONDS.read_text())
    median = statistics.median(recorded.values())
    weight = {script: recorded.get(str(script), median) for script in scripts}
    index, count = map(int, os.environ["LR_CONTROLS_SHARD"].split("/"))
    loads, mine = [0.0] * count, []
    for script in sorted(scripts, key=lambda script: (-weight[script], str(script))):
        shard = loads.index(min(loads))
        loads[shard] += weight[script]
        if shard == index - 1:
            mine.append(script)
    return mine


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
    (logs / "seconds.json").write_text(json.dumps({str(script): round(seconds, 1) for script, _, seconds, _ in results}))
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
