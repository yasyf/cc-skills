"""The PR checks and wakes every watch on this Mac starts: at most SLOTS of them at work at once across all watches, by a flock slot each holds while it works, and each one a watch started ending with that watch."""

import argparse
import fcntl
import functools
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from drive import state

SLOTS = 8
WAIT_SECONDS = 1.0
WATCH_PID_ENV = "CLAUDE_LONG_RUNNING_WATCH_PID"


def mark_watch() -> None:
    """Name this process as the watch in the environment each process it starts inherits, so a check or wake it starts directly ends with it."""
    os.environ[WATCH_PID_ENV] = str(os.getpid())


def die_with_watch() -> None:
    """End this process once the watch that started it ends, by a thread that reads its parent each WAIT_SECONDS: a process the watch started directly is reparented once that watch exits, however it exits, and then exits too, its slot freed with it; a process anything else started runs on."""
    watch = os.environ.get(WATCH_PID_ENV)
    if not watch or int(watch) != os.getppid():
        return

    def watch_parent() -> None:
        while os.getppid() == int(watch):
            time.sleep(WAIT_SECONDS)
        os._exit(1)

    threading.Thread(target=watch_parent, daemon=True).start()


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
    """Run a drive verb a watch starts per PR, a check or a wake, ending with the watch that started it, and in a slot, so however many watches run, at most SLOTS such verbs load the drive at once."""

    @functools.wraps(verb)
    def run(args: argparse.Namespace) -> int:
        die_with_watch()
        with slot():
            return verb(args)

    return run
