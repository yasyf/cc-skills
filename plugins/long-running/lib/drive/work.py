"""The drive's work queue: items a worker pulls in compatible groups, reports one by one, and pulls again, holding its capacity only while it works; drive work enable turns a kind over to it, one kind at a time."""

import uuid
from datetime import datetime, timedelta
from pathlib import Path

from drive import agents, orca, place, reads, state, view

STAGE_TEST = "stage-test"
KINDS = (STAGE_TEST,)
WORKER = "worker"
ENV = state.STAGING_ENV
GROUP = state.PACKED_CUT
WORKER_ITEMS = 18
READS_PER_PASS = 12
CUT_MINUTES = 45
STALL_AFTER = timedelta(minutes=10)
WORKER_GOAL = "pull and run stage-test items on {env}, one cut a group"
WORKER_BRIEF = """# Brief

You are {worker}, the drive's stage-test worker for {env}. You run groups of staging tests, each group on one cut, report every test as its own item, then pull the next group. You hold the {env} slot only while a group of yours runs.

## The loop

1. Run `drive work pull --worker {worker}`. It prints your group: each item's job id, its brief, the PRs it tests and the cases its brief asks for, then the GO steps for the cut they share. When it prints `NONE` or `HOLD`, go to step 5.
2. Read every item's brief whole. Their cases all run on the one cut the GO line names: post one staging claim naming every PR, start one release carrying every item's PR head, and take one Mac box for the whole group when any brief asks for one.
3. Once staging serves the cut, run each item's cases as its brief says. As each item finishes, pipe its whole return, in the shape its brief asks for, CASE lines and all, into `drive work done {worker} <item> -` from a quoted heredoc. An item whose case waits on something returns `WAITING <what>` the same way; it keeps its place in the cut, and a later worker runs it there.
4. Go to step 1.
5. Give back any Mac box you took, then pipe `DONE ran <each item you reported>` into `drive job report {worker} -` and end.

Never start a second cut while `drive work pull` says your cut still holds the slot, and never report an item another worker holds.
"""


def enabled(data: dict, kind: str) -> bool:
    """Whether drive work enable turned this kind of work over to the queue on this drive."""
    return kind in data.get("work", {}).get("kinds", [])


def worker(job: dict) -> bool:
    return job.get("work") == WORKER


def kind_of(data: dict, job: dict) -> str | None:
    """The queue's kind of a job's work: a staging tester, a builder holding a `staging test of #N before it lands` part that starts no cut, is a stage-test; None for every other job."""
    if worker(job) or job["kind"] != "builder" or job["state"] in state.FINISHED_JOB_STATES:
        return None
    return STAGE_TEST if state.staging_parts(data, job) else None


def untaken(job: dict) -> bool:
    return not (job.get("agent") or job.get("dispatch") or job.get("bg") or job.get("codex_run"))


def queued(data: dict, job: dict) -> bool:
    """Whether the queue, never start_jobs or carry_held, starts this job's work: a worker, or an item of an enabled kind that no agent or lane of its own took."""
    return worker(job) or untaken(job) and (kind := kind_of(data, job)) is not None and enabled(data, kind)


def claimed_by(data: dict, job: dict) -> str | None:
    """The worker holding the item's claim, None once that worker finished, so its next pull goes to a fresh one."""
    held = (job.get("claim") or {}).get("worker")
    return held if held in data["jobs"] and data["jobs"][held]["state"] not in state.FINISHED_JOB_STATES else None


def ready(data: dict, job_id: str) -> bool:
    """Whether a stage-test item may be pulled now: queued, waiting, briefed, claimed by no live worker, no card of his open on it, nothing its start waits on holding it, by view.start_waits, and one PR it tests still open."""
    job = data["jobs"][job_id]
    return (
        queued(data, job)
        and not worker(job)
        and job["state"] == "waiting"
        and bool(job.get("brief"))
        and claimed_by(data, job) is None
        and not view.open_cards(job)
        and not [wait for wait in view.start_waits(data, job, None) if wait != (job.get("claim") or {}).get("worker")]
        and any(data["prs"].get(key, {}).get("state") == "open" for key in state.tested_prs(data, job))
    )


def rank(data: dict, job_id: str) -> tuple:
    """Where an item stands in the queue, lower first: a priority order's or an open incident's first, by state.jumps_queue, then the oldest added."""
    return (not state.jumps_queue(data, job_id), state.parse_time(data["jobs"][job_id]["created"]))


