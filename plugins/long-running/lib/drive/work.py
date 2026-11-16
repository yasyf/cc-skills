"""The drive's work queue: items a worker pulls in compatible groups, reports one by one, and pulls again, holding its capacity only while it works; every kind it runs runs there on every drive, its old start path gone."""

import uuid
from datetime import datetime, timedelta
from pathlib import Path

from drive import agents, orca, place, providers, reads, state, view, watch
from drive.render import when

STAGE_TEST = "stage-test"
PROVE = "prove"
BUILD = "build"
RESEARCH = "research"
DESIGN = "design"
TOLD = ("ASK", "BLOCKED")
WORKER = "worker"
WORKER_ITEMS = 18
WORKERS_PER_PASS = 4
POOLS = ("local", "remote")
READS_PER_PASS = 12
CUT_SHAPE = {"cut_items": 6, "cut_minutes": 30, "train_minutes": 60, "promotion_minutes": 30, "ci_minutes": 20, "start_moves": 10, "cut_steps": ""}
CUT_STEPS = "start one cut on {env} of the items' PR heads through the release provider, then run the items' cases once staging serves it"
STALL_AFTER = timedelta(minutes=10)
DONE_WHEN = "drive work pull --worker {worker} prints NONE or HOLD and every item it gave you is reported; return DONE ran <each item you reported>"


def untaken(job: dict) -> bool:
    return not (job.get("agent") or job.get("dispatch") or job.get("bg") or job.get("codex_run"))


def worker(job: dict) -> bool:
    return job.get("work") == WORKER


def env(data: dict) -> str | None:
    """The staging env whose slot the queue's cuts hold, the repository's staging provider's."""
    return providers.of(data).staging.env


def cut_shape(data: dict) -> dict:
    """How big a staging cut the queue's packer forms and how it runs: at most cut_items tests planned at cut_minutes, a release's lease of train_minutes, and the repository's own cut_steps after the plugin's, from the repository's [work] table in its .claude/long-running.toml, these defaults for a key it leaves out, so the drive holds shared staging for one short cut at a time."""
    return CUT_SHAPE | (providers.of(data).config.get("work") or {})


def free_at(data: dict, moment: datetime) -> datetime:
    """When the next cut can take the env: the end of the lease holding it, then each release the train holds for it at train_minutes, then promotion_minutes when its promotion skipped its last tick, all from the toml's [work]; mechanics on rows the drive holds, no read."""
    shape, slot = cut_shape(data), STAGE.slot(data)
    free = max(moment, state.parse_time(slot["holder"]["until"])) if slot["holder"] else moment
    free += timedelta(minutes=shape["train_minutes"]) * len(state.train_waiting(data, env(data)))
    return free + (timedelta(minutes=shape["promotion_minutes"]) if slot.get("promotion_due") else timedelta())


def ready_at(data: dict, job_id: str, moment: datetime) -> datetime | None:
    """When a queued stage-test item should be ready to pull: now when it is, ci_minutes on when all that holds it is its own PRs' checks still running, None when it waits on anything else, which no row times."""
    if STAGE.ready(data, job_id):
        return moment
    waits = STAGE.blocking(data, job_id)
    if waits and all(wait in data["prs"] and data["prs"][wait]["checks"] == "pending" for wait in waits):
        return moment + timedelta(minutes=cut_shape(data)["ci_minutes"])
    return None


def planned(data: dict, moment: datetime) -> dict[str, tuple[datetime, datetime]]:
    """When each stage-test item's cut starts and ends: the cut on the env now keeps its lease; the rest are packed in queue order into cuts of cut_items each lasting cut_minutes from free_at, an item about to be ready, by ready_at, joining the first cut that starts after it is, never holding back a cut whose head is ready, so a cut fills with what will be ready by its start; an item no row times gets none."""
    shape, slot = cut_shape(data), STAGE.slot(data)
    plan = {member: (state.parse_time(slot["holder"]["since"]), state.parse_time(slot["holder"]["until"])) for member in state.slot_members(slot)} if STAGE.ours(data) else {}
    queued = sorted((job_id for job_id, job in data["jobs"].items() if job_id not in plan and STAGE.queued(data, job)), key=lambda job_id: rank(data, job_id))
    ready = {job_id: at for job_id in queued if (at := ready_at(data, job_id, moment))}
    start, length = free_at(data, moment), timedelta(minutes=shape["cut_minutes"])
    while ready:
        if not (here := [job_id for job_id in queued if job_id in ready and ready[job_id] <= start]):
            start = min(ready.values())
            continue
        first, riders = state.packed([STAGE.entry(data, job_id, moment) for job_id in here], shape["cut_items"])
        for job_id in [first["job"], *(rider["job"] for rider in riders)]:
            plan[job_id] = (start, start + length)
            ready.pop(job_id)
        start += length
    return plan


def scheduled(data: dict, moment: datetime) -> None:
    """Keep each queued stage-test item's planned start and end on its row, writing only what moved, and owe each ask's thread its staging start once, by his question whether batching got lost: one step per order, the earliest start and the latest end across its items, told by the thread's voice, by his one-voice ruling, again only when either moves by start_moves or more; an item no row times keeps none, and a process order owes none."""
    from drive import owing

    plan, moves = planned(data, moment), timedelta(minutes=cut_shape(data)["start_moves"])
    asks: dict[str, list[tuple[datetime, datetime]]] = {}
    for job_id, job in data["jobs"].items():
        if not STAGE.queued(data, job):
            continue
        times = tuple(map(state.stamp, plan[job_id])) if job_id in plan else (None, None)
        if (job.get("starts_at"), job.get("ends_at")) != times:
            job.update(starts_at=times[0], ends_at=times[1])
        if job_id in plan and state.class_rank(data, job) != state.CLASSES.index("process"):
            asks.setdefault(job["order"], []).append(plan[job_id])
    for order_id, spans in asks.items():
        order = data["orders"][order_id]
        starts, ends = min(start for start, _ in spans), max(end for _, end in spans)
        if (said := order.get("start_said")) and abs(state.parse_time(said[0]) - starts) < moves and abs(state.parse_time(said[1]) - ends) < moves:
            continue
        order["start_said"] = [state.stamp(starts), state.stamp(ends)]
        what = f"tell his thread its staging test starts about {when(order['start_said'][0])} and should end by {when(order['start_said'][1])}"
        if held := owing.open_on(order, owing.STEP, {"start": order_id}):
            held["what"] = what
        else:
            owing.add(data, order_id, owing.STEP, what, moment + owing.BLOCKED_DUE, {"start": order_id}, "its staging start", moment, voice=True)


