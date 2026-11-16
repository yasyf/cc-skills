"""The drive's work queue: items a worker pulls in compatible groups, reports one by one, and pulls again, holding its capacity only while it works; drive work enable turns a kind over to it, one kind at a time."""

import uuid
from datetime import datetime, timedelta

from drive import agents, orca, place, providers, reads, state, view

STAGE_TEST = "stage-test"
PROVE = "prove"
BUILD = "build"
TOLD = ("ASK", "BLOCKED", "DONE")
WORKER = "worker"
WORKER_ITEMS = 18
READS_PER_PASS = 12
CUT_MINUTES = 45
STALL_AFTER = timedelta(minutes=10)
DONE_WHEN = "drive work pull --worker {worker} prints NONE or HOLD and every item it gave you is reported; return DONE ran <each item you reported>"


def untaken(job: dict) -> bool:
    return not (job.get("agent") or job.get("dispatch") or job.get("bg") or job.get("codex_run"))


def worker(job: dict) -> bool:
    return job.get("work") == WORKER


def env(data: dict) -> str | None:
    """The staging env whose slot the queue's cuts hold, the repository's staging provider's."""
    return providers.of(data).staging.env


def enabled(data: dict, kind: str) -> bool:
    """Whether drive work enable turned this kind of work over to the queue on this drive."""
    return kind in data.get("work", {}).get("kinds", [])


def claimed_by(data: dict, job: dict) -> str | None:
    """The worker holding the item's claim, None once that worker finished, so its next pull goes to a fresh one."""
    held = (job.get("claim") or {}).get("worker")
    return held if held in data["jobs"] and data["jobs"][held]["state"] not in state.FINISHED_JOB_STATES else None


def placed(data: dict, job: dict) -> str:
    """Where a worker runs an item: on this Mac, "local", when the lines its brief flags give it today declare a step only this Mac runs, by cli.mac_lines, the rule drive job launch places a lane by, such as a Mac box's or drive job prove's, else on a Sprite, "remote", which keeps this Mac's load down."""
    from drive import cli

    return "local" if any(cli.mac_lines(data["name"], job, job.get("brief_flags") or {})) else "remote"


def rank(data: dict, job_id: str) -> tuple:
    """Where an item stands in the queue, lower first: a priority order's or an open incident's first, by state.jumps_queue, then the oldest added."""
    return (not state.jumps_queue(data, job_id), state.parse_time(data["jobs"][job_id]["created"]))


