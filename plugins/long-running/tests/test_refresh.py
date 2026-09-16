from __future__ import annotations

import ledger
from conftest import DIRTY_HEAD, FakeShell, LEDGER, LISTED_HEAD, MOVED_HEAD


def refresh(shell, lock, lane=(), pr=()):
    argv = ["refresh", "--repo", "Forge-AI/monorepo", "--ledger", LEDGER, "--lock", str(lock)]
    for pair in lane:
        argv += ["--lane", pair]
    for number in pr:
        argv += ["--pr", number]
    assert ledger.main(argv, shell) == 0


def test_refresh_grades_the_rows_it_holds_and_the_prs_named_never_the_repository(lock, red_routes):
    held = {"key": "20961", "fields": {"head": "stale", "lane": "p2-edge-rows"}}
    shell = FakeShell(rows=[held], routes=red_routes)
    refresh(shell, lock, pr=["21052"])

    assert {row["key"] for row in shell.store["rows"]} == {"20961", "21052"}
    assert shell.fields("20961")["head"] == DIRTY_HEAD
    assert shell.fields("20961")["lane"] == "p2-edge-rows"
    assert shell.fields("21052")["head"] == MOVED_HEAD
    assert not [endpoint for endpoint in shell.endpoints() if endpoint.endswith("/pulls") or "/pulls?" in endpoint]


def test_moved_head_is_graded_and_recorded_not_the_listed_head(lock, red_routes):
    shell = FakeShell(routes=red_routes)
    refresh(shell, lock, pr=["21052"])

    fields = shell.fields("21052")
    assert fields["head"] == MOVED_HEAD
    assert fields["test_state"] == "failure"
    endpoints = shell.endpoints()
    assert f"repos/Forge-AI/monorepo/commits/{MOVED_HEAD}/status" in endpoints
    assert f"repos/Forge-AI/monorepo/commits/{LISTED_HEAD}/status" not in endpoints


def test_row_carries_the_whole_graded_schema(lock, red_routes):
    shell = FakeShell(routes=red_routes)
    refresh(shell, lock, pr=["21052"])

    fields = shell.fields("21052")
    assert fields["state"] == "open"
    assert fields["base"] == "dev"
    assert fields["branch"] == "infra/accounts-register"
    assert fields["author"] == "yasyf"
    assert fields["mergeable_state"] == "unknown"
    assert fields["labels"] == "configuration,docs"
    assert fields["ai_review"] == "success"
    assert fields["changed_files"] == "38"
    assert fields["first_seen"] == fields["last_refresh"]


def test_ai_review_absent_when_no_such_check_run(lock):
    shell = FakeShell(routes={f"checks:{MOVED_HEAD}": "check-runs-no-ai-review.json"})
    refresh(shell, lock, pr=["21052"])

    assert shell.fields("21052")["ai_review"] == ledger.AI_REVIEW_ABSENT


def test_refresh_merges_and_never_clobbers_orchestrator_fields(lock, red_routes):
    held = {
        "key": "21052",
        "fields": {
            "head": LISTED_HEAD,
            "hold_reason": "waiting on the accounts import class",
            "hold_since": "2026-09-16T01:00:00Z",
            "lane": "adopt-20947",
            "declared_intent": "settings files only, no roster change",
            "last_graded_head": LISTED_HEAD,
            "first_seen": "2026-09-15T22:00:00Z",
        },
    }
    shell = FakeShell(rows=[held], routes=red_routes)
    refresh(shell, lock)

    fields = shell.fields("21052")
    assert fields["hold_reason"] == "waiting on the accounts import class"
    assert fields["hold_since"] == "2026-09-16T01:00:00Z"
    assert fields["lane"] == "adopt-20947"
    assert fields["declared_intent"] == "settings files only, no roster change"
    assert fields["last_graded_head"] == LISTED_HEAD
    assert fields["first_seen"] == "2026-09-15T22:00:00Z"
    assert fields["head"] == MOVED_HEAD