def wait_eta(data: dict, wait: str, moment: datetime) -> tuple[str, datetime | None]:
    """What a queued item waits on, in words, and when that should end if a row knows: a PR's checks still running at ci_minutes on, a job holding a staging lease at its end, a release on the train at the next cut's free_at plus train_minutes, a job's clock at its due; None when no row times it."""
    if wait in data["prs"] and data["prs"][wait]["checks"] == "pending":
        return f"#{wait.split('#')[1]}'s checks", moment + timedelta(minutes=cut_shape(data)["ci_minutes"])
    if job := data["jobs"].get(wait):
        if held := next((slot["holder"] for slot in data["slots"].values() if slot["holder"] and slot["holder"]["job"] == wait), None):
            return wait, state.parse_time(held["until"])
        if job.get("train"):
            return wait, free_at(data, moment) + timedelta(minutes=cut_shape(data)["train_minutes"])
        if job.get("due"):
            return wait, state.parse_time(job["due"])
    return wait, None


def untimed(data: dict, job_id: str, moment: datetime) -> str:
    """Why a queued item has no planned start, never bare: each wait it holds and, where a row knows, when that ends."""
    said = []
    for wait in STAGE.blocking(data, job_id) or [data["jobs"][job_id]["state"]]:
        what, ends = wait_eta(data, wait, moment)
        said.append(f"after {what}, about {when(state.stamp(ends))}" if ends else f"waits on {what}, no estimate")
    return "; ".join(said)


def claimed_by(data: dict, job: dict) -> str | None:
    """The worker holding the item's claim, None once that worker finished, so its next pull goes to a fresh one."""
    held = (job.get("claim") or {}).get("worker")
    return held if held in data["jobs"] and data["jobs"][held]["state"] not in state.FINISHED_JOB_STATES else None


def placed(data: dict, job: dict) -> str:
    """Where a worker runs an item: on this Mac, "local", when the lines its brief flags give it today declare a step only this Mac runs, by cli.mac_lines, the rule drive job launch places a lane by, such as a Mac box's or drive job prove's, else on a Sprite, "remote", which keeps this Mac's load down."""
    from drive import cli

    flags = dict.fromkeys(state.BRIEF_FLAGS) | (job.get("brief_flags") or {})
    return "local" if any(cli.mac_lines(data["name"], job, flags)) else "remote"


def rank(data: dict, job_id: str) -> tuple:
    """Where an item stands in the queue, lower first: its class, by state.class_rank, then one place.preempts lets past the caps, a --priority job, an urgent part or an immediate fix among them, so an urgent item heads its pool and the spawner sizes its room by the priority cap, then a priority order's or an open incident's, by state.jumps_queue, then the oldest added."""
    job = data["jobs"][job_id]
    return (state.class_rank(data, job), not place.preempts(data, job), not state.jumps_queue(data, job_id), state.parse_time(job["created"]))


def carried(data: dict, job_id: str, moment: datetime) -> None:
    """Put each text an item holds, his answers among them, into its brief before a worker reads it, by cli.answers_since, the way a launch briefs a job's first lane, and mark them gone to it, since carry_held never starts a queued item and no lane of its own would carry them."""
    from drive import cli, superseded

    job = data["jobs"][job_id]
    written = Path(job["brief"]).read_text()
    if (briefed := cli.answers_since(data, job_id, written)) != written:
        Path(job["brief"]).write_text(briefed)
    state.went(data["name"], data, job_id, job["undelivered"], f"in the brief a worker of the work queue pulled at {when(state.stamp(moment))}")
    superseded.hear(data, job_id, briefed, moment)


def claim(data: dict, worker_id: str, items: list[str], moment: datetime, go: str) -> None:
    data["jobs"][worker_id]["work_go"] = go
    for job_id in items:
        carried(data, job_id, moment)
        job = data["jobs"][job_id]
        job.update(
            claim={"worker": worker_id, "attempt": uuid.uuid4().hex[:12], "at": state.stamp(moment)},
            waits=list(dict.fromkeys([*job["waits"], worker_id])),
            said=f"claimed by worker {worker_id} of the work queue",
            updated=state.stamp(moment),
        )


