"""The drive's own detached processes an older install left running once nothing waits on them: a `drive pr check` or `drive pr wake` whose watch a newer install replaced, a `drive job prove` past any proof's time, a `drive reload --seat` waiting for its seat's turn to end, which the newest install's reload replaces, and a `drive handler-start` waiting its handler's turn, which would start a handler no install keeps."""

import os
import re
import signal
import time

from drive import procs, state

STALE_PROVE_SECONDS = 2 * 3600
DRIVE_COMMAND = re.compile(r"/long-running/(?P<version>\d+(?:\.\d+)+)/bin/drive (?P<verb>job prove|reload --seat|pr check|pr wake|handler-start)\b.*--drive (?P<drive>\S+)")


def superseded(drive: str, now: float, own: bool) -> list[tuple[int, str]]:
    """Each process of an older install than this one running `drive pr check`, `drive pr wake`, `drive job prove` or, from the drive's own watch, own, `drive reload --seat` or `drive handler-start` on the drive that launchd adopted, since nothing that started it waits on it, with its command: every such check, wake, reload and handler start, which the newest install's watch, reload and start begin again, and a proof only once it has run STALE_PROVE_SECONDS, past the longest proof's timeout."""
    found = []
    for process in procs.table():
        if process.ppid != 1 or process.zombie or not (command := procs.command(process.pid)) or not (match := DRIVE_COMMAND.search(command)):
            continue
        if match["drive"] != drive or state.version_key(match["version"]) >= state.version_key(state.VERSION) or match["verb"] in ("reload --seat", "handler-start") and not own:
            continue
        if match["verb"] == "job prove" and ((started := procs.started(process.pid)) is None or now - started < STALE_PROVE_SECONDS):
            continue
        found.append((process.pid, command))
    return found


def end(drive: str, own: bool) -> list[str]:
    """End each superseded process of the drive with SIGTERM, read again just before so a pid the system handed another process is left alone, start the newest install's `drive reload` on the drive when a reload was among them, which hands each seat to a process of this install; a line each."""
    ended = []
    for pid, command in superseded(drive, time.time(), own):
        if procs.command(pid) != command:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        ended.append(f"ended pid {pid}, left by an older install: {command}")
    if any(" reload --seat " in line for line in ended):
        state.detach(drive, ["reload"], "reloads.log")
        ended.append(f"started drive reload on {drive} from long-running {state.VERSION}")
    return ended
