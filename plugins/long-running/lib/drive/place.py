import ctypes
import os
import sys
import time
from datetime import datetime

from drive import agents, state, watch
from drive.render import when

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


def verdict(share: float, ceiling: float, builders: dict[str, str]) -> tuple[str, str]:
    """Where the next monorepo builder starts, and why."""
    if share >= ceiling:
        return "remote", f"this Mac is {share:.0%} busy, at or over its {ceiling:.0%} limit"
    if len(builders) >= LOCAL_BUILDERS:
        return "remote", at_cap(builders)
    load = f"this Mac is {share:.0%} busy, under its {ceiling:.0%} limit"
    return "local", f"{load}, with {len(builders)} of {LOCAL_BUILDERS} builders"


def at_cap(builders: dict[str, str]) -> str:
    """The cap's refusal, naming each builder it counted."""
    return f"{len(builders)} builders already run on this Mac, its cap of {LOCAL_BUILDERS} ({'; '.join(builders.values())})"


def local_builders(data: dict, moment: datetime) -> dict[str, str]:
    """The builders holding a slot on this Mac, each job beside its order and its agent's or lane's last write: a job here the watch's FREE line freed whose agent has not started yet, an agent here still in its turn, or an Orca lane started with --local that launches or has not ended."""
    return {
        job_id: f"{job_id} on {job['order']}, {work}"
        for job_id, job in data["jobs"].items()
        if job["kind"] == "builder" and job["state"] in ("running", "waiting") and (work := at_work_here(job_id, job, data, moment))
    }


def at_work_here(job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    if job["state"] == "waiting":
        return None if job["waits"] or job["agent"] or job.get("where") == "remote" else "freed to start here, its agent not started yet"
    if job.get("where") != "remote":
        if not job["agent"] or watch.local_gap(job_id, job, data, moment) is not None:
            return None
        wrote, turn = watch.last_write(job, data)
        return None if turn and agents.ended(turn) else f"its agent {job['agent']} last wrote at {when(state.stamp(wrote), seconds=True)}"
    if not state.on_mac(job):
        return None
    if not job.get("dispatch"):
        return f"its Orca lane launching since {when(job['launching'], seconds=True)}" if watch.launching(job, moment) else None
    if state.lane_death(job):
        return None
    return f"its Orca lane {job['dispatch']}, not ended"


def waiting_builders(data: dict) -> list[str]:
    """The builder jobs waiting their turn for a slot on this Mac, earliest due first: the earliest time promised him on an order each serves, then the oldest added."""
    waiting = [job_id for job_id, job in data["jobs"].items() if job["state"] == "waiting" and state.SLOT_WAIT in job["waits"]]
    return sorted(waiting, key=lambda job_id: due_first(data, data["jobs"][job_id]))


def positions(data: dict) -> dict[str, str]:
    """Each builder job waiting its turn for a slot on this Mac, with its place in line."""
    line = waiting_builders(data)
    return {job_id: f"{number} of {len(line)} in line" for number, job_id in enumerate(line, 1)}


def crowded(data: dict, job_id: str | None, moment: datetime) -> str | None:
    """Why the builder job takes no slot on this Mac now, None when it may: the cap's builders hold every slot but its own, or builders wait their turn ahead of a job that holds none, since the watch's FREE line gives each open slot to the earliest due."""
    builders = local_builders(data, moment)
    others = {other: line for other, line in builders.items() if other != job_id}
    if len(others) >= LOCAL_BUILDERS:
        return at_cap(others)
    if job_id not in builders and (line := [other for other in waiting_builders(data) if other != job_id]):
        return f"builders already wait their turn for a slot on this Mac ahead of it, earliest due first ({', '.join(line)})"
    return None


def queue(job: dict, moment: datetime) -> None:
    """Hold a builder job no agent runs waiting its turn for a slot on this Mac, so the watch reads it waiting, never abandoned, until its FREE line names it."""
    job.update(state="waiting", waits=list(dict.fromkeys([*job["waits"], state.SLOT_WAIT])), said="waits its turn for a builder slot on this Mac", updated=state.stamp(moment))


def turns(data: dict, moment: datetime) -> tuple[list[str], int]:
    """The builder jobs whose turn comes now, earliest due first, one for each slot on this Mac no builder holds, and how many builders hold one."""
    if not (line := waiting_builders(data)):
        return [], 0
    held = len(local_builders(data, moment))
    return line[: max(LOCAL_BUILDERS - held, 0)], held


def promised(data: dict, job: dict) -> str | None:
    """The earliest time promised him on an order the job serves, its own or one it carries."""
    return min((due for order_id in [job["order"], *job["carries"]] if (due := data["orders"][order_id]["due"])), key=state.parse_time, default=None)


def due_first(data: dict, job: dict) -> tuple[bool, datetime]:
    due = promised(data, job)
    return due is None, state.parse_time(due or job["created"])