def line(data: dict) -> list[str]:
    """The stage-test items ready now, in queue order."""
    return sorted((job_id for job_id in data["jobs"] if ready(data, job_id)), key=lambda job_id: rank(data, job_id))


def our_cut(slot: dict) -> bool:
    return bool((slot.get("holder") or {}).get("work"))


def members_ready(data: dict, slot: dict) -> list[str]:
    """The items of the queue's cut on the env that a worker may run now, its tests that returned WAITING and whose waits ended."""
    return [job_id for job_id in state.slot_members(slot) if our_cut(slot) and ready(data, job_id)] if slot.get("holder") else []


def legacy_ahead(data: dict, slot: dict, head: str) -> str | None:
    """The job of the env's old first-in-first-out queue whose turn comes before the item's, by the same priority and age, so the queue and the old slot line share one ledger fairly."""
    if not slot["queue"]:
        return None
    first = slot["queue"][0]
    return first["job"] if (not state.jumps_queue(data, first["job"]), state.parse_time(first["at"])) <= rank(data, head) else None


def entry(data: dict, job_id: str, moment: datetime) -> dict:
    job = data["jobs"][job_id]
    reads = job.get("work_reads") or {}
    return {"job": job_id, "prs": state.tested_prs(data, job), "minutes": CUT_MINUTES, "cases": None, "at": state.stamp(moment), "paths": reads.get("paths"), "stacks": reads.get("stacks"), "starts": False}


def why_not(data: dict, moment: datetime) -> str | None:
    """Why no new cut of queued work takes the env now, None when one may: the slot is held, held for another job, or the old line's turn comes first."""
    slot = data["slots"].setdefault(ENV, {"holder": None, "queue": [], "hold": None})
    if slot["holder"]:
        return f"the {ENV} slot is held by {slot['holder']['job']}'s cut until {slot['holder']['until']}"
    if not (items := line(data)):
        return "no stage-test item is ready"
    if not state.held_for(data, slot, items[0], moment):
        return f"the {ENV} slot is held for {slot['hold']['for']} until {slot['hold']['until']}"
    if ahead := legacy_ahead(data, slot, items[0]):
        return f"{ahead} of the {ENV} slot's old line comes first"
    return None


def claim(data: dict, worker_id: str, items: list[str], moment: datetime, go: str) -> None:
    data["jobs"][worker_id]["work_go"] = go
    for job_id in items:
        job = data["jobs"][job_id]
        job.update(
            claim={"worker": worker_id, "attempt": uuid.uuid4().hex[:12], "at": state.stamp(moment)},
            waits=list(dict.fromkeys([*job["waits"], worker_id])),
            said=f"claimed by worker {worker_id} on the {ENV} cut",
            updated=state.stamp(moment),
        )


def take(data: dict, worker_id: str, moment: datetime) -> tuple[str, list[str], str]:
    """Claim the worker's next group, in one write: the ready items of the queue's cut on the env first, else, when why_not lets a cut start, the head of the queue and up to GROUP - 1 items behind it whose PRs change no file and reach no stack of the cut's, by state.packed, taking the slot for them; the word, the items and the line the worker reads."""
    slot = data["slots"].setdefault(ENV, {"holder": None, "queue": [], "hold": None})
    if again := members_ready(data, slot):
        go = f"GO run these on the {ENV} cut the queue already holds until {slot['holder']['until']}, starting no new cut"
        claim(data, worker_id, again, moment, go)
        return "GO", again, go
    if slot["holder"] and our_cut(slot) and any(data["jobs"][member]["state"] not in state.FINISHED_JOB_STATES for member in state.slot_members(slot)):
        waiting = ", ".join(member for member in state.slot_members(slot) if data["jobs"][member]["state"] not in state.FINISHED_JOB_STATES)
        return "HOLD", [], f"HOLD the queue's {ENV} cut still holds {waiting}, waiting on what each returned; a fresh worker runs them there once their waits end"
    if why := why_not(data, moment):
        return "NONE", [], f"NONE {why}"
    queue = [entry(data, job_id, moment) for job_id in line(data)]
    head, riders = state.packed(queue)
    slot["holder"] = state.leased(head, moment, riders) | {"work": worker_id}
    group = [head["job"], *(rider["job"] for rider in riders)]
    steps = state.SLOT_GO_STEPS.format(env=ENV, job=head["job"])
    go = f"GO staging {ENV} until {slot['holder']['until']} for {', '.join(group)}, one cut carrying {', '.join(pr for job_id in group for pr in state.tested_prs(data, data['jobs'][job_id]))}: {steps}"
    claim(data, worker_id, group, moment, go)
    return "GO", group, go


