import ctypes
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from drive import agents, brief, orca, state, watch
from drive.render import when

LIMIT_ENV = "HOOKS_ADMISSION_LOAD_PER_CORE"
MONOREPO = "Forge-AI/monorepo"
LOCAL_BUILDERS = 8
MAC_LINES = "its Mac: line, which drive job brief --mac writes, or its Release: line"
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


def mac_step(text: str) -> str | None:
    """The step only this Mac runs that a brief declares, as written: its Mac: line, which its writer gives drive job brief --mac for a step that signs in with this Mac's machine profile or Buildkite token, or that reaches this Mac's Claude Code or the drive's dashboard, else its Release: line, since a release starts with this Mac's Buildkite token; None for neither. No other words of a brief place its job, since its fields quote where his words were typed and what earlier jobs found, nor any of a takeover section after it, which quotes an earlier lane's report, whose Mac: line places the prover install.add_prover hands its merge to, and the messages it never read."""
    own = orca.brief_of(text).partition(orca.TAKING_OVER)[0]
    found = brief.MAC.search(own) or brief.RELEASE.search(own)
    return found[1].strip() if found else None


def kept_step(job: dict) -> str | None:
    """The step only this Mac runs that the brief a job keeps declares, by mac_step; None for a job keeping no brief."""
    return mac_step(Path(job["brief"]).read_text()) if job.get("brief") else None


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
    """The builders holding a slot on this Mac, each job beside its order and its agent's or lane's last write: a job here the agent_brief hook let start whose agent has not started yet, an agent here still in its turn, or an Orca lane started with --local that launches or has not ended. A builder that builds nothing, by state.builds_nothing, such as a merged PR's live proof, holds no checkout or compiler, so it holds no slot."""
    return {
        job_id: f"{job_id} on {job['order']}, {work}"
        for job_id, job in data["jobs"].items()
        if job["kind"] == "builder" and not state.builds_nothing(job) and job["state"] in ("running", "waiting") and (work := at_work_here(job_id, job, data, moment))
    }


def at_work_here(job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    if job.get("where") != "remote" and not job["agent"] and watch.launching(job, moment):
        return f"its agent launching since {when(job['launching'], seconds=True)}"
    if job["state"] == "waiting":
        return None
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


def crowded(data: dict, job_id: str | None, moment: datetime, step: str | None) -> str | None:
    """Why the builder job takes no slot on this Mac now, None when it may: the cap's builders hold every slot but its own. A builder whose brief declares the step given, one only this Mac runs as its caller read it by mac_step, or on an open incident's order or an immediate fix part starts at once past the cap, counting against it for later builders once it runs, by his picks "Decide at start: a brief naming a Mac-only step starts here at any load" and "Start at once past the cap", since a Sprite never runs that step and the cap exists to send work to one, and one that builds nothing starts at once past it, holding no slot."""
    job = data["jobs"].get(job_id)
    if step or job and (state.builds_nothing(job) or state.open_incidents(data, orders := [job["order"], *job["carries"]]) or any(state.IMMEDIATE_FIX in state.job_parts(job, order_id) for order_id in orders)):
        return None
    others = {other: line for other, line in local_builders(data, moment).items() if other != job_id}
    return at_cap(others) if len(others) >= LOCAL_BUILDERS else None


def woken(data: dict, job_id: str, moment: datetime) -> str | None:
    """Why a message may not wake the builder's agent on this Mac: its turn ended, so the message starts it again, and crowded gives it no slot."""
    job = data["jobs"][job_id]
    if job["kind"] != "builder" or job.get("where") == "remote" or not job.get("agent") or job_id in local_builders(data, moment):
        return None
    return f"its turn ended and {why}" if (why := crowded(data, job_id, moment, kept_step(job))) else None


def on_sprite(job: dict) -> bool:
    """Whether the job's lane runs on a Sprite, so a start here moves it from there."""
    return job.get("where") == "remote" and bool(job.get("dispatch")) and not state.on_mac(job)