class Kind:
    """One kind of work the queue runs: which jobs are its items, when one is still worth running, what keeps a new group from starting, how a worker takes its next group, and the loop its worker's brief gives it."""

    name = ""
    group = 6
    goal = ""
    loop = ""
    agent = "builder"
    compiles = False
    free_here = False
    told: tuple[str, ...] | None = TOLD
    most: int | None = None

    def matches(self, data: dict, job: dict) -> bool:
        raise NotImplementedError

    def where(self, data: dict, job: dict) -> str:
        """Where a worker runs an item of this kind, by placed."""
        return placed(data, job)

    def worth(self, data: dict, job: dict) -> bool:
        return True

    def adopted(self, job: dict) -> bool:
        return False

    def queued(self, data: dict, job: dict) -> bool:
        """Whether the queue, never carry_held, starts this job's work: an item of this kind that no agent or lane of its own took or that the queue adopted, on an order no communicator holds, whose handler starts its own work."""
        return (untaken(job) or self.adopted(job)) and job["state"] not in state.FINISHED_JOB_STATES and self.matches(data, job) and data["orders"][job["order"]].get("owner") != "communicator"

    def ready(self, data: dict, job_id: str) -> bool:
        """Whether an item may be pulled now: queued, waiting or built, as a build that returned BUILT waiting on his card is once the card is answered, briefed, no drive job launch of it in flight, by watch.launching, so no worker claims a job a launch is starting, claimed by no live worker, no card of his open on it, nothing its start waits on holding it, by view.start_waits, and still worth running."""
        job = data["jobs"][job_id]
        return (
            not worker(job)
            and self.queued(data, job)
            and job["state"] in ("waiting", "built")
            and bool(job.get("brief"))
            and not watch.launching(job, state.now())
            and claimed_by(data, job) is None
            and not view.open_cards(job)
            and not self.blocking(data, job_id)
            and self.worth(data, job)
        )

    def blocking(self, data: dict, job_id: str) -> list[str]:
        """The waits holding an item's start, by view.start_waits, but its own claiming worker's."""
        job = data["jobs"][job_id]
        return [wait for wait in view.start_waits(data, job, None) if wait != (job.get("claim") or {}).get("worker")]

    def line(self, data: dict) -> list[str]:
        """The items of this kind ready now, in queue order."""
        return sorted((job_id for job_id in data["jobs"] if self.ready(data, job_id)), key=lambda job_id: rank(data, job_id))

    def resumed(self, data: dict) -> list[str]:
        """Items a worker runs again where they ran before, ahead of the line; none for a kind that holds nothing between pulls."""
        return []

    def pool(self, data: dict, where: str) -> list[str]:
        """The items a worker running where may take: those to run again there first, else the line's items placed there."""
        if (again := self.resumed(data)) and self.where(data, data["jobs"][again[0]]) == where:
            return again
        return [job_id for job_id in self.line(data) if self.where(data, data["jobs"][job_id]) == where]

    def why_not(self, data: dict, moment: datetime) -> str | None:
        return None if self.line(data) else f"no {self.name} item is ready"

    def held(self, data: dict, worker_id: str) -> str | None:
        """Why a worker of this kind may pull nothing new while work of the queue's still stands: none for a kind holding nothing between pulls."""
        return None

    def reclaimed(self, data: dict, worker_id: str) -> None:
        """What a worker taking the items another worker ran before takes over with them: nothing for a kind holding nothing between pulls."""

    def group_of(self, data: dict, worker_id: str, items: list[str], moment: datetime) -> tuple[list[str], str]:
        """The group a worker takes from the ready items placed where it runs, its head first, and the GO line it reads."""
        group = items[: self.group]
        return group, f"GO {', '.join(group)}"

    def take(self, data: dict, worker_id: str, moment: datetime) -> tuple[str, list[str], str]:
        """Claim the worker's next group in one write: the items to run again first, else, when why_not lets a group start, the head of the line and the items behind it placed where the worker runs, by group_of; the word, the items and the line the worker reads."""
        where = data["jobs"][worker_id]["where"]
        if (again := self.resumed(data)) and self.where(data, data["jobs"][again[0]]) != where:
            return "NONE", [], f"NONE the queue's {', '.join(again)} run {'on this Mac' if where == 'remote' else 'on a Sprite'}, so a worker there takes them: end"
        if again:
            go = data["jobs"][worker_id].get("work_go") or f"GO run these again where they ran: {', '.join(again)}"
            claim(data, worker_id, again, moment, go)
            self.reclaimed(data, worker_id)
            return "GO", again, go
        if why := self.held(data, worker_id):
            return "HOLD", [], f"HOLD {why}"
        if why := self.why_not(data, moment):
            return "NONE", [], f"NONE {why}"
        if not (line := [job_id for job_id in self.pool(data, where) if (data["jobs"][job_id].get("work_back") or {}).get("worker") != worker_id]):
            return "NONE", [], f"NONE no ready {self.name} item runs {'on this Mac' if where == 'local' else 'on a Sprite'} but those you gave back, so another worker takes the rest: end"
        group, go = self.group_of(data, worker_id, line, moment)
        claim(data, worker_id, group, moment, go)
        return "GO", group, go

    def row(self, data: dict, job_id: str) -> str:
        job = data["jobs"][job_id]
        return f"- {job_id} on {job['order']}: brief {job['brief']}{f'; its last return: drive job report {job_id}' if job.get('returned') else ''}"

    def done(self, data: dict, job_id: str, line: str) -> None:
        """What a reported item gives back beside its claim."""

    def back(self, data: dict, job_id: str) -> None:
        """What an item a worker gave back gives back beside its claim: nothing for a kind holding nothing for it."""

    def wanted(self, data: dict, where: str, moment: datetime) -> bool:
        """Whether the queue wants another worker of this kind running where: the kind holds fewer live workers than its most, none of its workers there failed to start in the last STALL_AFTER, so a failing start is tried again at that pace, and items placed there wait to run again, or wait unclaimed while a new group may start, by why_not; a worker that finished its group pulls again on its own, so a ready item no worker holds needs a new one."""
        if self.most is not None and len(live_workers(data, self.name)) >= self.most or any(
            worker(job) and job.get("work_kind") == self.name and job.get("where") == where and job["state"] == "failed" and not (job["agent"] or job.get("dispatch")) and moment - state.parse_time(job["updated"]) < STALL_AFTER for job in data["jobs"].values()
        ):
            return False
        if (again := self.resumed(data)) and self.where(data, data["jobs"][again[0]]) == where:
            return True
        return self.why_not(data, moment) is None and bool(self.pool(data, where))


