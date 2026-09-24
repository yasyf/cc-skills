from __future__ import annotations

import json

import pytest
from conftest import (
    EPOCH,
    HEAD,
    MOVED_HEAD,
    PR,
    check_run,
    comment,
    event,
    labeled,
    pull,
    surface,
    unlabeled,
)

GRAPHITE_WAITING = check_run(
    "Graphite / mergeability_check",
    status="in_progress",
    conclusion=None,
    title="This check will pass when downstack PRs merge",
    summary="- #24450 needs to be merged into dev before you can merge this PR.",
)


def test_green_clean_pr_is_all_green(poll):
    run = poll(surface(pull()))
    assert run.lines == ["CHECK build pass https://ci.example/build", "DONE all-green"]


def test_failed_check_is_checks_failed(poll):
    run = poll(surface(pull(), runs=[check_run("build", conclusion="failure")]))
    assert run.done == "DONE checks-failed"


def test_dirty_is_conflicted_on_the_first_read(poll):
    run = poll(surface(pull(mergeable=False, mergeable_state="dirty")))
    assert run.done == "DONE conflicted"
    assert run.passes == 1


def test_mergeable_false_is_conflicted_on_the_second_read(poll):
    stuck = surface(pull(mergeable=False, mergeable_state="blocked"))
    run = poll(stuck, stuck)
    assert run.done == "DONE conflicted"
    assert run.passes == 2


def test_unknown_mergeable_is_not_a_read(poll):
    unknown = surface(pull(mergeable=None, mergeable_state="unknown"))
    run = poll(unknown, unknown, unknown, unknown)
    assert run.done is None
    assert run.state["mergeable_false_reads"] == 0


def test_unknown_mergeable_holds_all_green_until_computed(poll):
    unknown = surface(pull(mergeable=None, mergeable_state="unknown"))
    run = poll(unknown, unknown, surface(pull()))
    assert run.done == "DONE all-green"
    assert run.passes == 3


def test_unknown_between_false_reads_neither_counts_nor_resets(poll):
    false = surface(pull(mergeable=False, mergeable_state="blocked"))
    unknown = surface(pull(mergeable=None, mergeable_state="unknown"))
    run = poll(false, unknown, false)
    assert run.done == "DONE conflicted"
    assert run.passes == 3


def test_conflicted_fires_once_per_head(poll):
    dirty = surface(pull(mergeable=False, mergeable_state="dirty"))
    assert poll(dirty).done == "DONE conflicted"
    rearmed = poll(dirty, dirty)
    assert rearmed.done is None
    moved = surface(pull(head=MOVED_HEAD, mergeable=False, mergeable_state="dirty"))
    assert poll(moved).done == "DONE conflicted"


def test_queued_green_pr_keeps_watching(poll):
    queued = surface(pull(labels=("merge",)), events=[labeled(1)])
    run = poll(queued, queued, queued)
    assert "QUEUED yasyf label" in run.lines
    assert run.done is None


def test_graphite_unlabel_is_evicted(poll):
    queued = surface(pull(labels=("merge",)), events=[labeled(1)])
    dropped = surface(pull(), events=[labeled(1), unlabeled(2)])
    run = poll(queued, dropped)
    assert run.lines[-2:] == ["QUEUED yasyf label", "DONE evicted unknown label removed by graphite-app[bot]"]


def test_push_while_queued_is_evicted_as_head_moved(poll):
    queued = surface(pull(labels=("merge",)), events=[labeled(1)])
    pushed = event(2, "head_ref_force_pushed", "yasyf", at="2026-09-23T23:22:38Z")
    dropped = surface(pull(head=MOVED_HEAD), events=[labeled(1), pushed, unlabeled(3)])
    run = poll(queued, dropped)
    assert run.done == f"DONE evicted head-moved {MOVED_HEAD[:12]}"


def test_eviction_names_the_downstack_pr_from_the_graphite_check(poll):
    queued = surface(pull(labels=("merge",)), events=[labeled(1)])
    dropped = surface(pull(), runs=[check_run("build"), GRAPHITE_WAITING], events=[labeled(1), unlabeled(2)])
    run = poll(queued, dropped)
    assert run.done == "DONE evicted downstack #24450"


