from __future__ import annotations

import ledger
from conftest import DIRTY_HEAD, FakeShell, GREEN_HEAD, LEDGER, MOVED_HEAD

ERROR = "Invariant violation: build-sand left sand untouched, so it stored nothing to fetch"

RED = {
    "key": "21052",
    "fields": {
        "head": MOVED_HEAD,
        "base": "dev",
        "test_state": "failure",
        "mergeable_state": "unknown",
    },
}
DIRTY = {
    "key": "20961",
    "fields": {
        "head": DIRTY_HEAD,
        "base": "dev",
        "test_state": "success",
        "mergeable_state": "dirty",
    },
}
BLOCKED = {
    "key": "20970",
    "fields": {
        "head": GREEN_HEAD,
        "base": "dev",
        "test_state": "success",
        "mergeable_state": "blocked",
    },
}
GREEN = {
    "key": "20993",
    "fields": {
        "head": "aa9123696d0d1f2a3b4c5d6e7f8091a2b3c4d5e6",
        "base": "dev",
        "test_state": "success",
        "mergeable_state": "clean",
    },
}


def route(shell, dry_run=False):
    argv = ["route", "--repo", "Forge-AI/monorepo", "--ledger", LEDGER]
    if dry_run:
        argv.append("--dry-run")
    assert ledger.main(argv, shell) == 0


def test_failing_row_gets_one_comment_carrying_the_first_real_error(red_routes):
    shell = FakeShell(rows=[RED], routes=red_routes)
    route(shell)

    assert len(shell.posted) == 1
    number, body = shell.posted[0]
    assert number == "21052"
    assert body.startswith(f"<!-- {ledger.MARKER} {MOVED_HEAD} -->\n")
    assert ERROR in body
    assert "The command exited with status" not in body
    assert "throw new Error" not in body
    assert "https://buildkite.com/forge/test/builds/8794" in body


def test_route_records_the_head_it_graded_and_the_next_action(red_routes):
    shell = FakeShell(rows=[RED], routes=red_routes)
    route(shell)

    fields = shell.fields("21052")
    assert fields["last_graded_head"] == MOVED_HEAD
    assert fields["next_action"].startswith("fix: ")
    assert ERROR[:40] in fields["next_action"]


def test_row_already_graded_at_this_head_is_skipped(red_routes):
    graded = {"key": "21052", "fields": dict(RED["fields"], last_graded_head=MOVED_HEAD)}
    shell = FakeShell(rows=[graded], routes=red_routes)
    route(shell)

    assert shell.posted == []
    assert not [argv for argv in shell.calls if argv[0] == "bk"]


def test_moved_head_invalidates_the_prior_verdict(red_routes):
    stale = {"key": "21052", "fields": dict(RED["fields"], last_graded_head="0" * 40)}
    shell = FakeShell(rows=[stale], routes=red_routes)
    route(shell)

    assert len(shell.posted) == 1


def test_existing_marker_comment_blocks_a_second_post(red_routes):
    shell = FakeShell(
        rows=[RED],
        routes=red_routes,
        comments={"21052": ["unrelated chatter", f"<!-- {ledger.MARKER} {MOVED_HEAD} -->\nCI is red."]},
    )
    route(shell)

    assert shell.posted == []


def test_marker_for_another_head_does_not_block_the_post(red_routes):
    shell = FakeShell(
        rows=[RED],
        routes=red_routes,
        comments={"21052": [f"<!-- {ledger.MARKER} {'0' * 40} -->\nCI was red."]},
    )
    route(shell)

    assert len(shell.posted) == 1


def test_dirty_row_gets_a_rebase_instruction_without_reading_a_log():
    shell = FakeShell(rows=[DIRTY])
    route(shell)

    number, body = shell.posted[0]
    assert number == "20961"
    assert "Rebase it onto the base branch" in body
    assert not [argv for argv in shell.calls if argv[0] == "bk"]
    assert shell.fields("20961")["next_action"] == "rebase onto dev"


def test_blocked_row_names_the_held_requirement():
    shell = FakeShell(rows=[BLOCKED])
    route(shell)

    _, body = shell.posted[0]
    assert "Mergeable state is `blocked`" in body
    assert shell.fields("20970")["next_action"] == "unblock: blocked"


def test_green_row_is_never_routed():
    shell = FakeShell(rows=[GREEN])
    route(shell)

    assert shell.posted == []


def test_dry_run_writes_nothing(capsys, red_routes):
    shell = FakeShell(rows=[RED, DIRTY], routes=red_routes)
    route(shell, dry_run=True)

    assert shell.posted == []
    assert not [argv for argv in shell.calls if argv[:4] == ["ccn", "ledger", "row", "set"]]
    assert "last_graded_head" not in shell.fields("21052")
    printed = capsys.readouterr().out
    assert ERROR in printed
    assert "#20961 would post" in printed


def test_killed_job_reports_no_error_line_rather_than_a_job_name(red_routes):
    routes = dict(red_routes, **{"log:01a0a85c-6c8f-4f3f-a875-f2bc0dc46e1d": "job-log-killed.txt"})
    shell = FakeShell(rows=[RED], routes=routes)
    route(shell)

    _, body = shell.posted[0]
    assert "has no error line" in body


def test_job_id_comes_from_the_build_when_the_url_carries_none(red_routes):
    routes = dict(red_routes, **{f"checks:{MOVED_HEAD}": "check-runs-no-ai-review.json"})
    shell = FakeShell(rows=[RED], routes=routes)
    route(shell)

    assert [argv for argv in shell.calls if argv[1:3] == ["build", "view"]]
    assert ERROR in shell.posted[0][1]


def test_route_never_calls_graphql(red_routes):
    shell = FakeShell(rows=[RED, DIRTY, BLOCKED], routes=red_routes)
    route(shell)

    assert not [argv for argv in shell.calls if "graphql" in " ".join(argv)]