class StageTest(Kind):
    """Staging tests: a builder holding a `staging test of #N before it lands` part that starts no cut, run in groups whose PRs change no file and reach no stack in common, one group a cut on the env's one slot, whose ledger the old first-in-first-out line shares."""

    name = STAGE_TEST
    most = 1
    free_here = True
    goal = "pull and run stage-test items on {env}, one cut a group"
    loop = (
        "You are {worker}, the drive's stage-test worker for {env}: you run groups of staging tests, each group on one cut, report every test as its own item, then pull the next group, holding the {env} slot only while a group of yours runs."
        " 1) Run `drive work pull --worker {worker}`: it prints your group, each item's job id, its brief, the PRs it tests and, for an item that ran before, the `drive job report <item>` that prints its last return, then the GO steps of the cut they share; on `NONE` or `HOLD` go to 5. Give back an item that does not fit the group, naming why, with `drive work back {worker} <item> \"<why>\"`, and another worker takes it."
        " 2) Read every item's brief and last return whole. Their cases all run on the one cut the GO line names: post one staging claim naming every PR, start one release carrying every item's PR head, and take one Mac box for the whole group when a brief asks for one."
        " 3) Once staging serves the cut, run each item's cases as its brief says, and as each finishes pipe its whole return, in the shape its brief asks, CASE lines and all, into `drive work done {worker} <item> -` from a quoted heredoc; an item whose case waits on something returns `WAITING <what>` the same way and keeps its place in the cut for a later worker."
        " 4) Go to 1."
        " 5) Give back any Mac box you took, then return. Never start a second cut while `drive work pull` says the queue's cut still holds the slot, and never report an item another worker holds."
    )

    def matches(self, data: dict, job: dict) -> bool:
        return not worker(job) and job["kind"] == "builder" and bool(state.staging_parts(data, job))

    def worth(self, data: dict, job: dict) -> bool:
        return any(data["prs"].get(key, {}).get("state") == "open" for key in state.tested_prs(data, job))

    def adopted(self, job: dict) -> bool:
        return bool(job.get("work_adopted"))

    def where(self, data: dict, job: dict) -> str:
        """Every stage-test item needs staging:<env>, which only this Mac reaches, with the Mac boxes it takes, by his pick, never a Sprite."""
        return "local"

    def blocking(self, data: dict, job_id: str) -> list[str]:
        """The waits holding a stage-test item's start, but, for an item outside the queue's cut, its wait on staging serving its own commit on the env, which only the queue's next cut gives it."""
        cut = state.slot_members(self.slot(data)) if self.ours(data) else []
        return [wait for wait in super().blocking(data, job_id) if job_id in cut or not ((found := state.STAGING_WAIT.fullmatch(wait)) and found["env"] == env(data))]

    def slot(self, data: dict) -> dict:
        return data["slots"].setdefault(env(data), {"holder": None})

    def ours(self, data: dict) -> bool:
        return bool((self.slot(data).get("holder") or {}).get("work"))

    def resumed(self, data: dict) -> list[str]:
        """The items of the queue's cut on the env that a worker may run there again, tests that returned WAITING and whose waits ended."""
        return [job_id for job_id in state.slot_members(self.slot(data)) if self.ready(data, job_id)] if self.ours(data) else []

    def reclaimed(self, data: dict, worker_id: str) -> None:
        """The queue's cut on the env goes to the worker running its items again, so its own release start passes the landing guard."""
        self.slot(data)["holder"]["work"] = worker_id

    def held(self, data: dict, worker_id: str) -> str | None:
        slot = self.slot(data)
        if self.ours(data) and (waiting := [member for member in state.slot_members(slot) if data["jobs"][member]["state"] not in state.FINISHED_JOB_STATES]):
            return f"the queue's {env(data)} cut still holds {', '.join(waiting)}, waiting on what each returned; a fresh worker runs them there once their waits end"
        return None

    def why_not(self, data: dict, moment: datetime) -> str | None:
        """Why no new cut takes the env now, None when one may: a cut or release holds it, or the release train waits for it, which goes first between cuts by his ruling, his priority's and an open incident's release first."""
        slot = self.slot(data)
        if held := slot["holder"]:
            holder = f"the queue's cut of {held['job']}" if held.get("work") else f"release {held['job']}"
            return f"waits on {env(data)}, held by {holder} until {when(held['until'])}; the next cut starts about {when(state.stamp(free_at(data, moment)))}"
        if not self.line(data):
            return f"no {self.name} item is ready"
        if train := state.train_waiting(data, env(data)):
            return f"waits on {env(data)}: release {train[0]} goes first, the train between cuts; the next cut starts about {when(state.stamp(free_at(data, moment)))}"
        if slot.get("promotion_due"):
            return f"waits on {env(data)}: its promotion skipped its last tick while staging was held, so the train goes first; the next cut starts about {when(state.stamp(free_at(data, moment)))}"
        return None

    def entry(self, data: dict, job_id: str, moment: datetime) -> dict:
        job = data["jobs"][job_id]
        read = job.get("work_reads") or {}
        return {"job": job_id, "prs": state.tested_prs(data, job), "minutes": cut_shape(data)["cut_minutes"], "cases": None, "at": state.stamp(moment), "paths": read.get("paths"), "stacks": read.get("stacks"), "starts": False}

    def group_of(self, data: dict, worker_id: str, items: list[str], moment: datetime) -> tuple[list[str], str]:
        """The queue's next cut, formed in code: the head of the line and the items behind it whose PRs change no file and reach no stack of the cut's, by state.packed, sized by cut_shape, holding the env for them and recording on the cut and on each item the head of each PR it carries, so a DONE run there proves the head it tested; the line the worker reads, and no record, since the worker pulls it."""
        first, riders = state.packed([self.entry(data, job_id, moment) for job_id in items], cut_shape(data)["cut_items"])
        group = [first["job"], *(rider["job"] for rider in riders)]
        heads = {key: data["prs"][key]["head"] for job_id in group for key in state.tested_prs(data, data["jobs"][job_id]) if key in data["prs"]}
        for job_id in group:
            data["jobs"][job_id]["cut_heads"] = {key: heads[key] for key in state.tested_prs(data, data["jobs"][job_id]) if key in heads}
        self.slot(data)["holder"] = state.leased(first, moment, riders) | {"work": worker_id, "heads": heads}
        prs = ", ".join(pr for job_id in group for pr in state.tested_prs(data, data["jobs"][job_id]))
        return group, f"GO staging {env(data)} until {self.slot(data)['holder']['until']} for {', '.join(group)}, one cut carrying {prs}: {CUT_STEPS.format(env=env(data))}{f'; {steps}' if (steps := cut_shape(data)['cut_steps']) else ''}"

    def row(self, data: dict, job_id: str) -> str:
        job = data["jobs"][job_id]
        timed = f"; starts about {when(job['starts_at'])}, ends by {when(job['ends_at'])}" if job.get("starts_at") else ""
        return f"{super().row(data, job_id)}; needs staging:{env(data)}; tests {', '.join(state.tested_prs(data, job))}{timed}"

    def done(self, data: dict, job_id: str, line: str) -> None:
        """Give an item's place in the queue's cut back once its line is DONE."""
        slot = self.slot(data)
        if line.startswith("DONE") and slot["holder"] and job_id in state.slot_members(slot):
            state.slot_release(data, env(data), job_id)

    def back(self, data: dict, job_id: str) -> None:
        """Give a test a worker handed back its place in the queue's cut, so the next cut packs it afresh."""
        if (slot := self.slot(data))["holder"] and job_id in state.slot_members(slot):
            state.slot_release(data, env(data), job_id)


