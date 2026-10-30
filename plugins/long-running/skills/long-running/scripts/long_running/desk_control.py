from __future__ import annotations

import fcntl
import os
from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...

    def sleep(self, seconds: float) -> None: ...


class QuiesceFailed(RuntimeError):
    pass


class DeskControl:
    def __init__(self, source: Path, clock: Clock):
        self.source = source
        self.clock = clock

    def marker(self, pid: int | None = None) -> Path:
        return self.source.with_name(f"{self.source.name}.stop.{pid or os.getpid()}")

    @contextmanager
    def lock(self, desk: str) -> Iterator[int | None]:
        with self.source.with_name(f"{self.source.name}.{desk}.lock").open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.seek(0)
                yield int(handle.read().strip() or 0)
                return
            handle.truncate(0)
            handle.write(f"{os.getpid()}\n")
            handle.flush()
            yield None

    def holder(self, desk: str) -> int | None:
        with self.lock(desk) as holder:
            return holder

    @contextmanager
    def quiesced(self, desk: str, wait: timedelta, wake: float) -> Iterator[None]:
        pid = None
        deadline = self.clock.now() + wait
        while True:
            with self.lock(desk) as holder:
                if holder is None:
                    try:
                        yield
                    finally:
                        if pid is not None:
                            self.marker(pid).unlink(missing_ok=True)
                    return
                if not holder:
                    raise QuiesceFailed(f"the {desk} lock holder has not published its pid; no stop requested")
                if pid is None:
                    pid = holder
                    self.marker(pid).write_text(f"{pid}\n")
                elif holder != pid:
                    raise QuiesceFailed(f"runner {holder} replaced {pid}; the {desk} desk is not quiescent")
                if self.clock.now() >= deadline:
                    raise QuiesceFailed(f"runner {pid} still holds the {desk} desk after {wait}; {self.marker(pid)} stays; no successor started")
            self.clock.sleep(wake)


@contextmanager
def quiesced(desks: Iterable[tuple[DeskControl, str]], wait: timedelta, wake: float) -> Iterator[None]:
    with ExitStack() as leases:
        for control, desk in sorted(desks, key=lambda pair: (str(pair[0].source.resolve()), pair[1])):
            leases.enter_context(control.quiesced(desk, wait, wake))
        yield