def shown(data: dict, word: str, items: list[str], said: str) -> str:
    rows = [f"- {job_id} on {data['jobs'][job_id]['order']}: brief {data['jobs'][job_id]['brief']}; tests {', '.join(state.tested_prs(data, data['jobs'][job_id]))}" for job_id in items]
    return "\n".join([said, *rows]) if word == "GO" else said


def pulled(name: str, worker_id: str) -> str:
    """drive work pull: the worker's next group, claimed, as the lines it reads."""
    moment = state.now()

    def pull_write(data: dict) -> str:
        held = data["jobs"].get(worker_id)
        if not held or not worker(held) or held["state"] in state.FINISHED_JOB_STATES:
            raise state.DriveError(f"{worker_id}: refused, it is no live worker of this drive's queue")
        if (done := len(held.get("work_done") or [])) >= WORKER_ITEMS:
            return f"NONE {worker_id} reported {done} items, its whole budget: end, and a fresh worker takes the rest"
        if mine := [job_id for job_id, job in data["jobs"].items() if (job.get("claim") or {}).get("worker") == worker_id and job["state"] == "waiting" and worker_id in job["waits"]]:
            return shown(data, "GO", mine, f"{held.get('work_go') or 'GO'}\nYou still hold {', '.join(mine)}: report each with drive work done {worker_id} <item> - before you pull again")
        word, items, said = take(data, worker_id, moment)
        return shown(data, word, items, said)

    return state.update(name, None, pull_write)


def done(name: str, worker_id: str, job_id: str, whole: str) -> tuple[str, dict]:
    """drive work done: take an item's whole return as drive job report takes it, refused unless its claim is the worker's, then end the claim and the item's wait on its worker, and give its place in the cut back when its line is DONE; a refused return leaves the claim with the worker, to fix and send again."""
    from drive import cli

    data = state.load(name)
    if not (job := data["jobs"].get(job_id)) or claimed_by(data, job) != worker_id:
        raise state.DriveError(f"{job_id}: refused, {worker_id} holds no claim on it: drive work pull --worker {worker_id} names what it holds")
    said, payload = cli.take_report(name, job_id, whole)

    def done_write(data: dict) -> None:
        data["jobs"][job_id].update(claim=None, waits=[wait for wait in data["jobs"][job_id]["waits"] if wait != worker_id])
        data["jobs"][worker_id]["work_done"] = [*(data["jobs"][worker_id].get("work_done") or []), job_id]
        slot = data["slots"].get(ENV) or {"holder": None}
        if whole.strip().startswith("DONE") and slot["holder"] and job_id in state.slot_members(slot):
            state.slot_release(data, ENV, job_id)

    state.update(name, None, done_write)
    return said, payload


def brief_path(name: str, worker_id: str) -> Path:
    return state.drive_dir(name) / "jobs" / f"{worker_id}.brief.md"


