from __future__ import annotations

import json
import threading
import time
from datetime import timedelta

import ledger
from conftest import DIRTY_HEAD, LEDGER, LISTED_HEAD, MOVED_HEAD, FakeShell


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
    assert fields["head"] != LISTED_HEAD
    assert fields["test_state"] == "failure"


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


def test_refresh_never_rereads_a_settled_row_so_a_landing_sticks(lock, red_routes):
    landed = {"key": "20961", "fields": {"head": "stale", "state": "landed", "landed_at": "2026-10-12T21:00:00Z", "squash": "0bbac7ea1815"}}
    abandoned = {"key": "20970", "fields": {"head": "dead", "state": "closed-without-squash"}}
    shell = FakeShell(rows=[landed, abandoned], routes=red_routes)
    refresh(shell, lock, pr=["21052", "20961"])

    assert shell.fields("20961")["state"] == "landed"
    assert shell.fields("20961")["head"] == "stale"
    assert shell.fields("20970")["state"] == "closed-without-squash"
    assert shell.state_calls() == [["ccx", "vcs", "pr", "state", "--repo", "Forge-AI/monorepo", "--max-age", "2m", "21052"]]


def test_refresh_reopens_an_abandoned_row_github_shows_open_again(lock, red_routes):
    """#40373 on Oct 19: `stack-enqueue --dequeue` closed it and reopened it at a new head, and its row kept `closed-without-squash` at the old head."""
    recent = ledger.stamp(ledger.now() - timedelta(hours=1))
    rows = [
        {"key": "20961", "fields": {"head": "old", "state": "closed-without-squash", "last_refresh": "2020-01-01T00:00:00Z"}},
        {"key": "21052", "fields": {"head": "old", "state": "closed-without-squash", "last_refresh": recent}},
        {"key": "19999", "fields": {"head": "dead", "state": "closed-without-squash", "last_refresh": recent}},
    ]
    shell = FakeShell(rows=rows, routes=red_routes)
    refresh(shell, lock)

    assert shell.fields("20961")["state"] == "closed-without-squash"
    assert (shell.fields("21052")["state"], shell.fields("21052")["head"]) == ("open", MOVED_HEAD)
    assert shell.fields("19999")["state"] == "closed-without-squash"

    refresh(shell, lock, pr=["20961"])
    assert (shell.fields("20961")["state"], shell.fields("20961")["head"]) == ("open", DIRTY_HEAD)


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


def test_refresh_reads_every_row_in_one_shared_cache_call_and_no_gh(lock, red_routes):
    held = {"key": "20961", "fields": {"head": "stale"}}
    shell = FakeShell(rows=[held], routes=red_routes)
    refresh(shell, lock, pr=["21052", "20970"])

    assert shell.state_calls() == [["ccx", "vcs", "pr", "state", "--repo", "Forge-AI/monorepo", "--max-age", "2m", "20961", "20970", "21052"]]
    assert not [argv for argv in shell.calls if argv[0] == "gh"]


def test_a_full_refresh_reads_the_cache_in_batches_and_grades_every_row(lock, red_routes, monkeypatch):
    monkeypatch.setattr(ledger, "REFRESH_BATCH", 2)
    held = ["20961", "20970", "21052", "21102", "21103"]
    shell = FakeShell(rows=[{"key": pr, "fields": {"head": "stale"}} for pr in held], routes=red_routes)
    refresh(shell, lock)

    assert sorted(argv[8:] for argv in shell.state_calls()) == [["20961", "20970"], ["21052", "21102"], ["21103"]]
    assert all(shell.fields(pr)["head"] != "stale" for pr in held)
    assert (shell.fields("20961")["head"], shell.fields("21052")["head"]) == (DIRTY_HEAD, MOVED_HEAD)


def test_refresh_batches_run_at_most_refresh_workers_at_once(lock, red_routes, monkeypatch):
    class Counting(FakeShell):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.guard = threading.Lock()
            self.inflight = 0
            self.peak = 0

        def _pr_state(self, argv):
            with self.guard:
                self.inflight += 1
                self.peak = max(self.peak, self.inflight)
            time.sleep(0.05)
            try:
                return super()._pr_state(argv)
            finally:
                with self.guard:
                    self.inflight -= 1

    monkeypatch.setattr(ledger, "REFRESH_BATCH", 1)
    monkeypatch.setattr(ledger, "REFRESH_WORKERS", 2)
    held = ["20961", "20970", "21052", "21102", "21103", "21104"]
    shell = Counting(rows=[{"key": pr, "fields": {"head": "stale"}} for pr in held], routes=red_routes)
    refresh(shell, lock)

    assert len(shell.state_calls()) == len(held)
    assert shell.peak == 2