def test_eviction_names_the_downstack_pr_from_merge_activity(poll):
    activity = comment(
        11,
        "## Merge activity\n\n"
        "- Sep 23, 11:17 PM UTC: yasyf added this pull request to the Graphite merge queue.\n"
        "- Sep 23, 11:29 PM UTC: The Graphite merge queue removed this pull request due to "
        "downstack failures on PR #24450.\n",
    )
    run = poll(surface(pull(), events=[labeled(1)], comments=[activity]))
    assert "QUEUED yasyf 11" in run.lines
    assert run.done == "DONE evicted downstack #24450"


def test_eviction_quotes_the_merge_activity_verdict(poll):
    activity = comment(
        12,
        "### Merge activity\n\n"
        "* **Sep 23, 11:17 PM UTC**: `yasyf` added this pull request to the "
        "[Graphite merge queue](https://app.graphite.com/merges).\n"
        "* **Sep 23, 11:31 PM UTC**: The [Graphite merge queue](https://app.graphite.com/merges) "
        "couldn't merge this PR because **it had merge conflicts**.\n",
    )
    run = poll(surface(pull(), events=[labeled(1), unlabeled(2)], comments=[activity]))
    assert run.done == (
        "DONE evicted conflicts The Graphite merge queue couldn't merge this PR because it had merge conflicts."
    )


def test_eviction_on_a_dirty_head_is_conflicts_and_does_not_refire(poll):
    queued = surface(pull(labels=("merge",)), events=[labeled(1)])
    dropped = surface(pull(mergeable=False, mergeable_state="dirty"), events=[labeled(1), unlabeled(2)])
    assert poll(queued, dropped).done == "DONE evicted conflicts mergeable_state=dirty"
    rearmed = poll(dropped, dropped)
    assert rearmed.done is None


def test_evicted_pr_waits_for_relabel_then_reports_the_landing(poll):
    queued = surface(pull(labels=("merge",)), events=[labeled(1)])
    dropped = surface(pull(), events=[labeled(1), unlabeled(2)])
    assert poll(queued, dropped).done.startswith("DONE evicted ")

    relabel = labeled(3, at="2026-09-23T23:40:00Z")
    requeued = surface(pull(labels=("merge",)), events=[labeled(1), unlabeled(2), relabel])
    squash = {"sha": "c" * 40, "commit": {"message": f"infra: ♻️ derive tenant constants (#{PR})\n\nbody"}}
    landed = surface(pull(state="closed", labels=("merge",)), commits=[squash])
    run = poll(dropped, dropped, requeued, landed)
    assert run.lines == ["QUEUED yasyf label", "DONE queue-merged"]
    assert run.passes == 4


def test_human_unlabel_is_unqueued_not_evicted(poll):
    queued = surface(pull(labels=("merge",)), events=[labeled(1)])
    removed = surface(pull(), events=[labeled(1), unlabeled(2, actor="yasyf")])
    run = poll(queued, removed)
    assert run.lines[-2:] == ["UNQUEUED yasyf", "DONE all-green"]


def test_unlabel_from_before_the_watch_started_is_ignored(poll, tmp_path):
    (tmp_path / "state.json").unlink()
    old = [labeled(1, at="2020-01-01T00:00:00Z"), unlabeled(2, at="2020-01-01T00:05:00Z")]
    run = poll(surface(pull(), events=old))
    assert run.lines[-1] == "DONE all-green"


def test_poll_uses_rest_only(poll):
    queued = surface(pull(labels=("merge",)), events=[labeled(1)])
    run = poll(queued, surface(pull(), events=[labeled(1), unlabeled(2)]))
    assert run.gh_calls
    assert all(call.startswith("api ") and "graphql" not in call for call in run.gh_calls)


def test_startup_failure_is_a_failed_check(poll):
    run = poll(surface(pull(), runs=[check_run("build", conclusion="startup_failure")]))
    assert run.done == "DONE checks-failed"


def test_unknown_conclusion_is_pending(poll):
    run = poll(surface(pull(), runs=[check_run("build", conclusion="stale")]))
    assert run.done is None


def test_removal_seen_before_the_label_snapshot_catches_up_still_evicts(poll):
    racing = surface(pull(labels=("merge",)), events=[labeled(1), unlabeled(2)])
    dropped = surface(pull(), events=[labeled(1), unlabeled(2)])
    run = poll(racing, dropped)
    assert run.done == "DONE evicted unknown label removed by graphite-app[bot]"


def test_failed_event_read_never_reports_green(poll):
    queued = surface(pull(labels=("merge",)), events=[labeled(1)])
    blind = surface(pull(), events=[labeled(1)])
    blind[f"issues/{PR}/events"] = "FAIL"
    run = poll(queued, blind, blind)
    assert run.done is None


