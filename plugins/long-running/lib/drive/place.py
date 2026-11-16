import contextlib
import ctypes
import fcntl
import json
import os
import sys
import time
from collections.abc import Iterator
from datetime import datetime

from drive import agents, brief, orca, spend, state, watch
from drive.render import when

LIMIT_ENV = "HOOKS_ADMISSION_LOAD_PER_CORE"
LOCAL_BUILDERS = 8
CAP_RULE = "on builders that compile here, where a staging tester, a merged PR's prover or another builder marked --builds-nothing holds no slot"
MAC_LINES = "its Mac: line, which drive job brief --mac writes, or its Release: line"
SAMPLE_SECONDS = 1.0
HOST_CPU_LOAD_INFO = 3
HOST_CPU_STATES = 4
HOST_CPU_IDLE = 2
CAPS_FILE = "caps.json"
CAPS = {"local": 12, "sprite": 30, "priority": {"local": 40, "sprite": 60}, "load_per_core": 1, "free_memory": 15}
CAP_NAMES = {"local": "local Claude lanes work", "sprite": "Sprite lanes run"}
LANES_FILE = "lane-counts.json"
LANES_SECONDS = 20
ADMISSION_LOCK = "admission.lock"


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


def verdict(share: float, ceiling: float, builders: dict[str, str]) -> tuple[str, str]:
    """Where the next builder on the home repository starts, and why."""
    if share >= ceiling:
        return "remote", f"this Mac is {share:.0%} busy, at or over its {ceiling:.0%} limit"
    if len(builders) >= LOCAL_BUILDERS:
        return "remote", at_cap(builders)
    load = f"this Mac is {share:.0%} busy, under its {ceiling:.0%} limit"
    return "local", f"{load}, with {len(builders)} of {LOCAL_BUILDERS} builders"


def at_cap(builders: dict[str, str]) -> str:
    """The cap's refusal, naming its rule and each builder it counted."""
    return f"{len(builders)} builders already run on this Mac, its cap of {LOCAL_BUILDERS} {CAP_RULE} ({'; '.join(builders.values())})"


def local_builders(data: dict, moment: datetime) -> dict[str, str]:
    """The builders holding a slot on this Mac, each job beside its order and its agent's or lane's last write: a job here the agent_brief hook let start whose agent launches, an agent here still in its turn, or an Orca lane started with --local that launches, or whose session in the worktree its launch recorded, by watch.lane_transcript, has not started or is still in its turn. A builder that builds nothing, by state.builds_nothing, such as a merged PR's live proof or a staging tester, compiles nothing here, so it holds no slot."""
    return {
        job_id: f"{job_id} on {job['order']}, {work}"
        for job_id, job in data["jobs"].items()
        if job["kind"] == "builder" and not state.builds_nothing(job) and job["state"] in ("running", "waiting") and (work := at_work_here(job_id, job, data, moment))
    }


