import json
import subprocess

import pytest
from drive import github

GATE = "LTX Behavioral Gate (short)"
OPEN_ON = github.open_on
VISIBILITY = github.visibility
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
        "commits": {"nodes": [{"commit": {"committedDate": "2026-11-11T09:00:00Z", "statusCheckRollup": {"contexts": {"nodes": GREEN}}}}]},
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

    def fake(cmd: list[str], **_: object) -> str:
        calls.append(cmd)
        merged = node(state="MERGED", mergeCommit={"oid": "fff000"}, commits={"nodes": []}, reviewDecision="APPROVED", labels={"nodes": [{"name": "hold"}]})
        return json.dumps({"data": {"r0": {"p1": node(), "p2": merged}, "r1": {"p7": None}}})

    monkeypatch.setattr(github, "run", fake)
    reads, refused = github.fetch(["a/one#1", "a/one#2", "b/two#7"])
    assert refused is None
    assert calls[0][:4] == ["gh", "api", "graphql", "-f"]
    assert calls[0][4].startswith("query=query {")
    assert reads == {
        "a/one#1": {
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "abc1234def",
            "committed": "2026-11-11T09:00:00Z",
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
            "committed": None,
            "mergeCommit": "fff000",
            "checks": [],
            "reviewDecision": "APPROVED",
            "mergeable": "MERGEABLE",
            "title": "Fix the thing",
            "labels": ["hold"],
        },
    }


@pytest.mark.parametrize(("count", "queries"), [(1, [1]), (25, [25]), (167, [25, 25, 25, 25, 25, 25, 17])])
def test_fetch_reads_twenty_five_prs_a_graphql_query_and_open_on_every_branch_in_one(
    monkeypatch: pytest.MonkeyPatch, count: int, queries: list[int]
) -> None:
    sizes = []

    def fake(cmd: list[str], **_: object) -> str:
        sizes.append((cmd[4].count("pullRequest("), cmd[4].count("pullRequests(")))
        return json.dumps({"data": {}})

    monkeypatch.setattr(github, "run", fake)
    github.fetch([f"a/one#{number}" for number in range(1, count + 1)])
    OPEN_ON([("a/one", f"branch-{number}") for number in range(count)])
    assert sizes == [*((size, 0) for size in queries), (0, count)]


def test_a_refused_query_loses_only_its_own_prs_and_names_them(monkeypatch: pytest.MonkeyPatch) -> None:
    def second_refused(cmd: list[str], **_: object) -> str:
        if "p26: pullRequest" in cmd[4]:
            raise github.GithubError("gh: HTTP 502 ccx: gh failed after 4 tries")
        return json.dumps({"data": {"r0": {f"p{number}": node() for number in range(1, 61)}}})

    monkeypatch.setattr(github, "run", second_refused)
    reads, refused = github.fetch([f"a/one#{number}" for number in range(1, 61)])
    assert sorted(reads, key=lambda key: github.split(key)[2]) == [f"a/one#{number}" for number in [*range(1, 26), *range(51, 61)]]
    assert str(refused) == "could not read 25 PRs of a/one from GitHub: gh: HTTP 502 ccx: gh failed after 4 tries"


def test_a_failed_graphql_read_names_what_it_could_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def limited(cmd: list[str], **_: object) -> str:
        raise github.GithubError("gh: API rate limit already exceeded for installation ID 1.")

    monkeypatch.setattr(github, "run", limited)
    reads, refused = github.fetch(["a/one#1", "b/two#7"])
    with pytest.raises(github.GithubError) as opened:
        OPEN_ON([("a/one", "fix-it")])
    assert (reads, str(refused), str(opened.value)) == (
        {},
        "could not read 2 PRs of a/one, b/two from GitHub: gh: API rate limit already exceeded for installation ID 1.",
        "could not read the open PRs on running jobs' branches in a/one from GitHub: gh: API rate limit already exceeded for installation ID 1.",
    )