class Prove(Kind):
    """Live proofs of merged PRs: a builder whose goal reads `prove <PRs> live`, each run by its brief's handoff, the install or release cut it waits on, its proof file and drive job prove, which takes its DONE; a worker runs a group of them one after another, so a merge's proof holds no agent of its own."""

    name = PROVE
    free_here = True
    goal = "pull and prove merged PRs live, one item at a time"
    loop = (
        "You are {worker}, the drive's prove worker: you prove merged PRs live, a group at a time, each item its own job, then pull the next group."
        " 1) Run `drive work pull --worker {worker}`: it prints your group, each item's job id, its brief and, for an item that ran before, the `drive job report <item>` that prints its last return; on `NONE` or `HOLD` go to 4. Give back an item that does not fit the group, naming why, with `drive work back {worker} <item> \"<why>\"`, and another worker takes it."
        " 2) For each item, read its brief and last return whole and do what its handoff says: install by the INSTALL: line it names, or run its after-merge staging pass, then write its proof file from the PROVE: lines it names, keep it with `drive job set <item> --repro <file>` and run `drive job prove <item>`, whose exit 0 takes the item's DONE. An item whose proof cannot pass yet returns `WAITING <what>` or `BLOCKED <why>` by piping its whole return into `drive work done {worker} <item> -` from a quoted heredoc."
        " 3) Go to 1."
        " 4) Return. Never report an item another worker holds."
    )

    def matches(self, data: dict, job: dict) -> bool:
        return not worker(job) and job["kind"] == "builder" and bool(state.PROVER_GOAL.fullmatch(job["goal"])) and not state.starts_cut(job)


class Build(Kind):
    """Builds: a builder no agent or lane of its own took, holding no PR yet, neither a staging tester, a prover nor a production cut's starter, its first build or one that returned WAITING and whose waits ended, run in groups on one stack, its repo and branch, or its order where it names no branch yet, so one worker pushes each branch; once it returns, its PR's events go to its own next worker as before."""

    name = BUILD
    group = 3
    compiles = True
    told = None
    goal = "pull and build changes, a group on one stack at a time"
    loop = (
        "You are {worker}, the drive's build worker: you build a group of changes on one stack, each its own job, then pull the next group."
        " 1) Run `drive work pull --worker {worker}`: it prints your group, each item's job id and brief; on `NONE` or `HOLD` go to 4. Give back an item that does not fit the group, naming why, with `drive work back {worker} <item> \"<why>\"`, and another worker takes it."
        " 2) For each item, read its brief whole and build it as a builder builds its own job, the item's id standing for your job wherever agents/builder.md names it, such as `drive pr add <url> --job <item>`, in the order the pull lists them, each on the branch its brief names or stacked on the one before it, and when it is pushed or blocked pipe its whole return, in the shape agents/builder.md gives a builder's, into `drive work done {worker} <item> -` from a quoted heredoc."
        " 3) Go to 1."
        " 4) Return. Never push a branch another worker builds, and never report an item another worker holds."
    )

    def matches(self, data: dict, job: dict) -> bool:
        return (
            not worker(job)
            and job["kind"] == "builder"
            and not state.staging_parts(data, job)
            and not state.PROVER_GOAL.fullmatch(job["goal"])
            and not state.starts_cut(job)
            and not job["prs"]
        )

    def stack(self, data: dict, job: dict) -> tuple:
        """The stack an item builds on: its repo and branch, or its order while it names no branch."""
        return (job["repo"] or "", job["branch"] or job["order"])

    def worth(self, data: dict, job: dict) -> bool:
        """Whether no live worker builds a branch this item works, by state.works_branch, so one worker pushes each branch."""
        return not any(
            claimed_by(data, other) and state.works_branch(other, job["branch"], job["repo"])
            for other_id, other in data["jobs"].items()
            if other is not job and other.get("claim")
        )

    def group_of(self, data: dict, worker_id: str, items: list[str], moment: datetime) -> tuple[list[str], str]:
        """The head of the line and the items behind it on its stack, up to group."""
        stack = self.stack(data, data["jobs"][items[0]])
        group = [job_id for job_id in items if self.stack(data, data["jobs"][job_id]) == stack][: self.group]
        return group, f"GO build {', '.join(group)} on {' '.join(filter(None, stack))}"