SQUASH_SHA = "9f81c0e4a2b7d6c5e4f3a2b1c0d9e8f7a6b5c4d3"


def closed_row():
    return {"key": "19999", "fields": {"head": "dead", "test_state": "success", "mergeable_state": "clean"}}


def test_a_closed_pr_with_a_squash_on_the_trunk_reads_merged(lock, red_routes):
    shell = FakeShell(
        rows=[closed_row()],
        routes=red_routes,
        squashes={"19999": (SQUASH_SHA, "2026-09-16T04:10:00+00:00", "infra: retire the dead roles (#19999)")},
    )
    refresh(shell, lock)

    fields = shell.fields("19999")
    assert fields["state"] == "merged"
    assert fields["landed_sha"] == SQUASH_SHA
    assert fields["landed_at"] == "2026-09-16T04:10:00+00:00"


def test_a_closed_pr_with_no_squash_is_its_own_state_not_merged(lock, red_routes):
    """A PR auto-closed when its base branch was deleted reads exactly like a landed
    one on the forge. Only the trunk separates them, and calling it merged is how an
    approved fix sits absent from dev for hours while its row looks settled."""
    shell = FakeShell(rows=[closed_row()], routes=red_routes, squashes={})
    refresh(shell, lock)

    fields = shell.fields("19999")
    assert fields["state"] == "closed-without-squash"
    assert fields["landed_sha"] == ""


def test_a_squash_naming_another_pr_does_not_count_as_this_one(lock, red_routes):
    """`--grep` matches the body too, so the subject is the gate."""
    shell = FakeShell(
        rows=[closed_row()],
        routes=red_routes,
        squashes={"19999": (SQUASH_SHA, "2026-09-16T04:10:00+00:00", "infra: revert (#19999) and re-land it behind the gate")},
    )
    refresh(shell, lock)

    assert shell.fields("19999")["state"] == "closed-without-squash"


def test_the_trunk_is_fetched_once_before_it_is_read(lock, red_routes):
    """A ref the checkout last saw minutes ago reports a landing that has not happened."""
    shell = FakeShell(rows=[closed_row()], routes=red_routes, squashes={})
    refresh(shell, lock)

    assert shell.fetched == ["dev"]
    fetch_at = next(i for i, argv in enumerate(shell.calls) if argv[:2] == ["git", "fetch"])
    log_at = next(i for i, argv in enumerate(shell.calls) if argv[:2] == ["git", "log"])
    assert fetch_at < log_at


def test_an_open_pr_never_reads_the_trunk(lock, red_routes):
    shell = FakeShell(routes=red_routes)
    refresh(shell, lock, pr=["21052"])

    assert shell.fields("21052")["state"] == "open"
    assert not [argv for argv in shell.calls if argv[0] == "git"]


def test_a_closed_row_survives_the_sync(lock, red_routes):
    shell = FakeShell(rows=[closed_row()], routes=red_routes, squashes={})
    refresh(shell, lock)

    sync = next(argv for argv in shell.calls if argv[:3] == ["ccn", "ledger", "sync"])
    assert "--prune" not in sync


def test_lane_flag_stamps_only_the_row_it_names(lock, red_routes):
    shell = FakeShell(routes=red_routes)
    refresh(shell, lock, lane=["21052=landing-desk"], pr=["21052", "20970"])

    assert shell.fields("21052")["lane"] == "landing-desk"
    assert "lane" not in shell.fields("20970")


def test_dirty_pr_is_recorded_as_dirty(lock, red_routes):
    shell = FakeShell(routes=red_routes)
    refresh(shell, lock, pr=["20961"])

    assert shell.fields("20961")["mergeable_state"] == "dirty"
    assert shell.fields("20961")["head"] == DIRTY_HEAD


def test_refresh_never_calls_graphql(lock, red_routes):
    shell = FakeShell(routes=red_routes)
    refresh(shell, lock, pr=["21052", "20961", "20970"])

    assert not [argv for argv in shell.calls if "graphql" in " ".join(argv)]
