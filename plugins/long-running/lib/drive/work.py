"""The drive's work queue: items a worker pulls in compatible groups, reports one by one, and pulls again, holding its capacity only while it works; drive work enable turns a kind over to it, one kind at a time."""

import uuid
from datetime import datetime, timedelta

from drive import agents, orca, place, providers, reads, state, view
from drive.render import when

STAGE_TEST = "stage-test"
PROVE = "prove"
BUILD = "build"
TOLD = ("ASK", "BLOCKED", "DONE")
WORKER = "worker"
WORKER_ITEMS = 18
READS_PER_PASS = 12
CUT_SHAPE = {"cut_items": 6, "cut_minutes": 30, "train_minutes": 60, "cut_steps": ""}
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


def enabled(data: dict, kind: str) -> bool:
    """Whether the queue runs this kind of work on this drive: stage-test on every drive, by his approval of code-formed cuts, and another kind once drive work enable turned it over."""
    return kind == STAGE_TEST or kind in data.get("work", {}).get("kinds", [])


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

    def where(self, data: dict, job: dict) -> str:
        """Where a worker runs an item of this kind, by placed."""
        return placed(data, job)

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

    def head(self, data: dict) -> list[str]:
        return self.resumed(data) or self.line(data)

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
        if self.where(data, data["jobs"][(first := self.line(data)[0])]) != where:
            return "NONE", [], f"NONE the queue's head {first} runs {'on this Mac' if where == 'remote' else 'on a Sprite'}, so a worker there takes it: end"
        group, go = self.group_of(data, worker_id, [job_id for job_id in self.line(data) if self.where(data, data["jobs"][job_id]) == where], moment)
        claim(data, worker_id, group, moment, go)
        return "GO", group, go

    def row(self, data: dict, job_id: str) -> str:
        job = data["jobs"][job_id]
        return f"- {job_id} on {job['order']}: brief {job['brief']}{f'; its last return: drive job report {job_id}' if job.get('returned') else ''}"

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
            return f"waits on {env(data)}, held by {holder} until {when(held['until'])}; the queue takes it next unless a release waits"
        if not self.line(data):
            return f"no {self.name} item is ready"
        if train := state.train_waiting(data, env(data)):
            return f"waits on {env(data)}: release {train[0]} goes first, the train between cuts; the queue takes it once that release ends"
        if slot.get("promotion_due"):
            return f"waits on {env(data)}: its promotion skipped its last tick while staging was held, so the train goes first; the queue takes it once a tick promotes"
        return None

    def entry(self, data: dict, job_id: str, moment: datetime) -> dict:
        job = data["jobs"][job_id]
        read = job.get("work_reads") or {}
        return {"job": job_id, "prs": state.tested_prs(data, job), "minutes": cut_shape(data)["cut_minutes"], "cases": None, "at": state.stamp(moment), "paths": read.get("paths"), "stacks": read.get("stacks"), "starts": False}

    def group_of(self, data: dict, worker_id: str, items: list[str], moment: datetime) -> tuple[list[str], str]:
        """The queue's next cut, formed in code: the head of the line and the items behind it whose PRs change no file and reach no stack of the cut's, by state.packed, sized by cut_shape, holding the env for them; the line the worker reads, and no record, since the worker pulls it."""
        first, riders = state.packed([self.entry(data, job_id, moment) for job_id in items], cut_shape(data)["cut_items"])
        group = [first["job"], *(rider["job"] for rider in riders)]
        self.slot(data)["holder"] = state.leased(first, moment, riders) | {"work": worker_id}
        prs = ", ".join(pr for job_id in group for pr in state.tested_prs(data, data["jobs"][job_id]))
        return group, f"GO staging {env(data)} until {self.slot(data)['holder']['until']} for {', '.join(group)}, one cut carrying {prs}: {CUT_STEPS.format(env=env(data))}{f'; {steps}' if (steps := cut_shape(data)['cut_steps']) else ''}"

    def row(self, data: dict, job_id: str) -> str:
        return f"{super().row(data, job_id)}; needs staging:{env(data)}; tests {', '.join(state.tested_prs(data, data['jobs'][job_id]))}"

    def done(self, data: dict, job_id: str, line: str) -> None:
        """Give an item's place in the queue's cut back once its line is DONE."""
        slot = self.slot(data)
        if line.startswith("DONE") and slot["holder"] and job_id in state.slot_members(slot):
            state.slot_release(data, env(data), job_id)


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

    def group_of(self, data: dict, worker_id: str, items: list[str], moment: datetime) -> tuple[list[str], str]:
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
        if (mine := [job_id for job_id, job in data["jobs"].items() if (job.get("claim") or {}).get("worker") == worker_id and job["state"] == "waiting" and worker_id in job["waits"]]) and kind.where(data, data["jobs"][mine[0]]) != held["where"]:
            for job_id in mine:
                data["jobs"][job_id].update(claim=None, waits=[wait for wait in data["jobs"][job_id]["waits"] if wait != worker_id])
            return f"NONE {', '.join(mine)} run {'on this Mac' if held['where'] == 'remote' else 'on a Sprite'}, so their claims go back to the queue for a worker there: end"
        if mine:
            return shown(data, kind, "GO", mine, f"{held.get('work_go') or 'GO'}\nYou still hold {', '.join(mine)}: report each with drive work done {worker_id} <item> - before you pull again")
        word, items, said = kind.take(data, worker_id, moment)
        return shown(data, kind, word, items, said)

    return state.write(name, pull_write)


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

    state.write(name, done_write)
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
        "where": kind.where(data, first),
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


def admitted(data: dict, kind: Kind, moment: datetime) -> str | None:
    """Why no new worker of the kind starts now, by place.admission for a Sprite lane or one on this Mac, as its head item is placed and under the cap of the head's class, None when it may."""
    if not (first := kind.head(data)):
        return None
    return place.admission(data, first[0], "sprite" if kind.where(data, data["jobs"][first[0]]) == "remote" else "local", moment)


def spawned(data: dict, moment: datetime) -> tuple[list[str], list[str]]:
    """The spawner's write: a worker recorded for each kind that wants one and is admitted; the lines saying so and the workers to start."""
    lines, workers = [], []
    for kind in KIND.values():
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
