from __future__ import annotations

import plans
import pytest
from ccn_plans import OTHER_PLAN_ID, PLAN_ID, PROGRAM, CcnPlans, plan, task


@pytest.fixture(autouse=True)
def fresh_cache():
    plans.SHOWN.clear()


def by_step(progress: plans.Progress) -> dict[str, list[str]]:
    return {f"{owner.id[:7] if owner else '-'}:{step.number}": [f"{item.title}={item.state}" for item in step.tasks] for owner, step in progress.steps()}


def test_tasks_join_the_step_their_label_names_else_their_plans_unmapped_step_else_unassigned():
    ccn = CcnPlans(
        [
            task("a", "Ship the reader", plan=PLAN_ID, labels=(f"program:{PROGRAM}", "step:1"), status="done", closed_at="2026-10-02T00:00:00Z"),
            task("b", "Backfill rows", plan=PLAN_ID, labels=("step:2",), status="in_progress", assignee="backfill lane <lane@x>", started_at="2026-10-03T00:00:00Z"),
            task("c", "No step yet", plan=PLAN_ID, labels=(f"program:{PROGRAM}", "step:9")),
            task("d", "Loose end"),
            task("e", "Someone else's", labels=("program:other",)),
        ],
        [plan()],
    )

    progress = plans.read(ccn, PROGRAM)

    assert by_step(progress) == {
        "fc8714e:1": ["Ship the reader=done"],
        "fc8714e:2": ["Backfill rows=in_progress"],
        "fc8714e:unmapped": ["No step yet=open"],
        "-:unassigned": ["Loose end=open"],
    }
    assert [step.title for step in progress.plans[0].steps] == ["1. Readers first", "2. Delete the old store", "No step label"]
    assert progress.counts() == {"done": 1, "in_progress": 1, "blocked": 0, "open": 2}
    backfill = progress.plans[0].steps[1].tasks[0]
    assert (backfill.owner, backfill.since) == ("backfill lane", "2026-10-03T00:00:00Z")


def test_an_unfinished_blocker_or_a_failed_criterion_blocks_a_task_and_says_what_it_waits_on():
    ccn = CcnPlans(
        [
            task("a", "Backfill rows", criteria=[{"id": "1", "text": "every row copied", "status": "met"}, {"id": "2", "text": "readers switched", "status": "pending"}]),
            task("b", "Drop the column", blocked_by=["a" * 40]),
            task("c", "Delete the reader", blocked_by=["f" * 40]),
            task("f", "Landed already", status="done", labels=()),
            task("g", "Prove the floor", criteria=[{"id": "3", "text": "sandbox proof passes", "status": "failed"}]),
            task("h", "Dropped", status="cancelled"),
        ]
    )

    progress = plans.read(ccn, PROGRAM)

    waiting = {item.title: (item.state, item.waiting) for item in progress.unassigned.tasks}
    assert waiting == {
        "Backfill rows": ("open", "readers switched"),
        "Drop the column": ("blocked", f"blocked by Backfill rows ({'a' * 7})"),
        "Delete the reader": ("open", None),
        "Prove the floor": ("blocked", "failed: sandbox proof passes"),
    }
    assert [item.state for item in progress.unassigned.tasks] == ["blocked", "blocked", "open", "open"]
    assert progress.cancelled == 1


def test_only_plans_holding_tasks_or_named_outright_are_drawn():
    ccn = CcnPlans([task("a", "Ship the reader", plan=PLAN_ID)], [plan(), plan(OTHER_PLAN_ID, "Idle plan")])

    assert [item.title for item in plans.read(ccn, PROGRAM).plans] == ["Migration"]
    assert [item["title"] for item in plans.read(ccn, PROGRAM).idle] == ["Idle plan"]
    assert [item.title for item in plans.read(ccn, PROGRAM, [OTHER_PLAN_ID[:7]]).plans] == ["Idle plan", "Migration"]


def test_a_task_is_shown_again_only_after_it_changes():
    tasks = [task("a", "Ship the reader"), task("b", "Backfill rows")]
    ccn = CcnPlans(tasks)
    plans.read(ccn, PROGRAM)
    ccn.tasks["b" * 40] |= {"status": "in_progress", "updated_at": "2026-10-04T00:00:00Z"}
    ccn.calls.clear()

    progress = plans.read(ccn, PROGRAM)

    assert [call for call in ccn.calls if call[:2] == ("task", "show")] == [("task", "show", "b" * 40, "--json")]
    assert {item.title: item.state for item in progress.unassigned.tasks} == {"Ship the reader": "open", "Backfill rows": "in_progress"}


def test_summary_lines_name_each_plan_and_its_open_steps():
    ccn = CcnPlans([task("a", "Ship the reader", plan=PLAN_ID, labels=("step:1",), status="done"), task("b", "Backfill", plan=PLAN_ID, labels=("step:2",)), task("d", "Loose end")], [plan()])

    assert plans.summary_lines(plans.read(ccn, PROGRAM)) == ["- Plan `fc8714e` Migration: 1 of 2 tasks done, 0 blocked; steps open: 2 0/1", "- 0 of 1 drive tasks outside any plan done"]
