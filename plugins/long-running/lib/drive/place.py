import ctypes
import os
import sys
import time
from datetime import datetime

from drive import state, watch

LIMIT_ENV = "HOOKS_ADMISSION_LOAD_PER_CORE"
MONOREPO = "Forge-AI/monorepo"
LOCAL_BUILDERS = 8
SAMPLE_SECONDS = 1.0
HOST_CPU_LOAD_INFO = 3
HOST_CPU_STATES = 4
HOST_CPU_IDLE = 2


def cpu_ticks() -> tuple[int, int]:
    """Idle and total CPU ticks since boot."""
    if sys.platform == "darwin":
        system = ctypes.CDLL("/usr/lib/libSystem.dylib")
        system.mach_host_self.restype = ctypes.c_uint
        states = (ctypes.c_uint * HOST_CPU_STATES)()
        count = ctypes.c_uint(HOST_CPU_STATES)
        system.host_statistics(system.mach_host_self(), HOST_CPU_LOAD_INFO, states, ctypes.byref(count))
        return states[HOST_CPU_IDLE], sum(states)
    with open("/proc/stat") as stat:
        ticks = [int(tick) for tick in stat.readline().split()[1:]]
    return ticks[3] + ticks[4], sum(ticks)


def busy() -> float:
    """The share of CPU time this Mac spends working, sampled the way its load gate samples it."""
    idle, total = cpu_ticks()
    time.sleep(SAMPLE_SECONDS)
    later_idle, later_total = cpu_ticks()
    return 1 - (later_idle - idle) / (later_total - total) if later_total > total else 0.0


def limit() -> float:
    """The load gate's limit, the share of CPU above which it refuses a local build."""
    return float(os.environ.get(LIMIT_ENV, "1"))


def verdict(share: float, ceiling: float, local_builders: int) -> tuple[str, str]:
    """Where the next monorepo builder starts, and why."""
    if share >= ceiling:
        return "remote", f"this Mac is {share:.0%} busy, at or over its {ceiling:.0%} limit"
    if local_builders >= LOCAL_BUILDERS:
        return "remote", f"{local_builders} builders already run on this Mac, its cap of {LOCAL_BUILDERS}"
    load = f"this Mac is {share:.0%} busy, under its {ceiling:.0%} limit"
    return "local", f"{load}, with {local_builders} of {LOCAL_BUILDERS} builders"


def local_builders(data: dict, moment: datetime) -> int:
    """The builders at work on this Mac: running here, as an agent still writing or an Orca lane started with --local that has not ended."""
    return sum(
        job["kind"] == "builder" and job["state"] == "running" and at_work_here(job_id, job, data, moment) for job_id, job in data["jobs"].items()
    )


def at_work_here(job_id: str, job: dict, data: dict, moment: datetime) -> bool:
    if job.get("where") != "remote":
        return bool(job["agent"]) and watch.local_gap(job_id, job, data, moment) is None
    if not state.on_mac(job):
        return False
    if not job.get("dispatch"):
        return watch.launching(job, moment)
    return all(death["dispatch"] != job["dispatch"] for death in job.get("deaths", []))


def waiting_builders(data: dict) -> list[str]:
    """The builder jobs for this Mac that no agent runs yet, oldest due first: the earliest time promised him on an order each serves, then the oldest added."""
    waiting = [
        job_id
        for job_id, job in data["jobs"].items()
        if job["kind"] == "builder" and job["state"] == "running" and job.get("where") != "remote" and not job["agent"]
    ]
    return sorted(waiting, key=lambda job_id: due_first(data, data["jobs"][job_id]))


def promised(data: dict, job: dict) -> str | None:
    """The earliest time promised him on an order the job serves, its own or one it carries."""
    return min((due for order_id in [job["order"], *job["carries"]] if (due := data["orders"][order_id]["due"])), key=state.parse_time, default=None)


def due_first(data: dict, job: dict) -> tuple[bool, datetime]:
    due = promised(data, job)
    return due is None, state.parse_time(due or job["created"])
