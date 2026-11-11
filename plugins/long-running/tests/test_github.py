import json
import subprocess

import pytest
from drive import github

GATE = "LTX Behavioral Gate (short)"
GREEN = [{"name": "Lint", "conclusion": "SUCCESS"}, {"context": "buildkite/test", "state": "SUCCESS"}]


def node(**overrides: object) -> dict:
    base = {
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": "abc1234def",
        "title": "Fix the thing",
        "reviewDecision": None,
        "mergeable": "MERGEABLE",
        "mergeCommit": None,
        "labels": {"nodes": []},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": GREEN}}}}]},
    }
    return {**base, **overrides}


def stored(head: str = "abc", failed: list[str] | None = None) -> dict:
    return {"head": head, "failed_checks": failed or []}


def pr_read(rollup: list[dict], **overrides: object) -> dict:
    return {"state": "OPEN", "checks": rollup, "reviewDecision": None, "mergeable": "MERGEABLE", "headRefOid": "abc", "title": "T", "labels": [], **overrides}


def test_query_aliases_one_repository_per_repo_and_one_pull_per_number() -> None:
    text = github.query(["b/two#7", "a/one#1", "a/one#2"])
    assert 'r0: repository(owner: "a", name: "one")' in text
    assert 'r1: repository(owner: "b", name: "two")' in text
    assert "p1: pullRequest(number: 1)" in text
    assert "p2: pullRequest(number: 2)" in text
    assert "p7: pullRequest(number: 7)" in text
    assert text.count("repository(") == 2


def test_fetch_runs_gh_graphql_and_flattens_each_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake(cmd: list[str]) -> str:
        calls.append(cmd)
        merged = node(state="MERGED", mergeCommit={"oid": "fff000"}, commits={"nodes": []}, reviewDecision="APPROVED", labels={"nodes": [{"name": "hold"}]})
        return json.dumps({"data": {"r0": {"p1": node(), "p2": merged}, "r1": {"p7": None}}})

    monkeypatch.setattr(github, "run", fake)
    reads = github.fetch(["a/one#1", "a/one#2", "b/two#7"])
    assert calls[0][:4] == ["gh", "api", "graphql", "-f"]
    assert calls[0][4].startswith("query=query {")
    assert reads == {
        "a/one#1": {
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "abc1234def",
            "mergeCommit": None,
            "checks": GREEN,
            "reviewDecision": None,
            "mergeable": "MERGEABLE",
            "title": "Fix the thing",
            "labels": [],
        },
        "a/one#2": {
            "state": "MERGED",
            "isDraft": False,
            "headRefOid": "abc1234def",
            "mergeCommit": "fff000",
            "checks": [],
            "reviewDecision": "APPROVED",
            "mergeable": "MERGEABLE",
            "title": "Fix the thing",
            "labels": ["hold"],
        },
    }


def test_fetch_batches_fifty_prs_per_query(monkeypatch: pytest.MonkeyPatch) -> None:
    sizes = []

    def fake(cmd: list[str]) -> str:
        sizes.append(cmd[4].count("pullRequest("))
        return json.dumps({"data": {}})

    monkeypatch.setattr(github, "run", fake)
    github.fetch([f"a/one#{number}" for number in range(1, 121)])
    assert sizes == [50, 50, 20]


def test_run_raises_with_gh_stderr_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(cmd: list[str], **_: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(cmd, 1, "", "HTTP 502:\n  bad gateway\n")

    monkeypatch.setattr(github.subprocess, "run", fake)
    with pytest.raises(github.GithubError, match="HTTP 502: bad gateway"):
        github.run(["gh", "api", "graphql"])


def test_run_raises_when_gh_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(cmd: list[str], **_: object) -> subprocess.CompletedProcess:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(github.subprocess, "run", fake)
    with pytest.raises(github.GithubError):
        github.run(["gh", "api", "graphql"])


def test_fetch_raises_a_github_error_on_an_unreadable_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "run", lambda cmd: "<html>bad gateway</html>")
    with pytest.raises(github.GithubError, match="unreadable GraphQL reply"):
        github.fetch(["a/one#1"])


def test_live_maps_a_read_onto_the_stored_vocabulary() -> None:
    read = pr_read([*GREEN, {"context": "buildkite/deploy", "state": "ERROR"}], reviewDecision="CHANGES_REQUESTED", mergeable="CONFLICTING")
    assert github.live(read, stored()) == {
        "state": "open",
        "checks": "fail",
        "failed_checks": ["buildkite/deploy"],
        "review": "changes",
        "head": "abc",
        "title": "T",
        "hold_label": False,
        "conflict": True,
    }


def test_live_reads_the_hold_label_the_drive_holds_its_own_pr_with() -> None:
    assert github.live(pr_read(GREEN, labels=["bug", "hold"]), stored())["hold_label"] is True
    assert github.live(pr_read(GREEN, labels=["holdover"]), stored())["hold_label"] is False


def test_live_leaves_conflict_out_while_github_has_not_worked_it_out() -> None:
    assert "conflict" not in github.live(pr_read(GREEN, mergeable="UNKNOWN"), stored())


@pytest.mark.parametrize(
    ("rollup", "failed", "verdict"),
    [
        ([], [], "none"),
        (GREEN, [], "pass"),
        ([*GREEN, {"name": "Lint", "conclusion": "NEUTRAL"}, {"name": "S3", "conclusion": "SKIPPED"}], [], "pass"),
        ([*GREEN, {"name": GATE, "conclusion": None}], [], "pending"),
        ([*GREEN, {"name": GATE, "conclusion": ""}], [GATE], "pending"),
        ([*GREEN, {"context": "buildkite/deploy", "state": "PENDING"}], [], "pending"),
        ([*GREEN, {"name": GATE, "conclusion": None}, {"name": "Docker", "conclusion": "CANCELLED"}], [], "fail"),
        ([*GREEN, {"name": GATE, "conclusion": "FAILURE"}], [GATE], "fail"),
        (GREEN, [GATE], "pending"),
        ([], [GATE], "pending"),
        ([*GREEN, {"name": GATE, "conclusion": "SUCCESS"}], [GATE], "pass"),
    ],
)
def test_checks_reads_a_queued_running_or_missing_rerun_as_pending(rollup: list[dict], failed: list[str], verdict: str) -> None:
    assert github.checks(rollup, failed) == verdict


def test_a_check_that_failed_then_dropped_out_for_its_rerun_reads_pending_until_its_new_run_passes() -> None:
    row = stored()
    said = []
    for rollup in (
        [*GREEN, {"name": GATE, "conclusion": "FAILURE"}],
        GREEN,
        [*GREEN, {"name": GATE, "conclusion": None}],
        [*GREEN, {"name": GATE, "conclusion": "SUCCESS"}],
    ):
        row |= github.live(pr_read(rollup), row)
        said.append(row["checks"])
    assert said == ["fail", "pending", "pending", "pass"]
    assert row["failed_checks"] == [GATE]


def test_a_new_head_forgets_the_checks_that_failed_on_the_old_one() -> None:
    fresh = github.live(pr_read(GREEN, headRefOid="def"), stored("abc", [GATE]))
    assert (fresh["checks"], fresh["failed_checks"]) == ("pass", [])
