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


def route(shell, capsys=None, dry_run=False):
    argv = ["route", "--repo", "Forge-AI/monorepo", "--ledger", LEDGER]
    if dry_run:
        argv.append("--dry-run")
    assert ledger.main(argv, shell) == 0
    return capsys.readouterr().out if capsys else ""


def test_failing_row_verdict_carries_the_first_real_error(capsys, red_routes):
    shell = FakeShell(rows=[RED], routes=red_routes)
    printed = route(shell, capsys)

    assert "#21052" in printed
    assert ERROR in printed
    assert "The command exited with status" not in printed
    assert "throw new Error" not in printed
    assert "https://buildkite.com/forge/test/builds/8794" in printed


def test_route_writes_nothing_to_the_pull_request(capsys, red_routes):
    """A lane is addressed where it listens. A comment on a PR reaches whoever reads it."""
    shell = FakeShell(rows=[RED], routes=red_routes)
    route(shell, capsys)

    assert shell.posted == []
    assert not [argv for argv in shell.calls if "--method" in argv and "POST" in argv]


def test_route_records_the_head_it_graded_and_the_next_action(red_routes):
    shell = FakeShell(rows=[RED], routes=red_routes)
    route(shell)

    fields = shell.fields("21052")
    assert fields["last_graded_head"] == MOVED_HEAD
    assert fields["next_action"].startswith("fix: ")
    assert ERROR[:40] in fields["next_action"]


def test_row_already_graded_at_this_head_is_skipped(capsys, red_routes):
    graded = {"key": "21052", "fields": dict(RED["fields"], last_graded_head=MOVED_HEAD)}
    shell = FakeShell(rows=[graded], routes=red_routes)
    printed = route(shell, capsys)

    assert "#21052" not in printed
    assert not [argv for argv in shell.calls if argv[0] == "bk"]


def test_moved_head_invalidates_the_prior_verdict(capsys, red_routes):
    stale = {"key": "21052", "fields": dict(RED["fields"], last_graded_head="0" * 40)}
    shell = FakeShell(rows=[stale], routes=red_routes)
    printed = route(shell, capsys)

    assert ERROR in printed
    assert shell.fields("21052")["last_graded_head"] == MOVED_HEAD


def test_dirty_row_gets_a_rebase_instruction_without_reading_a_log(capsys):
    shell = FakeShell(rows=[DIRTY])
    printed = route(shell, capsys)

    assert "#20961" in printed
    assert "Rebase it onto the base branch" in printed
    assert not [argv for argv in shell.calls if argv[0] == "bk"]
    assert shell.fields("20961")["next_action"] == "rebase onto dev"


def test_blocked_row_names_the_held_requirement(capsys):
    shell = FakeShell(rows=[BLOCKED])
    printed = route(shell, capsys)

    assert "Mergeable state is `blocked`" in printed
    assert shell.fields("20970")["next_action"] == "unblock: blocked"


def test_green_row_is_never_routed(capsys):
    shell = FakeShell(rows=[GREEN])
    printed = route(shell, capsys)

    assert "#20993" not in printed
    assert not [argv for argv in shell.calls if argv[:4] == ["ccn", "ledger", "row", "set"]]


def test_dry_run_writes_nothing(capsys, red_routes):
    shell = FakeShell(rows=[RED, DIRTY], routes=red_routes)
    printed = route(shell, capsys, dry_run=True)

    assert not [argv for argv in shell.calls if argv[:4] == ["ccn", "ledger", "row", "set"]]
    assert "last_graded_head" not in shell.fields("21052")
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
    """The desk carries the verdict into its own message, so the extraction has to be
    reachable without running the command."""
    shell = FakeShell(rows=[RED], routes=red_routes)
    gh = ledger.Github(shell, "Forge-AI/monorepo")
    text, action = ledger.route_verdict(shell, gh, RED["fields"])

    assert ERROR in text
    assert action.startswith("fix: ")


def test_route_never_calls_graphql(capsys, red_routes):
    shell = FakeShell(rows=[RED, DIRTY, BLOCKED], routes=red_routes)
    route(shell, capsys)

    assert not [argv for argv in shell.calls if "graphql" in " ".join(argv)]