def test_label_gone_before_its_event_arrives_holds_the_watch(poll):
    queued = surface(pull(labels=("merge",)), events=[labeled(1)])
    lagging = surface(pull(), events=[labeled(1)])
    dropped = surface(pull(), events=[labeled(1), unlabeled(2)])
    run = poll(queued, lagging, dropped)
    assert run.done == "DONE evicted unknown label removed by graphite-app[bot]"
    assert run.passes == 3


def test_requeue_after_a_drop_in_one_interval_is_not_evicted(poll):
    activity = comment(
        13,
        "### Merge activity\n\n"
        "* **Sep 23, 11:31 PM UTC**: The Graphite merge queue couldn't merge this PR because **it had merge conflicts**.\n"
        "* **Sep 23, 11:40 PM UTC**: `yasyf` added this pull request to the Graphite merge queue.\n",
    )
    run = poll(surface(pull(labels=("merge",)), events=[labeled(1)], comments=[activity]))
    assert "QUEUED yasyf 13" in run.lines
    assert run.done is None


def test_second_eviction_after_a_relabel_between_polls_is_reported(poll):
    queued = surface(pull(labels=("merge",)), events=[labeled(1)])
    first = surface(pull(), events=[labeled(1), unlabeled(2)])
    assert poll(queued, first).done.startswith("DONE evicted ")
    again = [labeled(1), unlabeled(2), labeled(3, at="2026-09-23T23:40:00Z"), unlabeled(4, at="2026-09-23T23:45:00Z")]
    run = poll(surface(pull(), events=again))
    assert run.done == "DONE evicted unknown label removed by graphite-app[bot]"


def test_fresh_watch_does_not_replay_old_merge_activity(poll, tmp_path):
    (tmp_path / "state.json").unlink()
    history = (
        "### Merge activity\n\n"
        "* **Sep 23, 10:49 PM UTC**: The Graphite merge queue couldn't merge this PR because **it had merge conflicts**.\n"
    )
    edited = history + "* **Sep 23, 11:50 PM UTC**: The merge label 'merge' was detected.\n"
    computing = pull(mergeable=None, mergeable_state="unknown")
    run = poll(
        surface(computing, comments=[comment(14, history)]),
        surface(computing, comments=[comment(14, edited)]),
        surface(pull(), comments=[comment(14, edited)]),
    )
    assert run.done == "DONE all-green"
    assert run.passes == 3


PENDING = surface(pull(), runs=[check_run("build", status="in_progress", conclusion=None)])


def test_window_elapsed_ends_the_round_and_the_next_round_keeps_started_at(poll):
    env = {"PR_POLL_WINDOW": "1", "FAKE_SLEEP": "1.1"}
    first = poll(PENDING, PENDING, PENDING, env=env)
    assert first.done == "DONE window-elapsed"
    assert first.state["started_at"] > 0
    second = poll(PENDING, env={"PR_POLL_WINDOW": "600"})
    assert second.done is None
    assert second.state["started_at"] == first.state["started_at"]


def test_deadline_counts_from_the_state_files_started_at(poll, tmp_path):
    state = tmp_path / "state.json"
    seeded = json.loads(state.read_text())
    seeded["started_at"] = 1000
    state.write_text(json.dumps(seeded))
    run = poll(PENDING, env={"PR_POLL_DEADLINE": "10"})
    assert run.done == "DONE deadline-still-open"
    assert run.state["started_at"] == 1000
QUEUE_URL = "https://app.graphite.com/merges?org=Forge-AI&repo=monorepo"


def graphite_pr(number: int) -> str:
    return f"https://app.graphite.com/github/pr/Forge-AI/monorepo/{number}"


def merge_activity(comment_id: int, *bullets: str) -> dict:
    body = "### Merge activity\n\n" + "".join(f"* **Sep 24, 9:36 AM UTC**: {bullet}\n" for bullet in bullets)
    return comment(comment_id, body, author="graphite-app[bot]", at="2026-09-24T09:36:06Z")


ENQUEUED = f"`yasyf` added this pull request to the [Graphite merge queue]({QUEUE_URL})."
CONFLICTED = f"The [Graphite merge queue]({QUEUE_URL}) couldn't merge this PR because **it had merge conflicts**."


