"""The PR checks and wakes every watch on this Mac starts: at most SLOTS of them at work at once across all watches, by a flock slot each holds while it works, and each one a watch started ending with that watch."""

import argparse
import fcntl
import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from drive import procs, state

SLOTS = 8
WAIT_SECONDS = 1.0
WATCH_PID_ENV = "CLAUDE_LONG_RUNNING_WATCH_PID"


def mark_watch() -> None:
    """Name this process, by its pid and start, as the watch in the environment every process it starts inherits, its land and relay loops and what they start included, so each check or wake started under it ends with it."""
    os.environ[WATCH_PID_ENV] = f"{os.getpid()}:{procs.started(os.getpid())}"


def die_with_watch() -> None:
    """End this process once the watch it was started under ends, by a thread that reads that watch's start each WAIT_SECONDS, however far below the watch it was started and whether or not it was detached, its slot freed with it, since a pid whose start moved is a process the system handed that pid since; a process no watch started, or the watch itself running a verb in place, runs on."""
    if not (watch := os.environ.get(WATCH_PID_ENV)):
        return
    pid, _, started = watch.partition(":")
    if int(pid) == os.getpid():
        return
    ticks = threading.Event()

    def watch_alive() -> None:
        while str(procs.started(int(pid))) == started:
            ticks.wait(WAIT_SECONDS)
        os._exit(1)

    threading.Thread(target=watch_alive, daemon=True).start()


@contextmanager
def slot() -> Iterator[None]:
    """Hold one of SLOTS slots shared by every drive on this Mac while the body runs, waiting until one frees; the kernel frees a slot the moment its holder exits, however it exits."""
    folder = state.root() / "slots"
    folder.mkdir(parents=True, exist_ok=True)
    while True:
        for index in range(SLOTS):
            handle = (folder / f"child-{index}.lock").open("a")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                continue
            with handle:
                yield
            return
        time.sleep(WAIT_SECONDS)


def capped(verb: Callable[[argparse.Namespace], int]) -> Callable[[argparse.Namespace], int]:
    """Mark a drive verb a watch starts per PR, a check or a wake, for held, which cli.main enters before the verb reads anything of the drive."""
    verb.capped = True
    return verb


@contextmanager
def held(verb: Callable[[argparse.Namespace], int]) -> Iterator[None]:
    """Run a verb capped marks ending with the watch it was started under and in a slot, taken before it parses its PRs or loads the drive, so however many watches and land loops run, at most SLOTS such verbs hold the drive in memory at once and each one waiting costs only the interpreter; any other verb runs as it is."""
    if not getattr(verb, "capped", False):
        yield
        return
    die_with_watch()
    with slot():
        yield


def reap_inherited() -> None:
    """Reap, as each exits, every child this process holds at its start, which only an earlier install's image of it, replaced in place by an exec, can have started: no Popen of this image waits on one, so each would stay a zombie under it for good; a child this image starts is its Popen's to wait on, never this thread's."""
    with subprocess.Popen(["ps", "-A", "-o", "pid=", "-o", "ppid="], stdout=subprocess.PIPE, text=True) as listing:
        listed = listing.communicate()[0]
    held = {int(pid) for pid, parent in (line.split() for line in listed.splitlines()) if int(parent) == os.getpid() and int(pid) != listing.pid}
    if not held:
        return

    def reap() -> None:
        while held:
            for pid in list(held):
                try:
                    done, _ = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    done = pid
                if done:
                    held.discard(pid)
            time.sleep(WAIT_SECONDS)

    threading.Thread(target=reap, daemon=True).start()