def test_run_raises_with_gh_stderr_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(cmd: list[str], **_: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(cmd, 1, "", "HTTP 502:\n  bad gateway\n")

    monkeypatch.setattr(github.subprocess, "run", fake)
    with pytest.raises(github.GithubError, match="HTTP 502: bad gateway"):
        github.run(["gh", "api", "graphql"])


def test_run_sends_a_gh_command_through_ccx_gateway_under_the_drive_caller_and_any_other_command_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    ran = []

    def fake(cmd: list[str], env: dict | None = None, **_: object) -> subprocess.CompletedProcess:
        ran.append((cmd, env and env["CCX_CALLER"]))
        return subprocess.CompletedProcess(cmd, 0, "public\n", "")

    monkeypatch.setattr(github.subprocess, "run", fake)
    assert VISIBILITY("a/one") == "public"
    github.run(["gh", "pr", "close", "https://github.com/a/one/pull/1"])
    github.run(["git", "remote", "get-url", "origin"])
    assert ran == [
        (["ccx", "vcs", "gh", "--max-age", "1h", "--", "api", "repos/a/one", "--jq", ".visibility"], github.CALLER),
        (["ccx", "vcs", "gh", "--max-age", "0s", "--", "pr", "close", "https://github.com/a/one/pull/1"], github.CALLER),
        (["git", "remote", "get-url", "origin"], None),
    ]


def test_run_raises_when_gh_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(cmd: list[str], **_: object) -> subprocess.CompletedProcess:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(github.subprocess, "run", fake)
    with pytest.raises(github.GithubError):
        github.run(["gh", "api", "graphql"])


def test_fetch_names_an_unreadable_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "run", lambda cmd, **_: "<html>bad gateway</html>")
    reads, refused = github.fetch(["a/one#1"])
    assert reads == {}
    assert str(refused).startswith("unreadable GraphQL reply for 1 PRs of a/one")


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


def job(name: str, conclusion: str | None, run: int, workflow: str = "FDE Docs") -> dict:
    return {"name": name, "conclusion": conclusion, "detailsUrl": f"https://github.com/a/one/actions/runs/{run}/job/{run}0", "workflowName": workflow}


@pytest.mark.parametrize(
    ("again", "verdict"),
    [("SUCCESS", "pass"), ("SKIPPED", "pass"), (None, "pending"), ("CANCELLED", "fail"), ("FAILURE", "fail")],
)
def test_a_cancelled_run_fails_a_head_only_when_no_other_run_of_its_workflow_passed_or_runs(again: str | None, verdict: str) -> None:
    rollup = [*GREEN, job("build", "CANCELLED", 1), job("deploy", "CANCELLED", 1), job("build", again, 2)]
    assert github.checks(rollup, []) == verdict


def test_a_duplicate_run_reads_pending_then_pass_while_its_later_jobs_are_not_yet_created() -> None:
    cancelled = [*GREEN, job("build", "CANCELLED", 1), job("deploy", "CANCELLED", 1)]
    said = [github.live(pr_read(rollup), stored()) for rollup in ([*cancelled, job("build", None, 2)], [*cancelled, job("build", "SUCCESS", 2), job("deploy", "SKIPPED", 2)])]
    assert [(fresh["checks"], fresh["failed_checks"]) for fresh in said] == [("pending", []), ("pass", [])]


@pytest.mark.parametrize(
    "rollup",
    [
        [*GREEN, job("build", "SUCCESS", 1), job("deploy", "CANCELLED", 1)],
        [*GREEN, job("build", "CANCELLED", 1), job("build", "SUCCESS", 2, workflow="Other")],
        [*GREEN, {"name": "Socket", "conclusion": "CANCELLED", "detailsUrl": "https://socket.dev/x", "workflowName": ""}, {"name": "Socket", "conclusion": "SUCCESS"}],
    ],
)
def test_a_cancelled_job_with_no_other_run_of_its_workflow_still_fails(rollup: list[dict]) -> None:
    assert github.checks(rollup, []) == "fail"


def test_fetch_names_each_check_runs_workflow_as_gh_pr_view_does(monkeypatch: pytest.MonkeyPatch) -> None:
    contexts = [
        {"name": "build", "conclusion": "CANCELLED", "detailsUrl": "https://github.com/a/one/actions/runs/1/job/10", "checkSuite": {"workflowRun": {"workflow": {"name": "FDE Docs"}}}},
        {"name": "Socket", "conclusion": "SUCCESS", "detailsUrl": "https://socket.dev/x", "checkSuite": {"workflowRun": None}},
        {"context": "buildkite/test", "state": "SUCCESS"},
    ]
    pr = node(commits={"nodes": [{"commit": {"committedDate": "2026-11-11T09:00:00Z", "statusCheckRollup": {"contexts": {"nodes": contexts}}}}]})
    monkeypatch.setattr(github, "run", lambda cmd, **_: json.dumps({"data": {"r0": {"p1": pr}}}))
    assert github.fetch(["a/one#1"])[0]["a/one#1"]["checks"] == [
        {"name": "build", "conclusion": "CANCELLED", "detailsUrl": "https://github.com/a/one/actions/runs/1/job/10", "workflowName": "FDE Docs"},
        {"name": "Socket", "conclusion": "SUCCESS", "detailsUrl": "https://socket.dev/x", "workflowName": ""},
        {"context": "buildkite/test", "state": "SUCCESS"},
    ]


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