def at_work_here(job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    if job["kind"] == "codex" and job.get("codex_run") and job["state"] == "running":
        return f"its codex-ask run at work in {job['codex_run']}"
    if job.get("where") != "remote" and not job["agent"] and watch.launching(job, moment):
        return f"its agent launching since {when(job['launching'], seconds=True)}"
    if job.get("where") != "remote" and job["agent"] and state.on_mac(job) and watch.launching(job, moment):
        return f"its Orca lane launching since {when(job['launching'], seconds=True)}, taking over from its agent {job['agent']}"
    if job["state"] == "waiting" and not watch.launching(job, moment):
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
    if not (job.get("worktree") and (path := watch.lane_transcript(job["worktree"]))):
        return f"its Orca lane {job['dispatch']}, its session not started yet"
    wrote, turn = watch.written(path)
    return None if turn and agents.ended(turn) else f"its Orca lane {job['dispatch']} last wrote at {when(state.stamp(wrote), seconds=True)}"


def standings(data: dict) -> dict[tuple[str | None, str | None], float]:
    """How each project, as (project, None), and each of its workstreams, as (project, workstream), stands for a turn when a cap is full, lower first: by his newest move on an open order filed there, so the workstream he is driving now holds its turns and the one he touched longest ago pauses first, by his words "if we are hitting limits, the lowest priority workstreams or even projects should just straight be paused"."""
    held: dict[tuple[str | None, str | None], float] = {}
    for order in state.open_orders(data).values():
        stands = -state.parse_time(order["updated"]).timestamp()
        for key in ((order["project"], None), (order["project"], order["workstream"])):
            held[key] = min(held.get(key, stands), stands)
    return held


def caps() -> dict[str, float]:
    """The caps every drive and watch on this Mac shares on the Claude lanes it starts, read at each admission so a change holds at once: how many local Claude lanes, of any role, work at once, how many Sprite lanes run, the 1-minute load per core and the percent of memory free past which no local lane starts, CAPS unless the owner's caps.json under the state root sets any of them, as {"local": 12, "sprite": 30, "load_per_core": 1, "free_memory": 15}."""
    path = state.root() / CAPS_FILE
    return CAPS | (json.loads(path.read_text()) if path.is_file() else {})


def free_memory() -> int:
    """The share of this Mac's memory free to use, in percent: the kernel's memorystatus level on macOS, which counts the pages it can reclaim, never the free pages alone, and MemAvailable elsewhere."""
    if sys.platform == "darwin":
        system = ctypes.CDLL("/usr/lib/libSystem.dylib")
        level = ctypes.c_int()
        size = ctypes.c_size_t(ctypes.sizeof(level))
        system.sysctlbyname(b"kern.memorystatus_level", ctypes.byref(level), ctypes.byref(size), None, ctypes.c_size_t(0))
        return level.value
    with open("/proc/meminfo") as info:
        fields = {name: int(value.split()[0]) for name, _, value in (line.partition(":") for line in info)}
    return fields["MemAvailable"] * 100 // fields["MemTotal"]


def machine() -> tuple[float, int, int]:
    """This Mac's 1-minute load average, its cores and the percent of its memory free, as the load gate reads them: from the "machine" object of caps.json when it sets one, as a test's state root does so no test reads the load of the machine running it, else from the kernel."""
    if given := caps().get("machine"):
        return given["load"], given["cores"], given["free_memory"]
    return os.getloadavg()[0], os.cpu_count() or 1, free_memory()


def strained() -> str | None:
    """Why this Mac takes no new local Claude lane now: its 1-minute load average is over its core count times the load_per_core of caps, or under its free_memory percent of its memory is free, each read by machine; None when neither holds."""
    held = caps()
    load, cores, free = machine()
    if load > cores * held["load_per_core"]:
        return f"this Mac's 1-minute load is {load:.0f}, over its {cores} cores times {held['load_per_core']:g}, set by {state.root() / CAPS_FILE}"
    if free < held["free_memory"]:
        return f"{free}% of this Mac's memory is free, under {held['free_memory']:g}%, set by {state.root() / CAPS_FILE}"
    return None


def local_lanes(data: dict, moment: datetime) -> dict[str, str]:
    """The drive's Claude lanes at work on this Mac, each beside what it is doing: each job of any kind at work here, by at_work_here, and each watchdog agent at work on a stall, by stall.starts."""
    from drive import stall

    jobs = {job_id: f"{job_id} {job['kind']} on {job['order']}, {work}" for job_id, job in data["jobs"].items() if job["state"] in ("running", "waiting") and (work := at_work_here(job_id, job, data, moment))}
    watchdogs = {f"watchdog {number}": f"a watchdog agent on a stall ({number})" for number in range(1, stall.starts(data["stalls"], moment)[0] + 1)}
    return jobs | watchdogs


def sprite_lanes(data: dict, moment: datetime) -> dict[str, str]:
    """The drive's lanes running on a Sprite, each beside what it is doing: each running job whose live lane is on one, and each launching its first."""
    return {
        job_id: f"{job_id} {job['kind']} on {job['order']}, {work}"
        for job_id, job in data["jobs"].items()
        if job["state"] not in state.FINISHED_JOB_STATES
        and (
            work := "its Sprite lane at work"
            if job["state"] == "running" and on_sprite(job) and not state.lane_death(job, "dispatch")
            else f"its Sprite lane launching since {when(job['launching'], seconds=True)}"
            if not state.on_mac(job) and not job.get("dispatch") and job.get("where") == "remote" and watch.launching(job, moment)
            else None
        )
    }


@contextlib.contextmanager
def admitting() -> Iterator[None]:
    """Hold this Mac's admission lock, one for every drive and watch, so a count of its lanes and the start it admits are one step and two starts never both take the last room."""
    state.root().mkdir(parents=True, exist_ok=True)
    with open(state.root() / ADMISSION_LOCK, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def lanes(moment: datetime) -> dict[str, dict[str, str]]:
    """Every drive's lanes on this Mac, by local_lanes and sprite_lanes, each keyed by its drive and id, counted again once the count kept in LANES_FILE under the state root is LANES_SECONDS old or an admission dropped it."""
    path = state.root() / LANES_FILE
    with contextlib.suppress(FileNotFoundError, json.JSONDecodeError):
        kept = json.loads(path.read_text())
        if (moment - state.parse_time(kept["at"])).total_seconds() < LANES_SECONDS:
            return kept["lanes"]
    counted: dict[str, dict[str, str]] = {"local": {}, "sprite": {}}
    for name in state.names():
        data = state.load(name)
        counted["local"] |= {f"{name}/{key}": line for key, line in local_lanes(data, moment).items()}
        counted["sprite"] |= {f"{name}/{key}": line for key, line in sprite_lanes(data, moment).items()}
    made = path.with_name(f"{LANES_FILE}.{os.getpid()}")
    made.write_text(json.dumps({"at": state.stamp(moment), "lanes": counted}))
    os.replace(made, path)
    return counted


def counted_again() -> None:
    """Drop the kept count of lanes, so the next admission counts afresh, as each start an admission let through makes it stale."""
    (state.root() / LANES_FILE).unlink(missing_ok=True)


def preempts(data: dict, job: dict) -> bool:
    """Whether a job is of the priority class, which starts past the load gate, the daily budget and every other job's cap, waiting only on the higher priority cap of cap_of: one marked urgent alone by drive job add or set --priority, one carrying a part drive order set --urgent marked, one serving an open priority order, by state.first_in_line, or an open incident, or carrying an immediate fix part, by his words on a priority order, "they aren't slowed down at any cost by anything", and his Nov 16 cap ruling, "priority still bypass the caps, you can lower for rest of the work tho, or have caps for prio but just higher ones"."""
    orders = [job["order"], *job["carries"]]
    return bool(job.get("priority")) or state.urgent_part(data, job) or state.first_in_line(data, job) or bool(state.open_incidents(data, orders)) or any(state.IMMEDIATE_FIX in state.job_parts(job, order_id) for order_id in orders)


def admission(data: dict, job_id: str | None, where: str, moment: datetime) -> str | None:
    """Why no new Claude lane starts now, where "local" for a lane on this Mac of any role and "sprite" for one on a Sprite, None when it may: one of a job preempts lets through waits only on the priority cap of cap_of; a job whose project spent its daily budget waits for the next day, by spend.over; a local builder that builds nothing, by state.builds_nothing, starts at once past the load gate and the local cap, since it compiles nothing here; any other local one waits while strained reads this Mac loaded or short of memory, and either while the lanes every drive here runs fill its cap by caps, the job's own lane not counted. Its work waits its turn, never fails."""
    job = data["jobs"].get(job_id) if job_id else None
    priority = bool(job) and preempts(data, job)
    if not priority and job and (why := spend.over(data, job, moment)):
        return why
    if not priority and where == "local" and job and state.builds_nothing(job):
        return None
    if not priority and where != "sprite" and (why := strained()):
        return why
    cap = cap_of(where, priority)
    held = {key: line for key, line in lanes(moment)[where].items() if not job_id or key.rpartition("/")[2] != job_id}
    if len(held) < cap:
        return None
    shown = "; ".join(list(held.values())[:5])
    return f"{len(held)} {CAP_NAMES[where]} across this Mac's drives, at its {'priority ' if priority else ''}cap of {cap} set by {state.root() / CAPS_FILE} ({shown}{'; …' if len(held) > 5 else ''})"


ADMITTED = "This Mac admits your lane now, after its caps held it: carry the job on, the texts held for it below."


def await_admission(name: str, job_id: str, where: str, argv: list[str], text: str | None = None) -> None:
    """Return once this Mac admits a lane of the drive's job where it starts next, "local" or "sprite", by admission; else mark the job waiting on state.ADMISSION_WAITS's wait for where, with why and the drive command argv that starts it, holding the text this start carried so nothing is lost, and refuse, so the launch process exits, drive status and owing.waiting see the wait, and the watch runs argv once this Mac admits the lane."""
    with admitting():
        if not (why := admission(state.load(name), job_id, where, state.now())):
            counted_again()
            return
    wait = state.ADMISSION_WAITS[where]

    def held(data: dict) -> None:
        job = data["jobs"][job_id]
        job.update(waits=list(dict.fromkeys([*job["waits"], wait])), admission={"why": why, "argv": argv, "at": state.stamp()})
        if text:
            state.hold_text(job, text, state.held_sender(data))

    state.write(name, held)
    raise state.DriveError(f"{job_id}: not launched yet: {why}, so it waits on {wait} and the watch runs drive {argv[0]} {argv[1]} {job_id} once this Mac admits it")


def cap_of(where: str, priority: bool) -> int:
    """How many lanes where may hold across this Mac's drives before a job of its class waits: the priority class, incidents and his priority orders, by preempts, under the higher cap caps.json sets under "priority", every other job under the cap it sets at its top level."""
    held = caps()
    return int(held["priority"][where] if priority else held[where])


def admitted_room(where: str, moment: datetime, priority: bool = False) -> int:
    """How many more lanes of where's cap for priority's class every drive on this Mac may start now: none while a local one finds this Mac strained, which holds no priority job."""
    if where == "local" and not priority and strained():
        return 0
    return max(cap_of(where, priority) - len(lanes(moment)[where]), 0)


def woken(data: dict, job_id: str, moment: datetime) -> str | None:
    """Why a message may not wake the builder's agent on this Mac: its turn ended, so the message starts it again, and builder_cap gives it no room, so the resume hands it to a lane that waits its turn by await_admission; one marked with --builds-nothing on drive job add or drive job set takes it at once, as the refusal says, since its work holds no checkout or compiler."""
    job = data["jobs"][job_id]
    if job["kind"] != "builder" or job.get("where") == "remote" or not job.get("agent") or job.get("builds_nothing") or job_id in local_builders(data, moment):
        return None
    return f"its turn ended and {why}" if (why := builder_cap(data, job_id, moment)) else None


def builder_cap(data: dict, job_id: str | None, moment: datetime) -> str | None:
    """Why a builder of the job takes no room on this Mac now, None when it may: this Mac's admission, then the cap of LOCAL_BUILDERS builders at work here, by local_builders, its own not counted, which work.room holds a compiling kind's workers to; one preempts lets through waits only on admission's priority cap, and one that builds nothing, by state.builds_nothing, holds no room and starts at once."""
    job = data["jobs"].get(job_id) if job_id else None
    if why := admission(data, job_id, "local", moment):
        return why
    if job and (preempts(data, job) or state.builds_nothing(job)):
        return None
    others = {other: line for other, line in local_builders(data, moment).items() if other != job_id}
    return at_cap(others) if len(others) >= LOCAL_BUILDERS else None


def on_sprite(job: dict) -> bool:
    """Whether the job's lane runs on a Sprite, so a start here moves it from there."""
    return job.get("where") == "remote" and bool(job.get("dispatch")) and not state.on_mac(job)
