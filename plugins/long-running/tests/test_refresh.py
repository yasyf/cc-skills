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
            "routed_head": LISTED_HEAD,
            "label_head": LISTED_HEAD,
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
    assert fields["routed_head"] == LISTED_HEAD
    assert fields["label_head"] == LISTED_HEAD
    assert fields["first_seen"] == "2026-09-15T22:00:00Z"
    assert fields["head"] == MOVED_HEAD


def test_refresh_leaves_message_rows_alone(lock, red_routes):
    message = {"key": "msg/000001", "fields": {"kind": "idle", "pr": "21052", "head": MOVED_HEAD, "lane": "x", "text": "done", "state": "pending"}}
    shell = FakeShell(rows=[message], routes=red_routes)
    refresh(shell, lock, pr=["21052"])

    assert shell.fields("msg/000001")["state"] == "pending"
    assert "last_refresh" not in shell.fields("msg/000001")
    assert not [endpoint for endpoint in shell.endpoints() if "msg" in endpoint]


def test_closed_pr_keeps_its_row_marked_closed_for_the_desk_to_settle(lock, red_routes):
    closed = {"key": "19999", "fields": {"head": "dead", "test_state": "success", "mergeable_state": "clean"}}
    shell = FakeShell(rows=[closed], routes=red_routes)
    refresh(shell, lock)

    assert shell.fields("19999")["state"] == "closed"
    sync = next(argv for argv in shell.calls if argv[:3] == ["ccn", "ledger", "sync"])
    assert "--prune" not in sync


def test_an_unreachable_forge_writes_nothing_and_exits_non_zero(lock, red_routes, capsys):
    """A pass that graded half the rows must not write half a row set: a partial refresh
    reports a state nobody observed, and the caller believes it refreshed."""
    row = {"key": "21052", "fields": {"head": "dead", "test_state": "failure", "mergeable_state": "unknown"}}
    shell = FakeShell(rows=[row], routes=red_routes)
    shell.fail_gh = "pulls/21052"

    code = ledger.main(
        ["refresh", "--repo", "Forge-AI/monorepo", "--ledger", LEDGER, "--lock", str(lock)], shell=shell
    )

    assert code == 1
    assert not [argv for argv in shell.calls if argv[:3] == ["ccn", "ledger", "sync"]]
    assert "wrote nothing" in capsys.readouterr().err


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
