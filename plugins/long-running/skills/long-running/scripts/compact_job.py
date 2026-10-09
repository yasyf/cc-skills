from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import prompt_screen

POLL_SECONDS = 30
DEADLINE_SECONDS = 30 * 60
WAIT_TIMEOUT_MS = 120_000
MAX_LIFETIME_SECONDS = DEADLINE_SECONDS + WAIT_TIMEOUT_MS // 1000 + POLL_SECONDS
COMPACT_BOUNDARY = b'"subtype":"compact_boundary"'


def compacted_since(transcript: Path, offset: int) -> bool:
    with transcript.open("rb") as file:
        file.seek(offset)
        return COMPACT_BOUNDARY in file.read()


def orca(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["orca", "terminal", *args], capture_output=True, text=True)


def main(handle: str, text: str, transcript: str) -> None:
    path = Path(transcript)
    offset = path.stat().st_size
    deadline = time.monotonic() + DEADLINE_SECONDS
    while time.monotonic() < deadline and not compacted_since(path, offset):
        if orca("wait", "--terminal", handle, "--for", "tui-idle", "--timeout-ms", str(WAIT_TIMEOUT_MS)).returncode == 0:
            read = orca("read", "--terminal", handle, "--screen", "--json")
            if (
                read.returncode == 0
                and prompt_screen.idle_prompt(json.loads(read.stdout)["result"]["terminal"])
                and not compacted_since(path, offset)
            ):
                orca("send", "--terminal", handle, "--text", text, "--enter")
                return
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main(*sys.argv[1:])