def claim(data: dict, worker_id: str, items: list[str], moment: datetime, go: str) -> None:
    data["jobs"][worker_id]["work_go"] = go
    for job_id in items:
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

    def matches(self, data: dict, job: dict) -> bool:
        raise NotImplementedError

    def worth(self, data: dict, job: dict) -> bool:
        return True

    def adopted(self, job: dict) -> bool:
        return False

    def queued(self, data: dict, job: dict) -> bool:
        """Whether the queue, never start_jobs or carry_held, starts this job's work: an item of this kind on a drive that enabled it, that no agent or lane of its own took or that the queue adopted."""
        return (untaken(job) or self.adopted(job)) and job["state"] not in state.FINISHED_JOB_STATES and self.matches(data, job) and enabled(data, self.name)

    def ready(self, data: dict, job_id: str) -> bool:
        """Whether an item may be pulled now: queued, waiting, briefed, claimed by no live worker, no card of his open on it, nothing its start waits on holding it, by view.start_waits, and still worth running."""
        job = data["jobs"][job_id]
        return (
            not worker(job)
            and self.queued(data, job)
            and job["state"] == "waiting"
            and bool(job.get("brief"))
            and claimed_by(data, job) is None
            and not view.open_cards(job)
            and not [wait for wait in view.start_waits(data, job, None) if wait != (job.get("claim") or {}).get("worker")]
            and self.worth(data, job)
        )

    def line(self, data: dict) -> list[str]:
        """The items of this kind ready now, in queue order."""
        return sorted((job_id for job_id in data["jobs"] if self.ready(data, job_id)), key=lambda job_id: rank(data, job_id))

    def resumed(self, data: dict) -> list[str]:
        """Items a worker runs again where they ran before, ahead of the line; none for a kind that holds nothing between pulls."""
        return []

    def head(self, data: dict) -> list[str]:
        return self.resumed(data) or self.line(data)

    def why_not(self, data: dict, moment: datetime) -> str | None:
        return None if self.line(data) else f"no {self.name} item is ready"

    def held(self, data: dict, worker_id: str) -> str | None:
        """Why a worker of this kind may pull nothing new while work of the queue's still stands: none for a kind holding nothing between pulls."""
        return None

    def group_of(self, data: dict, items: list[str], moment: datetime) -> tuple[list[str], str]:
        """The group a worker takes from the ready items placed where it runs, its head first, and the GO line it reads."""
        group = items[: self.group]
        return group, f"GO {', '.join(group)}"

    def take(self, data: dict, worker_id: str, moment: datetime) -> tuple[str, list[str], str]:
        """Claim the worker's next group in one write: the items to run again first, else, when why_not lets a group start, the head of the line and the items behind it placed where the worker runs, by group_of; the word, the items and the line the worker reads."""
        if again := self.resumed(data):
            go = data["jobs"][worker_id].get("work_go") or f"GO run these again where they ran: {', '.join(again)}"
            claim(data, worker_id, again, moment, go)
            return "GO", again, go
        if why := self.held(data, worker_id):
            return "HOLD", [], f"HOLD {why}"
        if why := self.why_not(data, moment):
            return "NONE", [], f"NONE {why}"
        where = data["jobs"][worker_id]["where"]
        if placed(data, data["jobs"][(first := self.line(data)[0])]) != where:
            return "NONE", [], f"NONE the queue's head {first} runs {'on this Mac' if where == 'remote' else 'on a Sprite'}, so a worker there takes it: end"
        group, go = self.group_of(data, [job_id for job_id in self.line(data) if placed(data, data["jobs"][job_id]) == where], moment)
        claim(data, worker_id, group, moment, go)
        return "GO", group, go

    def row(self, data: dict, job_id: str) -> str:
        job = data["jobs"][job_id]
        return f"- {job_id} on {job['order']}: brief {job['brief']}{f'; its last return: drive job report {job_id}' if job.get('returned') else ''}"

    def absorb(self, data: dict) -> list[str]:
        return []

    def done(self, data: dict, job_id: str, line: str) -> None:
        """What a reported item gives back beside its claim."""

    def wanted(self, data: dict, moment: datetime) -> bool:
        """Whether the queue wants a worker of this kind it does not have: none lives, none failed to start in the last STALL_AFTER, so a failing start is tried again at that pace, and items wait to run again or a new group may start, by why_not."""
        if not enabled(data, self.name) or live_worker(data, self.name) or any(
            worker(job) and job.get("work_kind") == self.name and job["state"] == "failed" and not (job["agent"] or job.get("dispatch")) and moment - state.parse_time(job["updated"]) < STALL_AFTER for job in data["jobs"].values()
        ):
            return False
        return bool(self.resumed(data)) or self.why_not(data, moment) is None