class Research(Kind):
    """Reads: a reader job no agent or lane of its own took, run a group of one order's reads at a time by a reader worker, each read answered and reported as its own item, so a burst of reads holds a few lanes rather than one each."""

    name = RESEARCH
    group = 3
    agent = "reader"
    told = None
    goal = "pull and answer research items, a group of one order's reads at a time"
    loop = (
        "You are {worker}, the drive's research worker: you answer a group of one order's reads, each its own job, then pull the next group."
        " 1) Run `drive work pull --worker {worker}`: it prints your group, each item's job id, its brief and, for an item that ran before, the `drive job report <item>` that prints its last return; on `NONE` or `HOLD` go to 4. Give back an item that does not fit the group, naming why, with `drive work back {worker} <item> \"<why>\"`, and another worker takes it."
        " 2) For each item, read its brief and last return whole and answer it as a reader answers its own job, the item's id standing for your job wherever agents/reader.md names it, then pipe its whole return, in the shape agents/reader.md gives a reader's, into `drive work done {worker} <item> -` from a quoted heredoc."
        " 3) Go to 1."
        " 4) Return. Never report an item another worker holds."
    )

    def matches(self, data: dict, job: dict) -> bool:
        return not worker(job) and job["kind"] == "reader"

    def group_of(self, data: dict, worker_id: str, items: list[str], moment: datetime) -> tuple[list[str], str]:
        """The head of the line and the reads behind it on its order, up to group."""
        order = data["jobs"][items[0]]["order"]
        group = [job_id for job_id in items if data["jobs"][job_id]["order"] == order][: self.group]
        return group, f"GO answer {', '.join(group)} on {order}"


class Design(Kind):
    """Designs: a design job no agent or lane of its own took, run one at a time by a designer worker as its own session, never an Agent call inside the session holding its order, so a design takes no root's advisor."""

    name = DESIGN
    group = 1
    agent = "design"
    told = None
    goal = "pull and flesh out designs, one at a time"
    loop = (
        "You are {worker}, the drive's design worker: you flesh out one design at a time, each its own job, then pull the next."
        " 1) Run `drive work pull --worker {worker}`: it prints your item, its job id, its brief and, for an item that ran before, the `drive job report <item>` that prints its last return; on `NONE` or `HOLD` go to 4. Give back an item that does not fit the group, naming why, with `drive work back {worker} <item> \"<why>\"`, and another worker takes it."
        " 2) Read its brief and last return whole and design it as a designer designs its own job, the item's id standing for your job wherever agents/designer.md names it, then pipe its whole return, in the shape agents/designer.md gives a designer's, into `drive work done {worker} <item> -` from a quoted heredoc."
        " 3) Go to 1."
        " 4) Return. Never report an item another worker holds."
    )

    def matches(self, data: dict, job: dict) -> bool:
        return not worker(job) and job["kind"] == "design"


STAGE = StageTest()
KIND = {kind.name: kind for kind in (STAGE, Prove(), Build(), Research(), Design())}


def kind_of(data: dict, job: dict) -> Kind | None:
    """The kind of the queue's item a job is, None for a job none of them takes."""
    return next((kind for kind in KIND.values() if kind.matches(data, job)), None)


def queued(data: dict, job: dict) -> bool:
    """Whether the queue, never carry_held, starts this job's work: a worker, or an item one of its kinds queues."""
    return worker(job) or any(kind.queued(data, job) for kind in KIND.values())


def ready(data: dict, job_id: str) -> bool:
    return any(kind.ready(data, job_id) for kind in KIND.values())


def live_workers(data: dict, kind: str, where: str | None = None) -> list[str]:
    """The kind's workers not finished, those running where alone when it names one."""
    return [job_id for job_id, job in data["jobs"].items() if worker(job) and job.get("work_kind", STAGE_TEST) == kind and job["state"] not in state.FINISHED_JOB_STATES and (where is None or job.get("where") == where)]


def item_of(data: dict, job: dict) -> Kind | None:
    """The kind whose queue starts this job's work, None for a worker or a job no kind queues, so drive job start and launch start no second worker beside the queue's."""
    return next((kind for kind in KIND.values() if kind.queued(data, job)), None)


def shown(data: dict, kind: Kind, word: str, items: list[str], said: str) -> str:
    return "\n".join([said, *(kind.row(data, job_id) for job_id in items)]) if word == "GO" else said


def pulled(name: str, worker_id: str) -> str:
    """drive work pull: the worker's next group, claimed, as the lines it reads."""
    moment = state.now()

    def pull_write(data: dict) -> str:
        held = data["jobs"].get(worker_id)
        if not held or not worker(held) or held["state"] in state.FINISHED_JOB_STATES:
            raise state.DriveError(f"{worker_id}: refused, it is no live worker of this drive's queue")
        kind = KIND[held.get("work_kind", STAGE_TEST)]
        if (done := len(held.get("work_done") or [])) >= WORKER_ITEMS:
            return f"NONE {worker_id} reported {done} items, its whole budget: end, and a fresh worker takes the rest"
        if (mine := [job_id for job_id, job in data["jobs"].items() if (job.get("claim") or {}).get("worker") == worker_id and job["state"] == "waiting" and worker_id in job["waits"]]) and kind.where(data, data["jobs"][mine[0]]) != held["where"]:
            for job_id in mine:
                data["jobs"][job_id].update(claim=None, waits=[wait for wait in data["jobs"][job_id]["waits"] if wait != worker_id])
            return f"NONE {', '.join(mine)} run {'on this Mac' if held['where'] == 'remote' else 'on a Sprite'}, so their claims go back to the queue for a worker there: end"
        if mine:
            return shown(data, kind, "GO", mine, f"{held.get('work_go') or 'GO'}\nYou still hold {', '.join(mine)}: report each with drive work done {worker_id} <item> - before you pull again")
        word, items, said = kind.take(data, worker_id, moment)
        return shown(data, kind, word, items, said)

    return state.write(name, pull_write)


