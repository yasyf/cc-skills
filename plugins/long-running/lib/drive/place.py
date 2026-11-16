import contextlib
import ctypes
import fcntl
import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from datetime import datetime

from drive import agents, brief, orca, spend, state, view, watch
from drive.render import when

LIMIT_ENV = "HOOKS_ADMISSION_LOAD_PER_CORE"
MONOREPO = "Forge-AI/monorepo"
LOCAL_BUILDERS = 8
CAP_RULE = "on builders that compile here, where a staging tester, a merged PR's prover or another builder marked --builds-nothing holds no slot"
MAC_LINES = "its Mac: line, which drive job brief --mac writes, or its Release: line"
SAMPLE_SECONDS = 1.0
HOST_CPU_LOAD_INFO = 3
HOST_CPU_STATES = 4
HOST_CPU_IDLE = 2
CAPS_FILE = "caps.json"
CAPS = {"local": 12, "sprite": 30, "handlers": 8, "handler_pool": 4, "load_per_core": 1, "free_memory": 15}
CAP_NAMES = {"local": "local Claude lanes work", "sprite": "Sprite lanes run", "handlers": "handler sessions work"}
LANES_FILE = "lane-counts.json"
LANES_SECONDS = 20
ADMISSION_LOCK = "admission.lock"
TURN_SECONDS = 30
REPORT_TURN_SECONDS = 60


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
    """Where the next monorepo builder starts, and why."""
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
    """The builders holding a slot on this Mac, each job beside its order and its agent's or lane's last write: a job here the agent_brief hook let start or the watch's FREE line gave its turn for a slot whose agent has not started yet, a job on a Sprite, or one taking over from its agent here, whose turn to move here came within the time a launch takes and whose lane here has not started, or whose lane here launches, an agent here still in its turn, or an Orca lane started with --local that launches, or whose session in the worktree its launch recorded, by watch.lane_transcript, has not started or is still in its turn. A builder that builds nothing, by state.builds_nothing, such as a merged PR's live proof or a staging tester, compiles nothing here, so it holds no slot."""
    return {
        job_id: f"{job_id} on {job['order']}, {work}"
        for job_id, job in data["jobs"].items()
        if job["kind"] == "builder" and not state.builds_nothing(job) and job["state"] in ("running", "waiting") and (work := at_work_here(job_id, job, data, moment))
    }