class StageTest(Kind):
    """Staging tests: a builder holding a `staging test of #N before it lands` part that starts no cut, run in groups whose PRs change no file and reach no stack in common, one group a cut on the env's one slot, whose ledger the old first-in-first-out line shares."""

    name = STAGE_TEST
    group = state.PACKED_CUT
    goal = "pull and run stage-test items on {env}, one cut a group"
    loop = (
        "You are {worker}, the drive's stage-test worker for {env}: you run groups of staging tests, each group on one cut, report every test as its own item, then pull the next group, holding the {env} slot only while a group of yours runs."
        " 1) Run `drive work pull --worker {worker}`: it prints your group, each item's job id, its brief, the PRs it tests and, for an item that ran before, the `drive job report <item>` that prints its last return, then the GO steps of the cut they share; on `NONE` or `HOLD` go to 5."
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

    def slot(self, data: dict) -> dict:
        return data["slots"].setdefault(env(data), {"holder": None, "queue": [], "hold": None})

    def ours(self, data: dict) -> bool:
        return bool((self.slot(data).get("holder") or {}).get("work"))

    def resumed(self, data: dict) -> list[str]:
        """The items of the queue's cut on the env that a worker may run there again, tests that returned WAITING and whose waits ended."""
        return [job_id for job_id in state.slot_members(self.slot(data)) if self.ready(data, job_id)] if self.ours(data) else []

    def held(self, data: dict, worker_id: str) -> str | None:
        slot = self.slot(data)
        if self.ours(data) and (waiting := [member for member in state.slot_members(slot) if data["jobs"][member]["state"] not in state.FINISHED_JOB_STATES]):
            return f"the queue's {env(data)} cut still holds {', '.join(waiting)}, waiting on what each returned; a fresh worker runs them there once their waits end"
        return None

    def legacy_ahead(self, data: dict, first: str) -> str | None:
        """The job of the env's old first-in-first-out line whose turn comes before the item's, by the same priority and age, so the queue and the old line share one ledger fairly."""
        if not (queue := self.slot(data)["queue"]):
            return None
        return queue[0]["job"] if (not state.jumps_queue(data, queue[0]["job"]), state.parse_time(queue[0]["at"])) <= rank(data, first) else None

    def why_not(self, data: dict, moment: datetime) -> str | None:
        """Why no new cut of queued work takes the env now, None when one may: the slot is held, held for another job, or the old line's turn comes first."""
        slot = self.slot(data)
        if slot["holder"]:
            return f"the {env(data)} slot is held by {slot['holder']['job']}'s cut until {slot['holder']['until']}"
        if not (items := self.line(data)):
            return f"no {self.name} item is ready"
        if not state.held_for(data, slot, items[0], moment):
            return f"the {env(data)} slot is held for {slot['hold']['for']} until {slot['hold']['until']}"
        if ahead := self.legacy_ahead(data, items[0]):
            return f"{ahead} of the {env(data)} slot's old line comes first"
        return None

    def entry(self, data: dict, job_id: str, moment: datetime) -> dict:
        job = data["jobs"][job_id]
        read = job.get("work_reads") or {}
        return {"job": job_id, "prs": state.tested_prs(data, job), "minutes": CUT_MINUTES, "cases": None, "at": state.stamp(moment), "paths": read.get("paths"), "stacks": read.get("stacks"), "starts": False}

    def group_of(self, data: dict, items: list[str], moment: datetime) -> tuple[list[str], str]:
        """The head of the line and up to PACKED_CUT - 1 items behind it whose PRs change no file and reach no stack of the cut's, by state.packed, holding the env's slot for them as one cut."""
        slot = self.slot(data)
        first, riders = state.packed([self.entry(data, job_id, moment) for job_id in items])
        group = [first["job"], *(rider["job"] for rider in riders)]
        slot["holder"] = state.leased(first, moment, riders) | {"work": True}
        steps = state.SLOT_GO_STEPS.format(env=env(data), job=first["job"])
        prs = ", ".join(pr for job_id in group for pr in state.tested_prs(data, data["jobs"][job_id]))
        return group, f"GO staging {env(data)} until {slot['holder']['until']} for {', '.join(group)}, one cut carrying {prs}: {steps}"

    def row(self, data: dict, job_id: str) -> str:
        return f"{super().row(data, job_id)}; tests {', '.join(state.tested_prs(data, data['jobs'][job_id]))}"

    def done(self, data: dict, job_id: str, line: str) -> None:
        """Give an item's place in the queue's cut back once its line is DONE."""
        slot = self.slot(data)
        if line.startswith("DONE") and slot["holder"] and job_id in state.slot_members(slot):
            state.slot_release(data, env(data), job_id)

    def absorb(self, data: dict) -> list[str]:
        """Take each staging pass off the env's old first-in-first-out line into the queue, so the queue's cuts pack them: an item the queue runs already, and a tester waiting its turn there with no release of its own to start, which the queue adopts, its waits for that turn dropped, staging serving a commit of the cut it never got and its staging-slot token, so a worker runs its cases from its brief and last return; a running pass stays on the old line."""
        slot = data["slots"].get(env(data))
        if not slot or not enabled(data, self.name):
            return []
        for each in slot["queue"]:
            job = data["jobs"][each["job"]]
            if not each["starts"] and job["state"] == "waiting" and not job.get("work_adopted") and self.matches(data, job):
                job.update(work_adopted=True, waits=[wait for wait in job["waits"] if not state.STAGING_WAIT.fullmatch(wait) and wait != f"staging-slot:{each['job']}"])
        if not (ours := [each["job"] for each in slot["queue"] if self.queued(data, data["jobs"][each["job"]])]):
            return []
        slot["queue"] = [each for each in slot["queue"] if each["job"] not in ours]
        return [f"WORK took {len(ours)} passes off the {env(data)} slot's old line into the queue: {', '.join(ours)}"]


class Prove(Kind):
    """Live proofs of merged PRs: a builder whose goal reads `prove <PRs> live`, each run by its brief's handoff, the install or release cut it waits on, its proof file and drive job prove, which takes its DONE; a worker runs a group of them one after another, so a merge's proof holds no agent of its own."""

    name = PROVE
    goal = "pull and prove merged PRs live, one item at a time"
    loop = (
        "You are {worker}, the drive's prove worker: you prove merged PRs live, a group at a time, each item its own job, then pull the next group."
        " 1) Run `drive work pull --worker {worker}`: it prints your group, each item's job id, its brief and, for an item that ran before, the `drive job report <item>` that prints its last return; on `NONE` or `HOLD` go to 4."
        " 2) For each item, read its brief and last return whole and do what its handoff says: install by the INSTALL: line it names, or run its after-merge staging pass, then write its proof file from the PROVE: lines it names, keep it with `drive job set <item> --repro <file>` and run `drive job prove <item>`, whose exit 0 takes the item's DONE. An item whose proof cannot pass yet returns `WAITING <what>` or `BLOCKED <why>` by piping its whole return into `drive work done {worker} <item> -` from a quoted heredoc."
        " 3) Go to 1."
        " 4) Return. Never report an item another worker holds."
    )

    def matches(self, data: dict, job: dict) -> bool:
        return not worker(job) and job["kind"] == "builder" and bool(state.PROVER_GOAL.fullmatch(job["goal"])) and not state.starts_cut(job)


class Build(Kind):
    """First builds: a builder or codex-free change job that no worker ever started, neither a staging tester nor a prover, run in groups on one stack, its repo and branch, or its order where it names no branch yet, so one worker pushes each branch; once it returns, its PR's events go to its own next worker as before."""

    name = BUILD
    group = 3
    goal = "pull and build changes, a group on one stack at a time"
    loop = (
        "You are {worker}, the drive's build worker: you build a group of changes on one stack, each its own job, then pull the next group."
        " 1) Run `drive work pull --worker {worker}`: it prints your group, each item's job id and brief; on `NONE` or `HOLD` go to 4."
        " 2) For each item, read its brief whole and build it as a builder builds its own job, in the order the pull lists them, each on the branch its brief names or stacked on the one before it, and when it is pushed or blocked pipe its whole return, in the shape agents/builder.md gives a builder's, into `drive work done {worker} <item> -` from a quoted heredoc."
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
            and (job["said"] == state.NOT_STARTED or claimed_by(data, job) is not None)
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

    def group_of(self, data: dict, items: list[str], moment: datetime) -> tuple[list[str], str]:
        """The head of the line and the items behind it on its stack, up to group."""
        stack = self.stack(data, data["jobs"][items[0]])
        group = [job_id for job_id in items if self.stack(data, data["jobs"][job_id]) == stack][: self.group]
        return group, f"GO build {', '.join(group)} on {' '.join(filter(None, stack))}"


STAGE = StageTest()
KIND = {kind.name: kind for kind in (STAGE, Prove(), Build())}
KINDS = tuple(KIND)


def kind_of(data: dict, job: dict) -> Kind | None:
    """The kind of the queue's item a job is, None for a job none of them takes."""
    return next((kind for kind in KIND.values() if kind.matches(data, job)), None)


def queued(data: dict, job: dict) -> bool:
    """Whether the queue, never start_jobs or carry_held, starts this job's work: a worker, or an item a kind the drive enabled queues."""
    return worker(job) or any(kind.queued(data, job) for kind in KIND.values())


def ready(data: dict, job_id: str) -> bool:
    return any(kind.ready(data, job_id) for kind in KIND.values())


def live_worker(data: dict, kind: str) -> str | None:
    return next((job_id for job_id, job in data["jobs"].items() if worker(job) and job.get("work_kind", STAGE_TEST) == kind and job["state"] not in state.FINISHED_JOB_STATES), None)


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
        if mine := [job_id for job_id, job in data["jobs"].items() if (job.get("claim") or {}).get("worker") == worker_id and job["state"] == "waiting" and worker_id in job["waits"]]:
            return shown(data, kind, "GO", mine, f"{held.get('work_go') or 'GO'}\nYou still hold {', '.join(mine)}: report each with drive work done {worker_id} <item> - before you pull again")
        word, items, said = kind.take(data, worker_id, moment)
        return shown(data, kind, word, items, said)

    return state.update(name, None, pull_write)


def done(name: str, worker_id: str, job_id: str, whole: str) -> tuple[str, dict]:
    """drive work done: take an item's whole return as drive job report takes it, refused unless its claim is the worker's, then end the claim and the item's wait on its worker, and give back what its kind holds for it; a refused return leaves the claim with the worker, to fix and send again. Its line stays on the item, and only one someone must act on, ASK, BLOCKED or DONE, goes to the session holding its order as a JOB line, so the queue's workers add no cci record for a WAITING, READY or PUSHED."""
    from drive import cli

    data = state.load(name)
    if not (job := data["jobs"].get(job_id)) or claimed_by(data, job) != worker_id:
        raise state.DriveError(f"{job_id}: refused, {worker_id} holds no claim on it: drive work pull --worker {worker_id} names what it holds")
    said, payload = cli.take_report(name, job_id, whole, tell=whole.strip().startswith(TOLD))

    def done_write(data: dict) -> None:
        data["jobs"][job_id].update(claim=None, waits=[wait for wait in data["jobs"][job_id]["waits"] if wait != worker_id])
        data["jobs"][worker_id]["work_done"] = [*(data["jobs"][worker_id].get("work_done") or []), job_id]
        KIND[data["jobs"][worker_id].get("work_kind", STAGE_TEST)].done(data, job_id, whole.strip())

    state.update(name, None, done_write)
    return said, payload


def add_worker(data: dict, kind: Kind, moment: datetime) -> str:
    """Record a new worker of the kind on the order of its head item, a builder building nothing, on a Sprite unless that item runs only on this Mac, by placed, its first group claimed by take in the same write; its id. drive work start briefs and starts it."""
    first = data["jobs"][kind.head(data)[0]]
    worker_id = state.next_id(data["jobs"], "j")
    _, held = state.holding_role(data, data["orders"][first["order"]])
    data["jobs"][worker_id] = state.new_job(first["order"], "builder", kind.goal.format(env=env(data)), [], state.stamp(moment)) | {
        "work": WORKER,
        "work_kind": kind.name,
        "builds_nothing": True,
        "where": placed(data, first),
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
    call = agents.shaped({"session_id": held["session"], "tool_input": {"subagent_type": state.JOB_AGENTS["builder"][0], "prompt": f"Read your brief first: {job['brief']}"}})
    agent, cwd = uuid.uuid4().hex[:17], orca.checkout(name, held["cwd"])

    def start_write(data: dict) -> None:
        data["jobs"][worker_id]["bg"] = {"agent": agent, "session": None, "started": state.stamp(), "cwd": str(cwd), "pointed": []}

    state.update(name, None, start_write)
    try:
        session = agents.start_job(name, worker_id, agent, call, held, cwd)
    except (reads.ReadError, state.DriveError) as error:
        failed(name, worker_id, f"claude --bg started no agent: {error}")
        raise

    def started_write(data: dict) -> None:
        data["jobs"][worker_id]["bg"]["session"] = session
        data["jobs"][worker_id].update(agent=agent, launching=None)
        state.start(data["jobs"][worker_id])

    state.update(name, None, started_write)
    return session


def failed(name: str, worker_id: str, why: str) -> None:
    """Mark a worker whose start failed as failed, so its claims end and its items go back to the queue, and the spawner tries again only after STALL_AFTER."""

    def failed_write(data: dict) -> None:
        data["jobs"][worker_id].update(bg=None, state="failed", said=f"not started: {why}", updated=state.stamp())

    state.update(name, None, failed_write)


def unread(data: dict) -> list[str]:
    """The ready stage-test items whose files and stacks the queue has not read, at most READS_PER_PASS, so packing never pairs a pass whose files it cannot see."""
    return [job_id for job_id in STAGE.line(data) if not data["jobs"][job_id].get("work_reads")][:READS_PER_PASS]


def admitted(data: dict, kind: Kind, moment: datetime) -> str | None:
    """Why no new worker of the kind starts now, by place.admission for a Sprite lane or one on this Mac, as its head item is placed, None when it may, or when that head serves a priority order or an open incident, by state.jumps_queue, which starts past every cap."""
    if not (first := kind.head(data)) or state.jumps_queue(data, first[0]):
        return None
    return place.admission(data, None, "sprite" if placed(data, data["jobs"][first[0]]) == "remote" else "local", moment)


def spawned(data: dict, moment: datetime) -> tuple[list[str], list[str]]:
    """The spawner's write: each enabled kind's old line absorbed, then a worker recorded for each kind that wants one and is admitted; the lines saying so and the workers to start."""
    lines, workers = [], []
    for kind in KIND.values():
        lines += kind.absorb(data)
        if kind.wanted(data, moment) and not admitted(data, kind, moment):
            workers.append(worker_id := add_worker(data, kind, moment))
            lines.append(f"WORK {worker_id} records a {kind.name} worker, its first group claimed")
    return lines, workers


def stalled(data: dict, job_id: str, moment: datetime) -> str | None:
    """Why the queue leaves an item it holds with no one, None while a worker holds it, its waits hold it, or its kind keeps a new group from starting: a ready item STALL_AFTER since its last change while no worker of its kind lives and one is admitted, which the spawner should have answered."""
    job = data["jobs"][job_id]
    if worker(job) or not (kind := kind_of(data, job)) or not kind.ready(data, job_id) or live_worker(data, kind.name) or kind.why_not(data, moment) or admitted(data, kind, moment) or moment - state.parse_time(job["updated"]) < STALL_AFTER:
        return None
    return f"{job_id} is ready in the work queue {int((moment - state.parse_time(job['updated'])).total_seconds() // 60)} minutes with no worker while one may start: drive work show says why none started"
