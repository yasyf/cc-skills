"""The drive's work queue: items a worker pulls in compatible groups, reports one by one, and pulls again, holding its capacity only while it works; every kind it runs runs there on every drive, its old start path gone."""

import math
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from drive import agents, orca, place, providers, reads, state, view, watch
from drive.render import when

PROVE = "prove"
BUILD = "build"
RESEARCH = "research"
DESIGN = "design"
CODEX = "codex"
TOLD = ("ASK", "BLOCKED")
WORKER = "worker"
PROOF_HOLDS = "the proof the watch runs on it"
WORKER_ITEMS = 18
WORKERS_PER_PASS = 4
POOLS = ("local", "remote")
CUT_SHAPE = {"cut_items": 6, "cut_minutes": 30, "train_minutes": 60, "promotion_minutes": 30, "ci_minutes": 20, "start_moves": 10, "cut_steps": ""}
STALL_AFTER = timedelta(minutes=10)
DONE_WHEN = "drive work pull --worker {worker} prints NONE or HOLD and every item it gave you is reported; then end in two steps, in this order: pipe `DONE ran <each item you reported>` into `drive job report {worker} -` from a quoted heredoc, and once it is kept, end on that line alone"
ENDS = "pipe `DONE ran <each item you reported>` into `drive job report {worker} -` from a quoted heredoc, and once it is kept, end on that line alone, since ending first sends it back to you for the report, a whole extra turn"
ADDRESSED = " Where an item's brief or handoff names a lane its result goes to, such as `post the result there and to lane fable-fix`, post that lane the item's result as it says, beside its drive work done."


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
    """Where an item stands in the queue, lower first: its class, by state.class_rank, then one place.preempts lets past the caps, a --priority job, an urgent part or an immediate fix among them, so an urgent item heads its pool and the spawner sizes its room by the priority cap, then a priority order's or an open incident's, by state.jumps_queue, then its project's place in the ranks list caps.json sets, highest first, an unlisted one after every listed one, then where its project and then its workstream stand, by place.standings, so a full cap pauses the lowest workstream first and a whole project once its best workstream ranks lowest, then the oldest added; an order no longer open stands ahead of none."""
    job = data["jobs"][job_id]
    order = data["orders"][job["order"]]
    ranks = place.caps().get("ranks", [])
    held = place.standings(data)
    project = ranks.index(order["project"]) if order["project"] in ranks else len(ranks)
    stands = (held.get((order["project"], None), math.inf), held.get((order["project"], order["workstream"]), math.inf))
    return (state.class_rank(data, job), not place.preempts(data, job), not state.jumps_queue(data, job_id), project, *stands, state.parse_time(job["created"]))


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
    runs_itself = False
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

    def ready(self, data: dict, job_id: str, briefed: bool = True) -> bool:
        """Whether an item may be pulled now: queued, waiting or built, as a build that returned BUILT waiting on his card is once the card is answered, briefed, no drive job launch of it in flight, by watch.launching, so no worker claims a job a launch is starting, claimed by no live worker, no card of his open on it, nothing its start waits on holding it, by view.start_waits, and still worth running."""
        job = data["jobs"][job_id]
        return (
            not worker(job)
            and self.queued(data, job)
            and job["state"] in ("waiting", "built")
            and (bool(job.get("brief")) or not briefed)
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
        " 4) End: " + ENDS + ". Never report an item another worker holds."
    )

    def matches(self, data: dict, job: dict) -> bool:
        return not worker(job) and job["kind"] == "builder" and bool(state.PROVER_GOAL.fullmatch(job["goal"])) and not state.starts_cut(job)