def test_ui_enqueue_then_conflict_drop_without_a_label_is_evicted(poll):
    stacked = pull(base="graphite-base/24549")
    run = poll(
        surface(stacked, comments=[merge_activity(5811623691, ENQUEUED)]),
        surface(stacked, comments=[merge_activity(5811623691, ENQUEUED, CONFLICTED)]),
    )
    assert run.lines[-2:] == [
        "QUEUED yasyf 5811623691",
        "DONE evicted conflicts The Graphite merge queue couldn't merge this PR because it had merge conflicts.",
    ]
    assert run.passes == 2


@pytest.mark.parametrize(
    ("bullet", "done"),
    [
        (CONFLICTED, "conflicts The Graphite merge queue couldn't merge this PR because it had merge conflicts."),
        (
            "This pull request was removed from the merge queue due to merge conflicts, "
            "please rebase before retrying merge.",
            "conflicts This pull request was removed from the merge queue due to merge conflicts, pleas",
        ),
        (
            f"This pull request can not be added to the [Graphite merge queue]({QUEUE_URL}). "
            "Please try rebasing and resubmitting to merge when ready. ",
            "conflicts This pull request can not be added to the Graphite merge queue. Please try rebas",
        ),
        (
            f'[Graphite]({graphite_pr(23278)}) disabled "merge when ready" on this PR due to: '
            "a merge conflict with the target branch; resolve the conflict and try again..",
            'conflicts Graphite disabled "merge when ready" on this PR due to: a merge conflict with th',
        ),
        (
            f"The [Graphite merge queue]({QUEUE_URL}) removed this pull request due to "
            f"**downstack failures on PR #[23280]({graphite_pr(23280)})**.",
            "downstack #23280",
        ),
        (
            f"The [Graphite merge queue]({QUEUE_URL}) removed this pull request due to "
            f"**removal of a downstack PR #[23278]({graphite_pr(23278)})**.",
            "downstack #23278",
        ),
        (
            f"The [Graphite merge queue]({QUEUE_URL}) couldn't merge this PR because "
            "**it was not satisfying all requirements** (Failed CI (buildkite/test)).",
            "failed-ci buildkite/test",
        ),
    ],
)
def test_graphite_drop_bullet_is_classified(poll, bullet, done):
    run = poll(surface(pull(), comments=[merge_activity(21, ENQUEUED, bullet)]))
    assert run.done == f"DONE evicted {done}"


@pytest.mark.parametrize(
    "bullet",
    [
        f"The merge label 'merge' was detected. This PR will be added to the [Graphite merge queue]({QUEUE_URL}) "
        "once it meets the requirements.",
        f"The merge label 'merge' was removed. This PR will no longer be merged by the [Graphite merge queue]({QUEUE_URL})",
        f"CI is running for this pull request on a draft pull request ([#24065]({graphite_pr(24065)})) "
        "due to your merge queue CI optimization settings.",
    ],
)
def test_graphite_bookkeeping_bullet_is_neither_queued_nor_dropped(poll, bullet):
    run = poll(surface(pull(), comments=[merge_activity(22, bullet)]))
    assert not any(line.startswith("QUEUED ") for line in run.lines)
    assert run.done == "DONE all-green"


def test_ui_dequeue_without_a_label_is_unqueued_not_evicted(poll):
    enqueued = f"`andrewmbenton` added this pull request to the [Graphite merge queue]({QUEUE_URL})."
    dequeued = f"`andrewmbenton`  removed this pull request from the [Graphite merge queue]({QUEUE_URL})."
    run = poll(
        surface(pull(), comments=[merge_activity(5790504993, enqueued)]),
        surface(pull(), comments=[merge_activity(5790504993, enqueued, dequeued)]),
    )
    assert run.lines[-3:] == ["QUEUED andrewmbenton 5790504993", "UNQUEUED andrewmbenton", "DONE all-green"]
    assert run.passes == 2


def test_ui_dequeue_consumes_the_bot_unlabel_that_follows_it(poll):
    dequeued = f"`yasyf`  removed this pull request from the [Graphite merge queue]({QUEUE_URL})."
    queued = surface(pull(labels=("merge",)), events=[labeled(1)], comments=[merge_activity(5791423562, ENQUEUED)])
    racing = surface(
        pull(labels=("merge",)), events=[labeled(1)], comments=[merge_activity(5791423562, ENQUEUED, dequeued)]
    )
    unlabelled = surface(
        pull(), events=[labeled(1), unlabeled(2)], comments=[merge_activity(5791423562, ENQUEUED, dequeued)]
    )
    run = poll(queued, racing, unlabelled)
    assert "UNQUEUED yasyf" in run.lines
    assert not any(line.startswith("DONE evicted") for line in run.lines)
    assert run.done == "DONE all-green"
    assert run.passes == 3