def at_work_here(job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    if moves_here(job):
        move = "move here from its Sprite" if on_sprite(job) else f"take over from its agent {job['agent']}"
        return f"its turn to {move} came at {when(slot, seconds=True)}, its lane here not started yet" if (slot := job.get("slot")) and moment - state.parse_time(slot) < watch.RELAUNCH else None
    if job.get("where") != "remote" and not job["agent"] and watch.launching(job, moment):
        return f"its agent launching since {when(job['launching'], seconds=True)}"
    if job.get("where") != "remote" and job["agent"] and state.on_mac(job) and watch.launching(job, moment):
        return f"its Orca lane launching since {when(job['launching'], seconds=True)}, taking over from its agent {job['agent']}"
    if job["state"] == "waiting" and not watch.launching(job, moment):
        return "freed to start here, its agent not started yet" if job.get("slot") and not job["waits"] and not job["agent"] and job.get("where") != "remote" else None
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


def waiting_builders(data: dict) -> list[str]:
    """The builder jobs waiting their turn for a slot on this Mac, a priority order's first, by state.first_in_line, then by where its project and workstream stand, by ranked, then the oldest added; a job moving here from its Sprite waits in whatever state its lane there left it."""
    waiting = [
        job_id
        for job_id, job in data["jobs"].items()
        if state.SLOT_WAIT in job["waits"] and (job["state"] == "waiting" or on_sprite(job) and job["state"] not in state.FINISHED_JOB_STATES)
    ]
    held = standings(data)
    return sorted(waiting, key=lambda job_id: (not state.first_in_line(data, data["jobs"][job_id]), *ranked(data, data["jobs"][job_id], held), state.parse_time(data["jobs"][job_id]["created"])))


def positions(data: dict) -> dict[str, str]:
    """Each builder job waiting its turn for a slot on this Mac, with its place in line, and, for one whose workstream ranks below the first in line's, that its workstream is paused behind that one's until room returns."""
    line = waiting_builders(data)
    return {job_id: f"{number} of {len(line)} in line{paused(data, job_id, line)}" for number, job_id in enumerate(line, 1)}


def standings(data: dict) -> dict[tuple[str | None, str | None], float]:
    """How each project, as (project, None), and each of its workstreams, as (project, workstream), stands for a turn when a cap is full, lower first: by his newest move on an open order filed there, so the workstream he is driving now holds its turns and the one he touched longest ago pauses first, by his words "if we are hitting limits, the lowest priority workstreams or even projects should just straight be paused"."""
    held: dict[tuple[str | None, str | None], float] = {}
    for order in state.open_orders(data).values():
        stands = -state.parse_time(order["updated"]).timestamp()
        for key in ((order["project"], None), (order["project"], order["workstream"])):
            held[key] = min(held.get(key, stands), stands)
    return held


def ranked(data: dict, job: dict, held: dict[tuple[str | None, str | None], float]) -> tuple:
    """Where the job's project and workstream stand for a turn, lower first: the project's place in the ranks list caps.json sets, highest first, an unlisted one after every listed one, then the project's standing, then its workstream's, so a full cap pauses the lowest workstream first and a whole project once its best workstream ranks lowest."""
    order = data["orders"][job["order"]]
    ranks = caps().get("ranks", [])
    key = (order["project"], order["workstream"])
    return (ranks.index(key[0]) if key[0] in ranks else len(ranks), held[(key[0], None)], held[key]) if key in held else (len(ranks),)


def paused(data: dict, job_id: str, line: list[str]) -> str:
    held = standings(data)
    job, first = data["jobs"][job_id], data["jobs"][line[0]]
    if preempts(data, job) or ranked(data, job, held) <= ranked(data, first, held):
        return ""
    order, ahead = data["orders"][job["order"]], data["orders"][first["order"]]
    filed = " / ".join(filter(None, (order["project"], order["workstream"]))) or "its work"
    return f", {filed} paused behind {' / '.join(filter(None, (ahead['project'], ahead['workstream']))) or 'higher-ranked work'} until a slot frees"


def caps() -> dict[str, float]:
    """The caps every drive and watch on this Mac shares on the Claude lanes it starts, read at each admission so a change holds at once: how many local Claude lanes, of any role, work at once, how many Sprite lanes run, how many handler sessions work across every drive and how many pool handlers each drive keeps warm, the 1-minute load per core and the percent of memory free past which no local lane starts, CAPS unless the owner's caps.json under the state root sets any of them, as {"local": 12, "sprite": 30, "handlers": 8, "handler_pool": 4, "load_per_core": 1, "free_memory": 15}."""
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


def handler_lanes(data: dict) -> dict[str, str]:
    """The drive's handler sessions whose turn is not over, by agents.gone, which hold the handlers cap of their own pool and never the local cap, so a full local cap never holds back a post he waits on."""
    return {f"handler {handler}": f"handler {handler}, its session {entry['session']}" for handler, entry in data["handlers"].items() if entry["session"] and not agents.gone(entry)}


def sprite_lanes(data: dict, moment: datetime) -> dict[str, str]:
    """The drive's lanes running on a Sprite, each beside what it is doing: each running job whose live lane is on one, each launching its first, and each whose turn for one came within the time a launch takes, its lane not started yet."""
    return {
        job_id: f"{job_id} {job['kind']} on {job['order']}, {work}"
        for job_id, job in data["jobs"].items()
        if job["state"] not in state.FINISHED_JOB_STATES
        and (
            work := "its Sprite lane at work"
            if job["state"] == "running" and on_sprite(job) and not state.lane_death(job, "dispatch")
            else f"its Sprite lane launching since {when(job['launching'], seconds=True)}"
            if not state.on_mac(job) and not job.get("dispatch") and job.get("where") == "remote" and watch.launching(job, moment)
            else f"its turn for a Sprite lane came at {when(slot, seconds=True)}, its lane not started yet"
            if job["state"] == "waiting" and not job.get("dispatch") and job.get("where") == "remote" and (slot := job.get("sprite_turn")) and moment - state.parse_time(slot) < watch.RELAUNCH
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
    counted: dict[str, dict[str, str]] = {"local": {}, "sprite": {}, "handlers": {}}
    for name in state.names():
        data = state.load(name)
        counted["local"] |= {f"{name}/{key}": line for key, line in local_lanes(data, moment).items()}
        counted["sprite"] |= {f"{name}/{key}": line for key, line in sprite_lanes(data, moment).items()}
        counted["handlers"] |= {f"{name}/{key}": line for key, line in handler_lanes(data).items()}
    made = path.with_name(f"{LANES_FILE}.{os.getpid()}")
    made.write_text(json.dumps({"at": state.stamp(moment), "lanes": counted}))
    os.replace(made, path)
    return counted


def counted_again() -> None:
    """Drop the kept count of lanes, so the next admission counts afresh, as each start an admission let through makes it stale."""
    (state.root() / LANES_FILE).unlink(missing_ok=True)


def preempts(data: dict, job: dict) -> bool:
    """Whether a job starts past every cap, gate and daily budget on this Mac: one marked urgent alone by drive job add or set --priority, one serving an open priority order, by state.first_in_line, or an open incident, or carrying an immediate fix part, by his words on a priority order, "they aren't slowed down at any cost by anything"."""
    orders = [job["order"], *job["carries"]]
    return bool(job.get("priority")) or state.first_in_line(data, job) or bool(state.open_incidents(data, orders)) or any(state.IMMEDIATE_FIX in state.job_parts(job, order_id) for order_id in orders)


def admission(data: dict, job_id: str | None, where: str, moment: datetime) -> str | None:
    """Why no new Claude lane starts now, where "local" for a lane on this Mac of any role and "sprite" for one on a Sprite, None when it may: one of a job preempts lets through starts at once; a job whose project spent its daily budget waits for the next day, by spend.over; a local one waits while strained reads this Mac loaded or short of memory, and either while the lanes every drive here runs fill its cap by caps, the job's own lane not counted. Its work waits its turn, never fails. A handler, where "handlers", has a pool of its own, the handlers cap, which the load gate closes and the local cap never touches."""
    job = data["jobs"].get(job_id) if job_id else None
    if job and preempts(data, job):
        return None
    if job and (why := spend.over(data, job, moment)):
        return why
    if where != "sprite" and (why := strained()):
        return why
    cap = caps()[where]
    held = {key: line for key, line in lanes(moment)[where].items() if not job_id or key.rpartition("/")[2] != job_id}
    if len(held) < cap:
        return None
    shown = "; ".join(list(held.values())[:5])
    return f"{len(held)} {CAP_NAMES[where]} across this Mac's drives, at its cap of {cap} set by {state.root() / CAPS_FILE} ({shown}{'; …' if len(held) > 5 else ''})"


class Unadmitted(state.DriveError):
    pass


@contextlib.contextmanager
def waited_turn(name: str, waiting: Callable[[], None], exempt: bool = False, within: float | None = None) -> Iterator[None]:
    """Hold this Mac's admission lock once it admits a new handler session of the drive, by admission on the handlers pool, which the load gate closes and the local cap never does, calling waiting and reading again every TURN_SECONDS while it does not, so the start in the body waits its turn rather than failing, and drop the kept count once it started; an exempt start, a handler answering a message of his, runs at once."""
    if exempt:
        yield
        counted_again()
        return
    while True:
        with admitting():
            if not admission(state.load(name), None, "handlers", state.now()):
                yield
                counted_again()
                return
        if within is not None and time.time() >= within:
            raise Unadmitted(f"this Mac admitted no handler before its {REPORT_TURN_SECONDS}s turn ran out")
        waiting()
        time.sleep(TURN_SECONDS if within is None else max(0.0, min(TURN_SECONDS, within - time.time())))


def await_admission(name: str, job_id: str, where: str) -> None:
    """Return once this Mac admits a lane of the drive's job where it starts next, "local" or "sprite", by admission, reading again every TURN_SECONDS while it does not and saying on stderr once why it waits, so a relaunch or takeover, which adds a lane in place of one that ended or moved, waits its turn in the process that starts it rather than failing; one preempts lets through returns at once."""
    told = False
    while True:
        with admitting():
            if not (why := admission(state.load(name), job_id, where, state.now())):
                counted_again()
                return
        if not told:
            print(f"{job_id}: waits its turn for a {'lane here' if where == 'local' else 'Sprite lane'}: {why}", file=sys.stderr, flush=True)
            told = True
        time.sleep(TURN_SECONDS)


def admitted_room(where: str, moment: datetime) -> int:
    """How many more lanes of where's cap every drive on this Mac may start now: none while a local one finds this Mac strained."""
    if where == "local" and strained():
        return 0
    return max(int(caps()[where]) - len(lanes(moment)[where]), 0)


def sprite_line(data: dict) -> list[str]:
    """The jobs waiting their turn for a Sprite lane under this Mac's cap, a priority order's first, by state.first_in_line, then by ranked, then the oldest added, as waiting_builders orders builders, less each whose project spent its daily budget, by spend.over, which waits for the next day."""
    moment = state.now()
    waiting = [job_id for job_id, job in data["jobs"].items() if state.SPRITE_WAIT in job["waits"] and job["state"] == "waiting" and (preempts(data, job) or not spend.over(data, job, moment))]
    held = standings(data)
    return sorted(waiting, key=lambda job_id: (not state.first_in_line(data, data["jobs"][job_id]), *ranked(data, data["jobs"][job_id], held), state.parse_time(data["jobs"][job_id]["created"])))


def sprite_turns(data: dict, moment: datetime) -> tuple[list[str], int]:
    """The jobs whose turn for a Sprite lane comes now, and how many run: each one preempts lets past every cap at once, as admission lets its launch through, so a job marked priority after it queued never waits out a full cap, then one for each lane under the cap no drive here runs."""
    if not (line := sprite_line(data)):
        return [], 0
    first = [job_id for job_id in line if preempts(data, data["jobs"][job_id])]
    rest = [job_id for job_id in line if job_id not in first]
    return [*first, *rest[: admitted_room("sprite", moment)]], len(lanes(moment)["sprite"])


def queue_sprite(job: dict, moment: datetime) -> None:
    """Hold a job whose first lane the Sprite cap refused waiting its turn for one on sprite-slot, so the watch's FREE line names it as a lane frees."""
    job.update(state="waiting", waits=list(dict.fromkeys([*job["waits"], state.SPRITE_WAIT])), sprite_turn=None, said="waits its turn for a Sprite lane under this Mac's cap", updated=state.stamp(moment))


def crowded(data: dict, job_id: str | None, moment: datetime) -> str | None:
    """Why the builder job takes no slot on this Mac now, None when it may: this Mac is loaded or short of memory or its local Claude lanes fill their machine-wide cap, by admission, which a builder that builds nothing waits on too, since it is a Claude session here, the cap's builders hold every slot but its own, or builders wait their turn ahead of a job that holds none, since the watch's FREE line gives each open slot to the earliest due. A builder on an open incident's order, on an open priority order, by state.first_in_line, or on an immediate fix part starts at once past both, counting against the cap for later builders once it runs, by his pick "Start at once past the cap" and his words on a priority order, "they aren't slowed down at any cost by anything", and one that builds nothing starts at once past both, holding no slot."""
    job = data["jobs"].get(job_id)
    if job and preempts(data, job):
        return None
    if why := admission(data, job_id, "local", moment):
        return why
    if job and state.builds_nothing(job):
        return None
    builders = local_builders(data, moment)
    others = {other: line for other, line in builders.items() if other != job_id}
    if len(others) >= LOCAL_BUILDERS:
        return at_cap(others)
    if job_id not in builders and (line := [other for other in waiting_builders(data) if other != job_id]):
        return f"builders already wait their turn for a slot on this Mac ahead of it, earliest due first ({', '.join(line)})"
    return None


def woken(data: dict, job_id: str, moment: datetime) -> str | None:
    """Why a message may not wake the builder's agent on this Mac: its turn ended, so the message starts it again, and crowded gives it no slot; one marked with --builds-nothing on drive job add or drive job set takes it at once, as the refusal says, since its work holds no checkout or compiler."""
    job = data["jobs"][job_id]
    if job["kind"] != "builder" or job.get("where") == "remote" or not job.get("agent") or job.get("builds_nothing") or job_id in local_builders(data, moment):
        return None
    return f"its turn ended and {why}" if (why := crowded(data, job_id, moment)) else None


def queueable(data: dict, job_id: str) -> bool:
    """Whether a builder job may wait its turn for a slot on this Mac: one that no agent or lane runs, not finished, and that nothing else holds, no wait, card of his or open PR of its, so the line never frees work held on something else."""
    job = data["jobs"][job_id]
    return (
        job["kind"] == "builder"
        and job["state"] not in state.FINISHED_JOB_STATES
        and not job.get("agent")
        and not job.get("dispatch")
        and not job["waits"]
        and not view.open_cards(job)
        and not any(pr["job"] == job_id and pr["state"] == "open" for pr in data["prs"].values())
    )


def queue(job: dict, moment: datetime) -> None:
    """Hold a builder job waiting its turn for a slot on this Mac on builder-slot: one no agent or lane runs, or one taking over here from its agent on this Mac, waiting, so the watch reads it waiting, never abandoned, until its FREE line names it or its turn starts the takeover, and one moving here from its Sprite in the state its lane there set, that lane running on, until the watch's turn starts its move."""
    waits = list(dict.fromkeys([*job["waits"], state.SLOT_WAIT]))
    if on_sprite(job):
        job.update(waits=waits, slot=None, updated=state.stamp(moment))
        return
    job.update(state="waiting", where="local", waits=waits, slot=None, said="waits its turn for a builder slot on this Mac", updated=state.stamp(moment))


def on_sprite(job: dict) -> bool:
    """Whether the job's lane runs on a Sprite, so a start here moves it from there."""
    return job.get("where") == "remote" and bool(job.get("dispatch")) and not state.on_mac(job)


def moves_here(job: dict) -> bool:
    """Whether a start here takes the job over from the worker it has, so the watch's turn starts it itself with drive job launch <j> --local: its lane on a Sprite, or its agent on this Mac, held on builder-slot only by a takeover crowded_here refused at the cap, or given its turn since that agent last started."""
    if on_sprite(job):
        return True
    return job.get("where") == "local" and bool(job.get("agent")) and (state.SLOT_WAIT in job["waits"] or bool(job.get("slot")) and not state.started_after(job, job["slot"]))


def turns(data: dict, moment: datetime) -> tuple[list[str], int]:
    """The builder jobs whose turn comes now, and how many builders hold a slot on this Mac: each priority order's builder in line at once, as crowded starts one past the cap, then the earliest due, one for each slot no builder holds."""
    if not (line := waiting_builders(data)):
        return [], 0
    held = len(local_builders(data, moment))
    line = [job_id for job_id in line if preempts(data, data["jobs"][job_id]) or not spend.over(data, data["jobs"][job_id], moment)]
    first = [job_id for job_id in line if state.first_in_line(data, data["jobs"][job_id])]
    room = min(LOCAL_BUILDERS - held, admitted_room("local", moment))
    return [*first, *line[len(first) :][: max(room - len(first), 0)]], held