def told(data: dict, kind: Kind, job_id: str, line: str) -> bool:
    """Whether an item's return goes to the session holding its order as a JOB line: every line of a kind whose told is None, a build's, read's or design's, whose holder acts on each as on its own job's; otherwise an ASK or BLOCKED, which someone must act on, and a DONE that leaves its order no other unfinished job, the done order its holder proves and tells him of, every other DONE staying on the item."""
    if kind.told is None or line.startswith(kind.told):
        return True
    job = data["jobs"][job_id]
    return line.startswith("DONE") and not any(
        other_id != job_id and not worker(other := data["jobs"][other_id]) and other["state"] not in state.FINISHED_JOB_STATES for other_id in data["orders"][job["order"]]["jobs"]
    )


def back(name: str, worker_id: str, job_id: str, why: str) -> str:
    """drive work back: give an item the worker judged does not fit its group back to the queue, refused only unless its claim is the worker's: its claim and its wait on the worker end, the why stays on the item as work_back, its kind gives back what it held for it, such as its place in a cut, and the worker that gave it back never pulls it again, so another worker takes it; the line saying so."""
    moment = state.now()

    def back_write(data: dict) -> str:
        if not (job := data["jobs"].get(job_id)) or claimed_by(data, job) != worker_id:
            raise state.DriveError(f"{job_id}: refused, {worker_id} holds no claim on it: drive work pull --worker {worker_id} names what it holds")
        job.update(claim=None, waits=[wait for wait in job["waits"] if wait != worker_id], work_back={"worker": worker_id, "why": why, "at": state.stamp(moment)}, updated=state.stamp(moment))
        KIND[data["jobs"][worker_id].get("work_kind", STAGE_TEST)].back(data, job_id)
        return f"{job_id} goes back to the queue from {worker_id}: {why}; another worker takes it"

    return state.write(name, back_write)


def done(name: str, worker_id: str, job_id: str, whole: str) -> tuple[str, dict]:
    """drive work done: take an item's whole return as drive job report takes it, refused unless its claim is the worker's, then end the claim and the item's wait on its worker, and give back what its kind holds for it; a refused return leaves the claim with the worker, to fix and send again. Its line stays on the item, and only one someone must act on, by told, goes to the session holding its order as a JOB line, so the queue's workers add no cci record for a line that only reports, which stays on the item for drive work show --order to read."""
    from drive import cli

    data = state.load(name)
    if not (job := data["jobs"].get(job_id)) or claimed_by(data, job) != worker_id:
        raise state.DriveError(f"{job_id}: refused, {worker_id} holds no claim on it: drive work pull --worker {worker_id} names what it holds")
    kind = KIND[data["jobs"][worker_id].get("work_kind", STAGE_TEST)]
    said, payload = cli.take_report(name, job_id, whole, tell=told(data, kind, job_id, whole.strip()))

    def done_write(data: dict) -> None:
        data["jobs"][job_id].update(claim=None, waits=[wait for wait in data["jobs"][job_id]["waits"] if wait != worker_id])
        data["jobs"][worker_id]["work_done"] = [*(data["jobs"][worker_id].get("work_done") or []), job_id]
        kind.done(data, job_id, whole.strip())

    state.write(name, done_write)
    return said, payload


def add_worker(data: dict, kind: Kind, where: str, moment: datetime) -> str:
    """Record a new worker of the kind running where on the order of the head item placed there, a job of the kind's agent that builds nothing unless the kind compiles, its first group claimed by take in the same write; its id. drive work start briefs and starts it."""
    first = data["jobs"][kind.pool(data, where)[0]]
    worker_id = state.next_id(data["jobs"], "j")
    _, held = state.holding_role(data, data["orders"][first["order"]])
    data["jobs"][worker_id] = state.new_job(first["order"], kind.agent, kind.goal.format(env=env(data)), [], state.stamp(moment)) | {
        "work": WORKER,
        "work_kind": kind.name,
        "builds_nothing": not kind.compiles,
        "where": where,
        "session": held.get("session"),
        "brief_flags": {flag: None for flag in state.BRIEF_FLAGS},
        "work_done": [],
    }
    kind.take(data, worker_id, moment)
    return worker_id


def brief_flags(data: dict, worker_id: str) -> dict:
    """The brief lines drive work start gives a worker: its kind's loop as its handoff and when it is done."""
    kind = KIND[data["jobs"][worker_id].get("work_kind", STAGE_TEST)]
    return {"handoff": kind.loop.format(worker=worker_id, env=env(data)), "done_when": DONE_WHEN.format(worker=worker_id)}