def test_relabel_after_a_ui_dequeue_queues_again(poll):
    dequeued = f"`yasyf`  removed this pull request from the [Graphite merge queue]({QUEUE_URL})."
    dropped = surface(
        pull(labels=("merge",)), events=[labeled(1)], comments=[merge_activity(5791423562, ENQUEUED, dequeued)]
    )
    relabelled = surface(
        pull(labels=("merge",)),
        events=[labeled(1), unlabeled(2), labeled(3, at="2026-09-23T23:40:00Z")],
        comments=[merge_activity(5791423562, ENQUEUED, dequeued)],
    )
    run = poll(dropped, relabelled, relabelled)
    assert run.lines[-1] == "QUEUED yasyf label"
    assert run.done is None


def test_watch_resumed_from_a_schema_1_state_file_on_a_ui_queued_pr_reports_the_drop(poll, tmp_path):
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "head_at_last_pass": HEAD,
                "checks_seen": {"build": "pass"},
                "merge_activity": {"5811623691": 1},
                "attempts": {},
                "watermarks": {"comments": "2026-09-24T09:36:06Z", "reviews": EPOCH},
            }
        )
    )
    stacked = pull(base="graphite-base/24549")
    run = poll(
        surface(stacked, comments=[merge_activity(5811623691, ENQUEUED)]),
        surface(stacked, comments=[merge_activity(5811623691, ENQUEUED, CONFLICTED)]),
    )
    assert run.lines == [
        "DONE evicted conflicts The Graphite merge queue couldn't merge this PR because it had merge conflicts."
    ]
    assert run.passes == 2


def test_fresh_watch_on_a_ui_queued_pr_holds_through_green(poll, tmp_path):
    (tmp_path / "state.json").unlink()
    queued = surface(pull(), comments=[merge_activity(5811623691, ENQUEUED)])
    run = poll(queued, queued, queued, queued)
    assert run.done is None
    assert run.state["queue"]["queued"] is True


def test_ui_requeue_after_a_human_unlabel_between_polls_stays_queued(poll):
    labelled = surface(pull(labels=("merge",)), events=[labeled(1)])
    requeued = surface(
        pull(),
        events=[labeled(1), unlabeled(2, actor="yasyf")],
        comments=[merge_activity(5811623691, ENQUEUED)],
    )
    run = poll(labelled, requeued, requeued)
    assert "QUEUED yasyf 5811623691" in run.lines
    assert not any(line.startswith("UNQUEUED ") for line in run.lines)
    assert run.done is None


def test_requeue_and_drop_in_one_interval_after_an_eviction_reports_again(poll):
    assert poll(surface(pull(), comments=[merge_activity(21, ENQUEUED, CONFLICTED)])).done.startswith(
        "DONE evicted conflicts "
    )
    again = merge_activity(21, ENQUEUED, CONFLICTED, ENQUEUED, CONFLICTED)
    run = poll(surface(pull(), comments=[again]))
    assert run.done == (
        "DONE evicted conflicts The Graphite merge queue couldn't merge this PR because it had merge conflicts."
    )


def test_failed_comment_read_never_reports_green(poll):
    pending = surface(pull(), runs=[check_run("build", status="in_progress", conclusion=None)])
    blind = surface(pull())
    blind[f"issues/{PR}/comments"] = "FAIL"
    run = poll(pending, blind, blind)
    assert run.done is None


def test_fresh_watch_after_a_ui_dequeue_ignores_the_trailing_bot_unlabel(poll, tmp_path):
    (tmp_path / "state.json").unlink()
    dequeued = f"`yasyf`  removed this pull request from the [Graphite merge queue]({QUEUE_URL})."
    activity = merge_activity(5791423562, ENQUEUED, dequeued)
    lingering = surface(pull(labels=("merge",)), events=[labeled(1)], comments=[activity])
    cleaned = surface(pull(), events=[labeled(1), unlabeled(2, at="2099-01-01T00:00:00Z")], comments=[activity])
    run = poll(lingering, cleaned)
    assert not any(line.startswith(("QUEUED ", "DONE evicted")) for line in run.lines)
    assert run.done == "DONE all-green"


def test_first_pass_that_fails_before_reading_state_keeps_the_deadline(poll):
    blind = surface(pull())
    blind[f"issues/{PR}/events"] = "FAIL"
    run = poll(blind, PENDING, env={"PR_POLL_DEADLINE": "3600"})
    assert run.done is None
    assert run.passes == 2
