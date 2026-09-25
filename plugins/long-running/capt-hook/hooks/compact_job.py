from __future__ import annotations

import json
import subprocess
import sys
import time

__capt_hook_skip__ = True

RULE = "─"
PROMPT = "❯"
POLL_SECONDS = 30
DEADLINE_SECONDS = 30 * 60


def input_empty(terminal: dict) -> bool:
    if terminal.get("draft") or terminal.get("source") != "screen":
        return False
    tail = [line.strip() for line in terminal.get("tail") or []]
    rules = [i for i, line in enumerate(tail) if line and set(line) == {RULE}]
    boxes = [(top, bottom) for top, bottom in zip(rules, rules[1:]) if tail[top + 1].startswith(PROMPT)]
    if not boxes:
        return False
    top, bottom = boxes[-1]
    return tail[top + 1 : bottom] == [PROMPT]


def orca(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["orca", "terminal", *args], capture_output=True, text=True)


def main(handle: str, text: str) -> None:
    deadline = time.monotonic() + DEADLINE_SECONDS
    while time.monotonic() < deadline:
        if orca("wait", "--terminal", handle, "--for", "tui-idle", "--timeout-ms", "120000").returncode == 0:
            read = orca("read", "--terminal", handle, "--screen", "--json")
            if read.returncode == 0 and input_empty(json.loads(read.stdout)["result"]["terminal"]):
                orca("send", "--terminal", handle, "--text", text, "--enter")
                return
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main(*sys.argv[1:])