class Build(Kind):
    """Builds: a builder no agent or lane of its own took, holding no PR yet or holding a text for its PR, such as the STAGE line of an event on it that drive pr wake holds there, which its next worker takes at its PR's branch, neither a staging tester, a prover nor a production cut's starter, its first build or one that returned WAITING and whose waits ended, run in groups on one stack, its repo and branch, or its order where it names no branch yet, so one worker pushes each branch; once it returns, its PR's events go to its own next worker as before."""

    name = BUILD
    group = 3
    compiles = True
    told = ("ASK", "BLOCKED", "DONE", "BUILT")
    goal = "pull and build changes, a group on one stack at a time"
    loop = (
        "You are {worker}, the drive's build worker: you build a group of changes on one stack, each its own job, then pull the next group."
        " 1) Run `drive work pull --worker {worker}`: it prints your group, each item's job id and brief; on `NONE` or `HOLD` go to 4. Give back an item that does not fit the group, naming why, with `drive work back {worker} <item> \"<why>\"`, and another worker takes it."
        " 2) For each item, read its brief whole and build it as a builder builds its own job, the item's id standing for your job wherever agents/builder.md names it, such as `drive pr add <url> --job <item>`, in the order the pull lists them, each on the branch its brief names or stacked on the one before it, and when it is pushed or blocked pipe its whole return, in the shape agents/builder.md gives a builder's, into `drive work done {worker} <item> -` from a quoted heredoc."
        " 3) Go to 1."
        " 4) End: " + ENDS + ". Never push a branch another worker builds, and never report an item another worker holds."
    )

    def matches(self, data: dict, job: dict) -> bool:
        return (
            not worker(job)
            and job["kind"] == "builder"
            and not state.staging_parts(data, job)
            and not state.PROVER_GOAL.fullmatch(job["goal"])
            and not state.starts_cut(job)
            and (not job["prs"] or bool(job["undelivered"]))
        )

    def blocking(self, data: dict, job_id: str) -> list[str]:
        """The waits holding a build item's start but its waits on its own PRs, which a text it holds, such as the STAGE line drive pr wake holds on it, outranks, and, for a job the watch proves, by watch_proves, such as a repair waiting on the proof it marked, that proof, which waits_lines never frees either, so no worker rebuilds it or rewrites the said line its proof keys on."""
        own = {state.pr_key(url) for url in data["jobs"][job_id]["prs"]}
        proof = [PROOF_HOLDS] if watch.watch_proves(data, job_id) else []
        return [*proof, *(wait for wait in super().blocking(data, job_id) if wait not in own)]

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
        " 4) End: " + ENDS + ". Never report an item another worker holds."
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
        " 4) End: " + ENDS + ". Never report an item another worker holds."
    )

    def matches(self, data: dict, job: dict) -> bool:
        return not worker(job) and job["kind"] == "design"


class Codex(Kind):
    """Codex jobs: a codex job no codex-ask run of its own took yet, whose item is its run: the spawner launches each ready one's codex-ask run on this Mac by drive job launch, under the caps by its class, with no Claude worker between, and the watch takes the run's result onto the item's row as it takes any codex job's."""

    name = CODEX
    runs_itself = True

    def matches(self, data: dict, job: dict) -> bool:
        return not worker(job) and job["kind"] == "codex"

    def where(self, data: dict, job: dict) -> str:
        """A codex-ask run starts on this Mac."""
        return "local"


KIND = {kind.name: kind for kind in (Prove(), Build(), Research(), Design(), Codex())}


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
    return [job_id for job_id, job in data["jobs"].items() if worker(job) and job["work_kind"] == kind and job["state"] not in state.FINISHED_JOB_STATES and (where is None or job.get("where") == where)]


def unbriefed(data: dict, moment: datetime, settle: timedelta) -> list[str]:
    """A build, read, design or codex item, never a staging test or proof, whose brief their own steps write, that would be ready but that no brief was written for settle after drive job add recorded them, so a session's own brief, written in the same call as its add, lands first, so the spawner writes each one's brief from what the drive holds, as drive job launch writes a first lane's, rather than leaving it to a session."""
    return [job_id for job_id, job in data["jobs"].items() if not job.get("brief") and not job.get("brief_error") and moment - state.parse_time(job["created"]) >= settle and any(kind.ready(data, job_id, briefed=False) for kind in KIND.values() if kind.name != PROVE)]


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
        kind = KIND[held["work_kind"]]
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
    """Whether an item's return goes to the session holding its order as a JOB line: every line of a kind whose told is None, a read's or design's, whose holder answers him or draws his card from it; a line its kind's told opens with, such as a build's ASK, BLOCKED, DONE or BUILT, never its PUSHED or READY, which the watch acts on as PR events; otherwise an ASK or BLOCKED, which someone must act on, a DONE whose CASE lines make no full pass or that has none, by state.passing, since a failed or unrun test reaches its holder every time, and a DONE that leaves its order no other unfinished job, the done order its holder proves and tells him of; only a passing DONE that changes nothing for anyone stays on the item."""
    if kind.told is None or line.startswith(kind.told):
        return True
    if not line.startswith("DONE"):
        return False
    if not state.passing(state.case_lines(line)):
        return True
    job = data["jobs"][job_id]
    return not any(
        other_id != job_id and not worker(other := data["jobs"][other_id]) and other["state"] not in state.FINISHED_JOB_STATES for other_id in data["orders"][job["order"]]["jobs"]
    )


def back(name: str, worker_id: str, job_id: str, why: str) -> str:
    """drive work back: give an item the worker judged does not fit its group back to the queue, refused only unless its claim is the worker's: its claim and its wait on the worker end, the why stays on the item as work_back, its kind gives back what it held for it, such as its place in a cut, and the worker that gave it back never pulls it again, so another worker takes it; the line saying so."""
    moment = state.now()

    def back_write(data: dict) -> str:
        if not (job := data["jobs"].get(job_id)) or claimed_by(data, job) != worker_id:
            raise state.DriveError(f"{job_id}: refused, {worker_id} holds no claim on it: drive work pull --worker {worker_id} names what it holds")
        job.update(claim=None, waits=[wait for wait in job["waits"] if wait != worker_id], work_back={"worker": worker_id, "why": why, "at": state.stamp(moment)}, updated=state.stamp(moment))
        KIND[data["jobs"][worker_id]["work_kind"]].back(data, job_id)
        return f"{job_id} goes back to the queue from {worker_id}: {why}; another worker takes it"

    return state.write(name, back_write)