def add_worker(data: dict, name: str, moment: datetime) -> str:
    """Record a new stage-test worker job on the order of the queue's head item, a builder building nothing, briefed with WORKER_BRIEF, its first group claimed by take in the same write; its id."""
    head = members_ready(data, data["slots"].get(ENV) or {}) or line(data)
    order_id = data["jobs"][head[0]]["order"]
    worker_id = state.next_id(data["jobs"], "j")
    path = brief_path(name, worker_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(WORKER_BRIEF.format(worker=worker_id, env=ENV))
    _, held = state.holding_role(data, data["orders"][order_id])
    data["jobs"][worker_id] = state.new_job(order_id, "builder", WORKER_GOAL.format(env=ENV), [], state.stamp(moment)) | {
        "work": WORKER,
        "builds_nothing": True,
        "brief": str(path),
        "session": held.get("session"),
        "brief_flags": {flag: None for flag in state.BRIEF_FLAGS},
        "work_done": [],
    }
    take(data, worker_id, moment)
    return worker_id


def live_worker(data: dict) -> str | None:
    return next((job_id for job_id, job in data["jobs"].items() if worker(job) and job["state"] not in state.FINISHED_JOB_STATES), None)


def wanted(data: dict, moment: datetime) -> bool:
    """Whether the queue wants a stage-test worker it does not have: none lives, and ready items of its cut wait to run there or a new cut may start, by why_not."""
    if not enabled(data, STAGE_TEST) or live_worker(data):
        return False
    return bool(members_ready(data, data["slots"].get(ENV) or {})) or why_not(data, moment) is None


def start(name: str, worker_id: str) -> str:
    """Start a worker's agent as its own `claude --bg` session on this Mac, a builder on its brief, as drive job start starts a job's agent, in the drive's checkout; the session."""
    data = state.load(name)
    job = data["jobs"][worker_id]
    _, held = state.holding_role(data, data["orders"][job["order"]])
    if not held.get("session"):
        raise state.DriveError(f"{worker_id}: refused, not started: no session holds {job['order']}, so none would hear its agent")
    call = agents.shaped({"session_id": held["session"], "tool_input": {"subagent_type": state.JOB_AGENTS["builder"][0], "prompt": f"Read your brief first: {job['brief']}"}})
    agent, cwd = uuid.uuid4().hex[:17], orca.checkout(name, held["cwd"])

    def start_write(data: dict) -> None:
        data["jobs"][worker_id]["bg"] = {"agent": agent, "session": None, "started": state.stamp(), "cwd": str(cwd), "pointed": []}

    state.update(name, None, start_write)
    try:
        session = agents.start_job(name, worker_id, agent, call, held, cwd)
    except (reads.ReadError, state.DriveError) as error:

        def failed_write(data: dict) -> None:
            data["jobs"][worker_id].update(bg=None, state="failed", said=f"claude --bg started no agent: {error}", updated=state.stamp())

        state.update(name, None, failed_write)
        raise state.DriveError(f"{worker_id}: refused, not started: claude --bg started no agent: {error}") from None

    def started_write(data: dict) -> None:
        data["jobs"][worker_id]["bg"]["session"] = session
        data["jobs"][worker_id].update(agent=agent, launching=None)
        state.start(data["jobs"][worker_id])

    state.update(name, None, started_write)
    return session


def unread(data: dict) -> list[str]:
    """The ready items whose files and stacks the queue has not read, at most READS_PER_PASS, so packing never pairs a pass whose files it cannot see."""
    return [job_id for job_id in line(data) if not data["jobs"][job_id].get("work_reads")][:READS_PER_PASS]


def admitted(data: dict, moment: datetime) -> str | None:
    """Why this Mac takes no new worker now, by place.admission for a local lane, None when it may, or when the queue's head serves a priority order or an open incident, by state.jumps_queue, which starts past every cap."""
    head = members_ready(data, data["slots"].get(ENV) or {}) or line(data)
    if head and state.jumps_queue(data, head[0]):
        return None
    return place.admission(data, None, "local", moment)


def stalled(data: dict, job_id: str, moment: datetime) -> str | None:
    """Why the queue leaves an item it holds with no one, None while a worker holds it, its waits hold it, or the env's slot or the old line keeps a new cut from starting: a ready item STALL_AFTER since its last change while no worker lives and a cut may start, which the spawner should have answered."""
    job = data["jobs"][job_id]
    if worker(job) or not ready(data, job_id) or live_worker(data) or why_not(data, moment) or admitted(data, moment) or moment - state.parse_time(job["updated"]) < STALL_AFTER:
        return None
    return f"{job_id} is ready in the work queue {int((moment - state.parse_time(job['updated'])).total_seconds() // 60)} minutes with no worker while a cut may start: drive work show says why none started"


def drop_legacy(data: dict) -> list[str]:
    """Take each item the queue runs off the staging slot's old first-in-first-out line, where a lease that ended before its cut started, or the watch's own queue_waiters, would put it, so only a worker of the queue runs it."""
    slot = data["slots"].get(ENV)
    if not slot or not (ours := [each["job"] for each in slot["queue"] if queued(data, data["jobs"][each["job"]])]):
        return []
    slot["queue"] = [each for each in slot["queue"] if each["job"] not in ours]
    return [f"WORK took {', '.join(ours)} off the {ENV} slot's old line into the queue"]
