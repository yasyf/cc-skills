from __future__ import annotations

import json
import os
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


class CompactionCursor:
    def __init__(self, transcript: Path, offset: int):
        self.transcript = transcript
        self.start = self.offset = offset
        self.identity: tuple[int, int] | None = None
        self.overlap = b""
        self.found = False

    def compacted(self) -> bool:
        if self.found:
            return True
        with self.transcript.open("rb") as file:
            stat = os.fstat(file.fileno())
            identity = (stat.st_dev, stat.st_ino)
            if identity != self.identity or stat.st_size < self.offset:
                self.offset = self.start
                self.overlap = b""
            self.identity = identity
            file.seek(self.offset)
            while chunk := file.read(64 * 1024):
                data = self.overlap + chunk
                self.offset = file.tell()
                self.overlap = data[-(len(COMPACT_BOUNDARY) - 1):]
                if COMPACT_BOUNDARY in data:
                    self.found = True
                    return True
        return False


def orca(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["orca", "terminal", *args], capture_output=True, text=True)


def main(handle: str, text: str, transcript: str) -> None:
    path = Path(transcript)
    cursor = CompactionCursor(path, path.stat().st_size)
    deadline = time.monotonic() + DEADLINE_SECONDS
    while time.monotonic() < deadline and not cursor.compacted():
        if orca("wait", "--terminal", handle, "--for", "tui-idle", "--timeout-ms", str(WAIT_TIMEOUT_MS)).returncode == 0:
            read = orca("read", "--terminal", handle, "--screen", "--json")
            if (
                read.returncode == 0
                and prompt_screen.idle_prompt(json.loads(read.stdout)["result"]["terminal"])
                and not cursor.compacted()
            ):
                orca("send", "--terminal", handle, "--text", text, "--enter")
                return
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    from long_running import drive

    drive.run_installed(__file__)
    main(*sys.argv[1:])
