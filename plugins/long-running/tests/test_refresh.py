from __future__ import annotations

import ledger
from conftest import DIRTY_HEAD, FakeShell, LEDGER, LISTED_HEAD, MOVED_HEAD


def refresh(shell, lock, lane=()):
    argv = ["refresh", "--repo", "Forge-AI/monorepo", "--ledger", LEDGER, "--lock", str(lock)]
    for pair in lane:
        argv += ["--lane", pair]
    assert ledger.main(argv, shell) == 0


def test_pagination_walks_past_the_first_hundred(lock, red_routes):
    shell = FakeShell(routes=red_routes)
    refresh(shell, lock)

    keys = {row["key"] for row in shell.store["rows"]}
    assert len(keys) == 102
    assert {"21052", "20970", "20961"} <= keys
    listings = [endpoint for endpoint in shell.endpoints() if "pulls?" in endpoint]
    assert any("page=2" in endpoint for endpoint in listings), "a full page did not pull the next one"
    assert not any("page=3" in endpoint for endpoint in listings), "a short page did not end pagination"


def test_moved_head_is_graded_and_recorded_not_the_listed_head(lock, red_routes):
    shell = FakeShell(routes=red_routes)
    refresh(shell, lock)

    fields = shell.fields("21052")
    assert fields["head"] == MOVED_HEAD
    assert fields["test_state"] == "failure"
    endpoints = shell.endpoints()
    assert f"repos/Forge-AI/monorepo/commits/{MOVED_HEAD}/status" in endpoints
    assert f"repos/Forge-AI/monorepo/commits/{LISTED_HEAD}/status" not in endpoints


def test_row_carries_the_whole_graded_schema(lock, red_routes):
    shell = FakeShell(routes=red_routes)
    refresh(shell, lock)

    fields = shell.fields("21052")
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
    refresh(shell, lock)

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


def test_sync_prunes_a_pr_that_is_no_longer_open(lock, red_routes):
    closed = {"key": "19999", "fields": {"head": "dead", "test_state": "success", "mergeable_state": "clean"}}
    shell = FakeShell(rows=[closed], routes=red_routes)
    refresh(shell, lock)

    assert "19999" not in {row["key"] for row in shell.store["rows"]}
    sync = next(argv for argv in shell.calls if argv[:3] == ["ccn", "ledger", "sync"])
    assert "--prune" in sync


def test_lane_flag_stamps_only_the_row_it_names(lock, red_routes):
    shell = FakeShell(routes=red_routes)
    refresh(shell, lock, lane=["21052=landing-desk"])

    assert shell.fields("21052")["lane"] == "landing-desk"
    assert "lane" not in shell.fields("20970")


def test_dirty_pr_is_recorded_as_dirty(lock, red_routes):
    shell = FakeShell(routes=red_routes)
    refresh(shell, lock)

    assert shell.fields("20961")["mergeable_state"] == "dirty"
    assert shell.fields("20961")["head"] == DIRTY_HEAD


def test_refresh_never_calls_graphql(lock, red_routes):
    shell = FakeShell(routes=red_routes)
    refresh(shell, lock)

    assert not [argv for argv in shell.calls if "graphql" in " ".join(argv)]