def start(name: str, worker_id: str) -> str:
    """Start a worker's agent as its own `claude --bg` session on this Mac, a builder on its brief, as drive job start starts a job's agent, in the drive's checkout; the session."""
    data = state.load(name)
    job = data["jobs"][worker_id]
    _, held = state.holding_role(data, data["orders"][job["order"]])
    if not held.get("session"):
        raise state.DriveError(f"{worker_id}: refused, not started: no session holds {job['order']}, so none would hear its agent")
    call = agents.shaped({"session_id": held["session"], "tool_input": {"subagent_type": state.JOB_AGENTS[job["kind"]][0], "prompt": f"Read your brief first: {job['brief']}"}})
    agent, cwd = uuid.uuid4().hex[:17], orca.checkout(name, held["cwd"])

    def start_write(data: dict) -> None:
        data["jobs"][worker_id]["bg"] = {"agent": agent, "session": None, "started": state.stamp(), "cwd": str(cwd), "pointed": []}

    state.write(name, start_write)
    try:
        session = agents.start_job(name, worker_id, agent, call, held, cwd)
    except (reads.ReadError, state.DriveError) as error:
        failed(name, worker_id, f"claude --bg started no agent: {error}")
        raise

    def started_write(data: dict) -> None:
        data["jobs"][worker_id]["bg"]["session"] = session
        data["jobs"][worker_id].update(agent=agent, launching=None)
        state.start(data["jobs"][worker_id])

    state.write(name, started_write, patient=True)
    return session


def failed(name: str, worker_id: str, why: str) -> None:
    """Mark a worker whose start failed as failed, so its claims end and its items go back to the queue, and the spawner tries again only after STALL_AFTER."""

    def failed_write(data: dict) -> None:
        data["jobs"][worker_id].update(bg=None, state="failed", said=f"not started: {why}", updated=state.stamp())

    state.write(name, failed_write)


def unread(data: dict) -> list[str]:
    """The ready stage-test items whose files and stacks the queue has not read, at most READS_PER_PASS, so packing never pairs a pass whose files it cannot see."""
    return [job_id for job_id in STAGE.line(data) if not data["jobs"][job_id].get("work_reads")][:READS_PER_PASS]


def admitted(data: dict, kind: Kind, where: str, moment: datetime) -> str | None:
    """Why no new worker of the kind starts where now, None when it may: by place.crowded for one on this Mac of a kind that compiles, which adds the builder cap, else by place.admission, each read for the head item placed there, so its class picks the cap and an urgent head starts past the elastic one."""
    if not (first := kind.pool(data, where)):
        return None
    if where == "local" and kind.compiles:
        return place.crowded(data, first[0], moment)
    return place.admission(data, first[0], "sprite" if where == "remote" else "local", moment)


def room(data: dict, kind: Kind, where: str, moment: datetime) -> int:
    """How many workers of the kind may start where this pass, at most WORKERS_PER_PASS: none while admitted refuses one, all of them for a kind whose items hold no cap on this Mac, by free_here, a staging test or proof compiling nothing here, else the room under the cap of the head's class, and, for one compiling here, under the builder cap too, since a worker recorded this pass counts in neither until it starts."""
    if admitted(data, kind, where, moment):
        return 0
    if where == "local" and kind.free_here:
        return WORKERS_PER_PASS
    urgent = place.preempts(data, data["jobs"][kind.pool(data, where)[0]])
    left = place.admitted_room("local" if where == "local" else "sprite", moment, urgent)
    if where == "local" and kind.compiles and not urgent:
        left = min(left, place.LOCAL_BUILDERS - len(place.local_builders(data, moment)))
    return min(left, WORKERS_PER_PASS)


def spawned(data: dict, moment: datetime, kinds: list[Kind]) -> tuple[list[str], list[str]]:
    """The spawner's write: first each item still holding a builder-slot or sprite-slot turn drops it, since the queue's caps place it now, and each worker recorded STALL_AFTER ago that no start took fails, so the items it claimed go back; then for each of these kinds and each pool, workers recorded while it wants one and room, read again for each one with the workers this pass recorded counted, lets one start, each claiming its first group, so ready items no worker holds start as fast as the caps allow and an urgent head's priority room starts no item behind it; the lines saying so and the workers to start."""
    lines, workers = [], []
    for job_id, job in data["jobs"].items():
        if item_of(data, job) and (left := [wait for wait in job["waits"] if wait not in (state.SLOT_WAIT, state.SPRITE_WAIT)]) != job["waits"]:
            job.update(waits=left, slot=None, sprite_turn=None)
        if worker(job) and job["state"] not in state.FINISHED_JOB_STATES and untaken(job) and not watch.launching(job, moment) and moment - state.parse_time(job["created"]) >= STALL_AFTER:
            job.update(state="failed", said=f"not started: no drive work start took it in {int(STALL_AFTER.total_seconds() // 60)} minutes", updated=state.stamp(moment))
            lines.append(f"WORK {job_id} failed: no start took it, so its claims go back to the queue")
    for kind in kinds:
        for where in POOLS:
            started = 0
            while started < WORKERS_PER_PASS and kind.wanted(data, where, moment) and room(data, kind, where, moment) > started:
                started += 1
                workers.append(worker_id := add_worker(data, kind, where, moment))
                lines.append(f"WORK {worker_id} records a {kind.name} worker {'on this Mac' if where == 'local' else 'on a Sprite'}, its first group claimed")
    return lines, workers


def stalled(data: dict, job_id: str, moment: datetime) -> str | None:
    """Why the queue leaves an item it holds with no one, None while a worker holds it, its waits hold it, or its kind keeps a new group from starting: a ready item STALL_AFTER since its last change while no worker of its kind lives and one is admitted, which the spawner should have answered."""
    job = data["jobs"][job_id]
    if worker(job) or not (kind := kind_of(data, job)) or not kind.ready(data, job_id) or kind.why_not(data, moment) or admitted(data, kind, where := kind.where(data, job), moment) or not kind.wanted(data, where, moment) or moment - state.parse_time(job["updated"]) < STALL_AFTER:
        return None
    return f"{job_id} is ready in the work queue {int((moment - state.parse_time(job['updated'])).total_seconds() // 60)} minutes with no worker while one may start: drive work show says why none started"