def done(name: str, worker_id: str, job_id: str, whole: str) -> tuple[str, dict]:
    """drive work done: take an item's whole return as drive job report takes it, refused unless its claim is the worker's, then end the claim and the item's wait on its worker, and give back what its kind holds for it; a refused return leaves the claim with the worker, to fix and send again. Its line stays on the item, and only one someone must act on, by told, goes to the session holding its order as a JOB line, so the queue's workers add no cci record for a line that only reports, which stays on the item for drive work show --order to read."""
    from drive import cli

    data = state.load(name)
    if not (job := data["jobs"].get(job_id)) or claimed_by(data, job) != worker_id:
        raise state.DriveError(f"{job_id}: refused, {worker_id} holds no claim on it: drive work pull --worker {worker_id} names what it holds")
    kind = KIND[data["jobs"][worker_id]["work_kind"]]
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
    kind = KIND[data["jobs"][worker_id]["work_kind"]]
    return {"handoff": kind.loop.format(worker=worker_id, env=env(data)) + ADDRESSED, "done_when": DONE_WHEN.format(worker=worker_id)}


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


def admitted(data: dict, kind: Kind, where: str, moment: datetime) -> str | None:
    """Why no new worker of the kind starts where now, None when it may: by place.builder_cap for one on this Mac of a kind that compiles, which adds the builder cap to this Mac's admission, else by place.admission, each read for the head item placed there, so its class picks the cap and an urgent head starts past the elastic one."""
    if not (first := kind.pool(data, where)):
        return None
    if where == "local" and kind.compiles:
        return place.builder_cap(data, first[0], moment)
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


def spawned(data: dict, moment: datetime, kinds: list[Kind]) -> tuple[list[str], list[list[str]]]:
    """The spawner's write: first each worker recorded STALL_AFTER ago that no start took fails, so the items it claimed go back; then for each of these kinds and each pool, workers recorded while it wants one and room, read again for each one with the workers this pass recorded counted, lets one start, each claiming its first group, or for a kind whose item is its own run, a codex job's, the head item stamped launching and started by drive job launch, so ready items no worker holds start as fast as the caps allow and an urgent head's priority room starts no item behind it; the lines saying so and the workers to start."""
    lines, starts = [], []
    for job_id, job in data["jobs"].items():
        if worker(job) and job["state"] not in state.FINISHED_JOB_STATES and untaken(job) and not watch.launching(job, moment) and moment - state.parse_time(job["created"]) >= STALL_AFTER:
            job.update(state="failed", said=f"not started: no drive work start took it in {int(STALL_AFTER.total_seconds() // 60)} minutes", updated=state.stamp(moment))
            lines.append(f"WORK {job_id} failed: no start took it, so its claims go back to the queue")
    for kind in kinds:
        for where in POOLS:
            started = 0
            while started < WORKERS_PER_PASS and kind.wanted(data, where, moment) and room(data, kind, where, moment) > started:
                started += 1
                if kind.runs_itself:
                    first = kind.pool(data, where)[0]
                    data["jobs"][first].update(launching=state.stamp(moment), updated=state.stamp(moment))
                    starts.append(["job", "launch", first])
                    lines.append(f"WORK {first} starts its {kind.name} run, the queue's item itself")
                    continue
                starts.append(["work", "start", worker_id := add_worker(data, kind, where, moment)])
                lines.append(f"WORK {worker_id} records a {kind.name} worker {'on this Mac' if where == 'local' else 'on a Sprite'}, its first group claimed")
    return lines, starts


def stalled(data: dict, job_id: str, moment: datetime) -> str | None:
    """Why the queue leaves an item it holds with no one, None while a worker holds it, its waits hold it, or its kind keeps a new group from starting: a ready item STALL_AFTER since its last change while no worker of its kind lives and one is admitted, which the spawner should have answered."""
    job = data["jobs"][job_id]
    if worker(job) or not (kind := kind_of(data, job)) or not kind.ready(data, job_id) or kind.why_not(data, moment) or admitted(data, kind, where := kind.where(data, job), moment) or not kind.wanted(data, where, moment) or moment - state.parse_time(job["updated"]) < STALL_AFTER:
        return None
    return f"{job_id} is ready in the work queue {int((moment - state.parse_time(job['updated'])).total_seconds() // 60)} minutes with no worker while one may start: drive work show says why none started"
