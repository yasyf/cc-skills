"""Commands whose whole process tree dies with them, nested process groups and reparented orphans included."""

from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

TAG = "LR_BOUNDED"
TAGGED = re.compile(rf"(?:^|\s){TAG}=(?P<chain>\S+)")
PROC = Path("/proc")
GRACE = 5.0
POLL = 0.1


class RecursivePoll(RuntimeError):
    """The command already runs in this process's own ancestry, so starting it again would stack one more copy per pass."""


@dataclass(frozen=True)
class Run:
    child: subprocess.Popen
    token: str
    started: float


@dataclass(frozen=True)
class Proc:
    ppid: int
    age: float


def spawn(command: list[str]) -> Run:
    """Start `command` in its own session, tagged so every descendant, however it regroups or reparents, is found by `end`."""
    digest = hashlib.sha256("\0".join(command).encode()).hexdigest()[:12]
    chain = [entry for entry in os.environ.get(TAG, "").split(":") if entry]
    if any(entry.endswith(f".{digest}") for entry in chain):
        raise RecursivePoll(" ".join(command))
    token = f"{os.getpid()}.{digest}"
    started = time.time()
    return Run(subprocess.Popen(command, start_new_session=True, env=os.environ | {TAG: ":".join([*chain, token])}), token, started)


def seconds(etime: str) -> float:
    days, _, clock = etime.rpartition("-")
    total = 0
    for part in clock.split(":"):
        total = total * 60 + int(part)
    return total + int(days or 0) * 86400


def table() -> dict[int, Proc]:
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,etime="], capture_output=True, text=True, check=True).stdout
    return {int(pid): Proc(int(ppid), seconds(etime)) for pid, ppid, etime in (line.split() for line in out.splitlines())}


def subtree(procs: dict[int, Proc], roots: Iterable[int]) -> set[int]:
    children: dict[int, list[int]] = defaultdict(list)
    for pid, proc in procs.items():
        children[proc.ppid].append(pid)
    seen: set[int] = set()
    stack = list(roots)
    while stack:
        if (pid := stack.pop()) not in seen:
            seen.add(pid)
            stack.extend(children[pid])
    return seen


def environments(pids: list[int]) -> Iterable[tuple[int, str]]:
    if PROC.is_dir():
        for pid in pids:
            try:
                yield pid, (PROC / str(pid) / "environ").read_bytes().decode(errors="replace").replace("\0", " ")
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass
        return
    if not pids:
        return
    out = subprocess.run(["ps", "eww", "-o", "pid=,command=", "-p", ",".join(map(str, pids))], capture_output=True, text=True).stdout
    for line in out.splitlines():
        pid, _, rest = line.strip().partition(" ")
        yield int(pid), rest


def tree(run: Run) -> set[int]:
    """The live child's descendants plus every process started since the run that carries its token, with their descendants.

    Candidates are processes no older than the run, outside the child's own subtree: on macOS launchd's children,
    where every orphan lands; on Linux, where a subreaper may adopt one instead, any such process.
    """
    procs = table()
    live = subtree(procs, [run.child.pid]) if run.child.returncode is None else set()
    window = time.time() - run.started + 1
    candidates = [pid for pid, proc in procs.items() if pid not in live and proc.age <= window and (PROC.is_dir() or proc.ppid == 1)]
    carriers = [pid for pid, env in environments(candidates) if (match := TAGGED.search(env)) and run.token in match["chain"].split(":")]
    return (live | subtree(procs, carriers)) - {os.getpid()}


def signal_all(pids: Iterable[int], signum: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, signum)
        except (ProcessLookupError, PermissionError):
            pass


def alive(pid: int, run: Run) -> bool:
    if pid == run.child.pid:
        return run.child.poll() is None
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def end(run: Run, grace: float = GRACE) -> None:
    victims = tree(run)
    signal_all(victims, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while any(alive(pid, run) for pid in victims) and time.monotonic() < deadline:
        time.sleep(POLL)
    if survivors := {pid for pid in victims if alive(pid, run)}:
        signal_all(survivors | tree(run), signal.SIGKILL)
    run.child.wait()


def end_on_signals(run: Run) -> None:
    """Make SIGTERM, SIGHUP and SIGINT to this process end the run's tree before exiting, so killing the owner never orphans it."""

    def stop(signum: int, _frame: object) -> None:
        end(run)
        raise SystemExit(128 + signum)

    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, stop)
