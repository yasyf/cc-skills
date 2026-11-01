import ctypes
from contextlib import contextmanager
from collections.abc import Iterator
import json
import os
import sys
import threading
import time


OVERFLOW_PHASES = {"overflow-routes", "overflow-members-and-escalations", "overflow-cci-blockers"}


def snapshot() -> dict[str, int]:
    proc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    proc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    proc.proc_pid_rusage.restype = ctypes.c_int
    usage = (ctypes.c_uint64 * 40)()
    if proc.proc_pid_rusage(os.getpid(), 2, ctypes.byref(usage)):
        raise OSError(ctypes.get_errno(), "proc_pid_rusage")
    return {"read_bytes": usage[18], "write_bytes": usage[19], "cpu_ticks": usage[2] + usage[3]}


class OnePassIO:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.seen: set[str] = set()
        self.lock = threading.Lock()
        self.overflow_owner: int | None = None

    def finish(self) -> None:
        with self.lock:
            self.enabled = False

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        owner = threading.get_ident()
        with self.lock:
            selected = name not in self.seen and (self.overflow_owner == owner if name in OVERFLOW_PHASES else self.enabled)
            if selected:
                self.seen.add(name)
                if name == "overflow":
                    self.overflow_owner = owner
        if not selected:
            yield
            return
        try:
            started, before, failure = time.monotonic(), None, None
            try:
                before = snapshot()
            except OSError as error:
                failure = {"stage": "before", "type": type(error).__name__, "errno": error.errno}
            completed = False
            try:
                yield
                completed = True
            finally:
                delta = None
                if before is not None:
                    try:
                        after = snapshot()
                    except OSError as error:
                        failure = {"stage": "after", "type": type(error).__name__, "errno": error.errno}
                    else:
                        delta = {key: after[key] - before[key] for key in before}
                try:
                    print("PASS-IO " + json.dumps({"pid": os.getpid(), "thread": threading.current_thread().name, "native_id": threading.get_native_id(), "phase": name, "started_monotonic": started, "seconds": time.monotonic() - started, "completed": completed, "status": "COUNTER_ERROR" if failure else "CAPTURED", "counter_error": failure, "delta": delta, "scope": "process aggregate across overlapping threads; logical IO, not exclusive phase or physical IO"}), file=sys.stderr, flush=True)
                except OSError:
                    with self.lock:
                        self.enabled = False
                        self.overflow_owner = None
        finally:
            if name == "overflow":
                with self.lock:
                    self.overflow_owner = None
