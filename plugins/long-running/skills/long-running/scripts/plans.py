"""Roll the cc-notes tasks registered with a drive up into its plans' steps.

A plan is registered when it carries ``program:<program>`` or is named in ``pinned``. A task is registered when it
carries ``program:<program>`` or hangs off a registered plan. A plan's steps are its ``### <n>. <title>`` headings; a
task joins step ``<n>`` of its own plan through a ``step:<n>`` label, its plan's unmapped step without one, and the
unassigned step with no plan. Done tasks count as done, cancelled ones count nowhere, an open or in-progress task with
an unfinished blocker or a failed criterion is blocked. The drive dashboard's ``lr.plan-progress`` card and
``handoff.py``'s progress record both read through :func:`read`. Stdlib only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

STEP_HEADING = re.compile(r"^### (?P<number>\d+(?:\.\d+)*)\.\s+(?P<title>.+?)\s*$", re.MULTILINE)
STEP_LABEL = "step:"
STATES = ("done", "in_progress", "blocked", "open")
ORDER = ("blocked", "in_progress", "open", "done")
FINISHED = frozenset({"done", "cancelled"})
UNMAPPED = "unmapped"
UNASSIGNED = "unassigned"
EVERY_TASK = ("task", "list", "--all", "--all-branches", "--include-archived", "--json")
WORKERS = 4
TITLE_CHARS = 60
SHOWN: dict[str, tuple[str, dict]] = {}

Ccn = Callable[..., str]


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    state: str
    owner: str | None
    since: str | None
    waiting: str | None
    priority: int


@dataclass
class Step:
    number: str
    title: str
    tasks: list[Task] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {state: sum(1 for task in self.tasks if task.state == state) for state in STATES}


@dataclass
class Plan:
    id: str
    title: str
    status: str
    steps: list[Step]


@dataclass
class Progress:
    plans: list[Plan]
    unassigned: Step
    cancelled: int
    idle: list[dict]

    def steps(self) -> Iterator[tuple[Plan | None, Step]]:
        for plan in self.plans:
            for step in plan.steps:
                yield plan, step
        if self.unassigned.tasks:
            yield None, self.unassigned

    def counts(self) -> dict[str, int]:
        return {state: sum(step.counts()[state] for _, step in self.steps()) for state in STATES}


def clip(text: str, limit: int = TITLE_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def listed(ccn: Ccn, *args: str) -> list[dict]:
    return json.loads(ccn(*args) or "null") or []


def shown(ccn: Ccn, summaries: list[dict]) -> dict[str, dict]:
    stale = [summary for summary in summaries if SHOWN.get(summary["id"], ("",))[0] != summary["updated_at"]]
    with ThreadPoolExecutor(WORKERS) as pool:
        for summary, full in zip(stale, pool.map(lambda summary: json.loads(ccn("task", "show", summary["id"], "--json")), stale), strict=True):
            SHOWN[summary["id"]] = (summary["updated_at"], full)
    return {summary["id"]: SHOWN[summary["id"]][1] for summary in summaries}


def steps_of(body: str) -> list[Step]:
    found: dict[str, Step] = {}
    for match in STEP_HEADING.finditer(body):
        found.setdefault(match["number"], Step(match["number"], f"{match['number']}. {match['title']}"))
    return list(found.values())


def owner_of(assignee: str | None) -> str | None:
    return assignee.split(" <", 1)[0] if assignee else None


def waiting_on(full: dict, blockers: list[dict]) -> str | None:
    if blockers:
        return "blocked by " + ", ".join(f"{clip(blocker['title'], TITLE_CHARS // 2)} ({blocker['id'][:7]})" for blocker in blockers)
    criteria = full.get("criteria") or []
    if failed := [criterion for criterion in criteria if criterion["status"] == "failed"]:
        return f"failed: {failed[0]['text']}"
    return next((criterion["text"] for criterion in criteria if criterion["status"] != "met"), None)


def task_of(full: dict, summaries: dict[str, dict]) -> Task:
    status = full["status"]
    blockers = [summaries[blocker] for blocker in full.get("blocked_by") or [] if summaries[blocker]["status"] not in FINISHED] if status != "done" else []
    failed = status != "done" and any(criterion["status"] == "failed" for criterion in full.get("criteria") or [])
    state = "done" if status == "done" else "blocked" if blockers or failed else status
    since = full.get("closed_at") if status == "done" else full.get("started_at") if status == "in_progress" else None
    return Task(full["id"], full["title"], state, owner_of(full.get("assignee")), since or full.get("created_at") or full["updated_at"], None if status == "done" else waiting_on(full, blockers), int(full.get("priority") or 0))


def unmapped(plan: Plan) -> Step:
    if not plan.steps or plan.steps[-1].number != UNMAPPED:
        plan.steps.append(Step(UNMAPPED, "No step label"))
    return plan.steps[-1]


def step_label(full: dict) -> str | None:
    return next((label.removeprefix(STEP_LABEL) for label in full.get("labels") or [] if label.startswith(STEP_LABEL)), None)


def read(ccn: Ccn, program: str | None, pinned: list[str] = []) -> Progress:
    summaries = {summary["id"]: summary for summary in listed(ccn, *EVERY_TASK)}
    labelled = {summary["id"] for summary in listed(ccn, *EVERY_TASK, "--label", f"program:{program}")} if program else set()
    program_plans = listed(ccn, "plan", "list", "--label", f"program:{program}", "--json") if program else []
    plan_ids = [*pinned, *(plan["id"] for plan in program_plans)]
    registered = [summary for summary in summaries.values() if summary["id"] in labelled or any(summary.get("plan", "").startswith(plan) for plan in plan_ids if summary.get("plan"))]
    full = shown(ccn, registered)
    held = {summary["plan"] for summary in registered if summary.get("plan")}
    wanted = list(dict.fromkeys([*pinned, *(plan["id"] for plan in program_plans if plan["id"] in held), *sorted(held)]))
    plans: dict[str, Plan] = {}
    for ident in wanted:
        record = json.loads(ccn("plan", "show", ident, "--json"))
        if record["id"] not in plans:
            plans[record["id"]] = Plan(record["id"], record["title"], record["status"], steps_of(record["body"]))
    unassigned = Step(UNASSIGNED, "Unassigned")
    cancelled = 0
    for summary in registered:
        record = full[summary["id"]]
        if record["status"] == "cancelled":
            cancelled += 1
            continue
        task = task_of(record, summaries)
        plan = plans.get(record.get("plan") or "")
        if plan is None:
            unassigned.tasks.append(task)
            continue
        number = step_label(record)
        step = next((step for step in plan.steps if step.number == number), None) or unmapped(plan)
        step.tasks.append(task)
    progress = Progress(list(plans.values()), unassigned, cancelled, [plan for plan in program_plans if plan["id"] not in plans])
    for _, step in progress.steps():
        step.tasks.sort(key=lambda task: (ORDER.index(task.state), task.priority, task.since or ""))
    return progress


def summary_lines(progress: Progress) -> list[str]:
    lines = []
    for plan in progress.plans:
        counts = {state: sum(step.counts()[state] for step in plan.steps) for state in STATES}
        left = [f"{'no step' if step.number == UNMAPPED else step.number} {step.counts()['done']}/{len(step.tasks)}" for step in plan.steps if step.tasks and step.counts()["done"] < len(step.tasks)]
        lines.append(f"- Plan `{plan.id[:7]}` {clip(plan.title)}: {counts['done']} of {sum(counts.values())} tasks done, {counts['blocked']} blocked" + (f"; steps open: {', '.join(left)}" if left else ""))
    if tasks := progress.unassigned.tasks:
        lines.append(f"- {sum(1 for task in tasks if task.state == 'done')} of {len(tasks)} drive tasks outside any plan done")
    return lines
