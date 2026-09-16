from __future__ import annotations

import ledger
from conftest import DIRTY_HEAD, FakeShell, GREEN_HEAD, LEDGER, MOVED_HEAD

ERROR = "Invariant violation: build-sand left sand untouched, so it stored nothing to fetch"

RED = {
    "key": "21052",
    "fields": {
        "head": MOVED_HEAD,
        "base": "dev",
        "lane": "accounts",
        "test_state": "failure",
        "mergeable_state": "unknown",
    },
}
DIRTY = {
    "key": "20961",
    "fields": {
        "head": DIRTY_HEAD,
        "base": "dev",
        "lane": "p2-edge-rows",
        "test_state": "success",
        "mergeable_state": "dirty",
    },
}
BLOCKED = {
    "key": "20970",
    "fields": {
        "head": GREEN_HEAD,
        "base": "dev",
        "lane": "signer-row",
        "test_state": "success",
        "mergeable_state": "blocked",
    },
}
GREEN = {
    "key": "20993",
    "fields": {
        "head": "aa9123696d0d1f2a3b4c5d6e7f8091a2b3c4d5e6",
        "base": "dev",
        "lane": "p5-cut",
        "test_state": "success",
        "mergeable_state": "clean",
    },
}


def route(shell, capsys=None, *extra):
    argv = ["route", "--repo", "Forge-AI/monorepo", "--ledger", LEDGER, *extra]
    assert ledger.main(argv, shell) == 0
    return capsys.readouterr().out if capsys else ""


def test_failing_row_message_carries_the_first_real_error_to_its_lane(capsys, red_routes):
    shell = FakeShell(rows=[RED], routes=red_routes)
    printed = route(shell, capsys)

    assert printed.startswith("to accounts:\nDESK #21052 aa11bb22c: fix: ")
    assert ERROR in printed
    assert "3-line report" in printed
    assert "The command exited with status" not in printed
    assert "https://buildkite.com/forge/test/builds/8794" in printed


def test_route_writes_nothing_to_the_pull_request(red_routes):
    shell = FakeShell(rows=[RED], routes=red_routes)
    route(shell)

    assert not [argv for argv in shell.calls if "--method" in argv]


def test_route_records_head_job_lane_and_time_on_the_row(red_routes):
    shell = FakeShell(rows=[RED], routes=red_routes)
    route(shell)

    fields = shell.fields("21052")
    assert fields["routed_head"] == MOVED_HEAD
    assert fields["routed_lane"] == "accounts"
    assert fields["routed_job"].startswith("fix: ")
    assert ERROR[:40] in fields["routed_job"]
    assert fields["next_action"] == fields["routed_job"]
    assert fields["routed_at"]


def test_same_head_and_job_are_never_routed_twice(capsys, red_routes):
    shell = FakeShell(rows=[RED], routes=red_routes)
    route(shell)
    capsys.readouterr()
    printed = route(shell, capsys)

    assert "already routed to accounts at" in printed
    assert "to accounts:" not in printed


def test_moved_head_is_routed_again(capsys, red_routes):
    stale = {"key": "21052", "fields": dict(RED["fields"], routed_head="0" * 40, routed_job="fix: old")}
    shell = FakeShell(rows=[stale], routes=red_routes)
    printed = route(shell, capsys)

    assert ERROR in printed
    assert shell.fields("21052")["routed_head"] == MOVED_HEAD


def test_explicit_job_routes_one_pr_without_reading_the_forge(capsys):
    shell = FakeShell(rows=[GREEN])
    printed = route(shell, capsys, "--pr", "20993", "--job", "plan comment missing for this head")

    assert printed.startswith("to p5-cut:\nDESK #20993 aa9123696: plan comment missing for this head\n")
    assert not [argv for argv in shell.calls if argv[0] in ("gh", "bk")]
    assert shell.fields("20993")["routed_job"] == "plan comment missing for this head"


def test_a_different_job_on_the_same_head_is_new_information(capsys):
    shell = FakeShell(rows=[GREEN])
    route(shell, capsys, "--pr", "20993", "--job", "first")
    printed = route(shell, capsys, "--pr", "20993", "--job", "second")

    assert "to p5-cut:" in printed
    assert shell.fields("20993")["routed_job"] == "second"


def test_route_uses_the_reported_head_before_the_first_refresh(capsys):
    reported = {"key": "20993", "fields": {"reported_head": GREEN_HEAD, "lane": "p5-cut"}}
    shell = FakeShell(rows=[reported])
    printed = route(shell, capsys, "--pr", "20993", "--job", "rebase onto dev")

    assert f"DESK #20993 {GREEN_HEAD[:9]}" in printed
    assert shell.fields("20993")["routed_head"] == GREEN_HEAD


def test_dirty_row_gets_a_rebase_instruction_without_reading_a_log(capsys):
    shell = FakeShell(rows=[DIRTY])
    printed = route(shell, capsys)

    assert "to p2-edge-rows:\nDESK #20961 ab0de038c: rebase onto dev" in printed
    assert "Rebase it onto the base branch" in printed
    assert not [argv for argv in shell.calls if argv[0] == "bk"]


def test_blocked_row_names_the_held_requirement(capsys):
    shell = FakeShell(rows=[BLOCKED])
    printed = route(shell, capsys)

    assert "Mergeable state is `blocked`" in printed
    assert shell.fields("20970")["routed_job"] == "unblock: blocked"


def test_green_row_is_never_swept(capsys):
    shell = FakeShell(rows=[GREEN])
    printed = route(shell, capsys)

    assert "#20993" not in printed
    assert not [argv for argv in shell.calls if argv[:4] == ["ccn", "ledger", "row", "set"]]


def test_dry_run_prints_and_records_nothing(capsys, red_routes):
    shell = FakeShell(rows=[RED, DIRTY], routes=red_routes)
    printed = route(shell, capsys, "--dry-run")

    assert not [argv for argv in shell.calls if argv[:4] == ["ccn", "ledger", "row", "set"]]
    assert "routed_head" not in shell.fields("21052")
    assert ERROR in printed
    assert "#20961" in printed


def test_killed_job_reports_no_error_line_rather_than_a_job_name(capsys, red_routes):
    routes = dict(red_routes, **{"log:01a0a85c-6c8f-4f3f-a875-f2bc0dc46e1d": "job-log-killed.txt"})
    shell = FakeShell(rows=[RED], routes=routes)
    printed = route(shell, capsys)

    assert "has no error line" in printed


def test_job_id_comes_from_the_build_when_the_url_carries_none(capsys, red_routes):
    routes = dict(red_routes, **{f"checks:{MOVED_HEAD}": "check-runs-no-ai-review.json"})
    shell = FakeShell(rows=[RED], routes=routes)
    printed = route(shell, capsys)

    assert [argv for argv in shell.calls if argv[1:3] == ["build", "view"]]
    assert ERROR in printed


def test_route_verdict_is_callable_on_its_own(red_routes):
    shell = FakeShell(rows=[RED], routes=red_routes)
    gh = ledger.Github(shell, "Forge-AI/monorepo")
    text, action = ledger.route_verdict(shell, gh, RED["fields"])

    assert ERROR in text
    assert action.startswith("fix: ")


def test_route_never_calls_graphql(red_routes):
    shell = FakeShell(rows=[RED, DIRTY, BLOCKED], routes=red_routes)
    route(shell)

    assert not [argv for argv in shell.calls if "graphql" in " ".join(argv)]