def test_refresh_polls_the_rows_while_the_lane_stacks_list(lock, red_routes, tmp_path):
    class Listing(FakeShell):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.polled = threading.Event()
            self.overlapped = False

        def _pr_state(self, argv):
            if "--lane-prefix" not in argv:
                self.polled.set()
            return super()._pr_state(argv)

        def run(self, argv, stdin=None, cwd=None):
            if argv[1:4] == ["vcs", "stack", "list"]:
                self.overlapped = self.polled.wait(5)
            return super().run(argv, stdin, cwd)

    lane = {"key": "lane/lo", "fields": {"lane": "lo", "branch_prefix": "lo/", "worktree": str(tmp_path)}}
    shell = Listing(rows=[lane, {"key": "20961", "fields": {"head": "stale"}}], routes=red_routes)
    shell.stacks[str(tmp_path)] = {"root": str(tmp_path), "branches": [{"branch": "lo/a"}]}
    refresh(shell, lock)

    assert shell.overlapped
    assert shell.fields("20961")["head"] == DIRTY_HEAD


def test_grade_reads_a_ccx_record():
    record = {
        "number": 190,
        "state": "MERGED",
        "title": "vcs: pr watch",
        "author": "yasyf",
        "createdAt": "2026-09-30T06:18:16Z",
        "baseRefName": "main",
        "headRefName": "yasyf/pr-watch",
        "headRefOid": "50f993302e17013bff91d0e53764c7c17f9387c4",
        "mergeStateStatus": "UNKNOWN",
        "changedFiles": 5,
        "rollup": {
            "state": "SUCCESS",
            "contexts": {
                "nodes": [
                    {"__typename": "CheckRun", "name": "ai-review", "conclusion": "FAILURE", "status": "COMPLETED"},
                    {"__typename": "StatusContext", "context": "buildkite/tests", "state": "SUCCESS"},
                    {"__typename": "CheckRun", "name": "ai-review", "status": "IN_PROGRESS"},
                ]
            },
        },
    }

    assert ledger.grade(record, "yasyf/cc-context") == {
        "state": "closed",
        "head": "50f993302e17013bff91d0e53764c7c17f9387c4",
        "base": "main",
        "branch": "yasyf/pr-watch",
        "author": "yasyf",
        "title": "vcs: pr watch",
        "created_at": "2026-09-30T06:18:16Z",
        "test_state": "pending",
        "mergeable_state": "unknown",
        "labels": "",
        "ai_review": "in_progress",
        "failing_checks": "[]",
        "changed_files": "5",
    }


def test_failing_checks_name_each_check_whose_newest_run_failed_with_its_url():
    contexts = [
        {"__typename": "CheckRun", "name": "lint", "conclusion": "FAILURE", "status": "COMPLETED", "databaseId": 11},
        {"__typename": "CheckRun", "name": "lint", "conclusion": "SUCCESS", "status": "COMPLETED", "databaseId": 12},
        {"__typename": "CheckRun", "name": "unit", "conclusion": "TIMED_OUT", "status": "COMPLETED", "databaseId": 13},
        {"__typename": "StatusContext", "context": "buildkite/test", "state": "FAILURE", "targetUrl": "https://buildkite.com/forge/test/builds/9"},
        {"__typename": "StatusContext", "context": "buildkite/lint", "state": "PENDING", "targetUrl": "https://buildkite.com/forge/lint/builds/9"},
    ]

    assert json.loads(ledger.failing_checks(contexts, "acme/app")) == [
        {"name": "unit", "url": "https://github.com/acme/app/runs/13"},
        {"name": "buildkite/test", "url": "https://buildkite.com/forge/test/builds/9"},
    ]


def test_a_red_buildkite_check_carries_its_first_error_line_read_once_per_url():
    url = "https://buildkite.com/forge/test/builds/9"
    checks = json.dumps([{"name": "buildkite/test", "url": url}, {"name": "unit", "url": "https://github.com/acme/app/runs/13"}])
    shell = FakeShell()
    first = json.loads(ledger.with_errors(shell, checks, None))
    assert first[0]["error"] == ledger.resolve_error(FakeShell(), ledger.BUILDKITE_URL.match(url)) != ""
    assert "error" not in first[1]
    again = FakeShell()
    assert json.loads(ledger.with_errors(again, checks, json.dumps(first))) == first
    assert [call for call in again.calls if call[0] == "bk"] == []


def test_an_empty_ledger_refreshes_without_reading_the_cache(lock):
    shell = FakeShell()
    refresh(shell, lock)

    assert shell.state_calls() == []
    assert shell.pr_keys() == []
