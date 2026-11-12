import json
import subprocess

import pytest
from drive import github

GATE = "LTX Behavioral Gate (short)"
OPEN_ON = github.open_on
VISIBILITY = github.visibility
FETCH = github.fetch
BELOW = github.below
GREEN = [{"name": "Lint", "conclusion": "SUCCESS"}, {"context": "buildkite/test", "state": "SUCCESS"}]
RULED = {
    "rules": {
        "nodes": [
            {"type": "PULL_REQUEST", "parameters": {}},
            {"type": "REQUIRED_STATUS_CHECKS", "parameters": {"requiredStatusChecks": [{"context": "buildkite/test"}, {"context": GATE}]}},
            {"type": "REQUIRED_STATUS_CHECKS", "parameters": {"requiredStatusChecks": [{"context": "buildkite/test"}]}},
            {"type": "NON_FAST_FORWARD", "parameters": None},
        ]
    }
}
UNRULED = {"rules": {"nodes": []}}


def node(**overrides: object) -> dict:
    base = {
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": "abc1234def",
        "baseRefName": "main",
        "headRefName": "fix-the-thing",
        "title": "Fix the thing",
        "author": {"login": "yasyf"},
        "reviewDecision": None,
        "latestOpinionatedReviews": {"nodes": []},
        "mergeable": "MERGEABLE",
        "mergeCommit": None,
        "labels": {"nodes": []},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": GREEN}}}}]},
        "stack": None,
    }
    return {**base, **overrides}


def stored(head: str = "abc", failed: list[str] | None = None) -> dict:
    return {"head": head, "failed_checks": failed or []}


def pr_read(rollup: list[dict], **overrides: object) -> dict:
    return {"state": "OPEN", "checks": rollup, "reviewDecision": None, "approvals": [], "mergeable": "MERGEABLE", "headRefOid": "abc", "baseRefName": "main", "headRefName": "lane", "title": "T", "author": "yasyf", "labels": [], "native": [], "required_checks": [], **overrides}


def test_query_aliases_one_repository_per_repo_and_one_pull_per_number() -> None:
    text = github.query(["b/two#7", "a/one#1", "a/one#2"])
    assert 'r0: repository(owner: "a", name: "one")' in text
    assert 'r1: repository(owner: "b", name: "two")' in text
    assert "p1: pullRequest(number: 1)" in text
    assert "p2: pullRequest(number: 2)" in text
    assert "p7: pullRequest(number: 7)" in text
    assert text.count("repository(") == 2


def test_query_asks_each_pull_which_of_its_checks_its_base_branch_requires_and_a_heads_read_asks_none() -> None:
    text = github.query(["a/one#1", "a/one#2"])
    assert (text.count("isRequired(pullRequestNumber: 1)"), text.count("isRequired(pullRequestNumber: 2)")) == (2, 2)
    assert github.NUMBER not in text
    assert "isRequired" not in github.heads_query([("a/one", "lane")], ["a/one"])


def test_fetch_asks_each_repositorys_default_branch_rules_once_in_the_same_query_as_its_prs(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = []
    monkeypatch.setattr(github, "run", lambda cmd, **_: sent.append(cmd[4]) or json.dumps({"data": {}}))
    FETCH(["b/two#7", "a/one#1", "a/one#2"])
    assert (len(sent), sent[0].count(github.RULES), sent[0].count("defaultBranchRef")) == (1, 2, 2)
    assert 'r0: repository(owner: "a", name: "one") { defaultBranchRef { rules(first: 50)' in sent[0]


def test_fetch_runs_gh_graphql_and_flattens_each_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake(cmd: list[str], **_: object) -> str:
        calls.append(cmd)
        reviews = [{"state": "APPROVED", "commit": {"oid": "abc1234def"}}, {"state": "CHANGES_REQUESTED", "commit": {"oid": "0ld"}}, {"state": "APPROVED", "commit": None}]
        merged = node(
            state="MERGED",
            mergeCommit={"oid": "fff000"},
            commits={"nodes": []},
            reviewDecision="APPROVED",
            latestOpinionatedReviews={"nodes": reviews},
            labels={"nodes": [{"name": "hold"}]},
            author=None,
            stack={"entries": {"nodes": [{"position": 2, "pullRequest": {"number": 2, "state": "MERGED"}}, {"position": 1, "pullRequest": {"number": 1, "state": "OPEN"}}]}},
        )
        return json.dumps({"data": {"r0": {"defaultBranchRef": RULED, "p1": node(), "p2": merged}, "r1": {"defaultBranchRef": UNRULED, "p7": None}}})

    monkeypatch.setattr(github, "run", fake)
    reads, refused = FETCH(["a/one#1", "a/one#2", "b/two#7"])
    assert refused is None
    assert calls[0][:4] == ["gh", "api", "graphql", "-f"]
    assert calls[0][4].startswith("query=query {")
    assert reads == {
        "a/one#1": {
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "abc1234def",
            "baseRefName": "main",
            "headRefName": "fix-the-thing",
            "mergeCommit": None,
            "checks": GREEN,
            "reviewDecision": None,
            "approvals": [],
            "mergeable": "MERGEABLE",
            "title": "Fix the thing",
            "author": "yasyf",
            "labels": [],
            "native": [],
            "required_checks": [GATE, "buildkite/test"],
        },
        "a/one#2": {
            "state": "MERGED",
            "isDraft": False,
            "headRefOid": "abc1234def",
            "baseRefName": "main",
            "headRefName": "fix-the-thing",
            "mergeCommit": "fff000",
            "checks": [],
            "reviewDecision": "APPROVED",
            "approvals": ["abc1234def", None],
            "mergeable": "MERGEABLE",
            "title": "Fix the thing",
            "author": "ghost",
            "labels": ["hold"],
            "native": [("a/one#1", "open"), ("a/one#2", "merged")],
            "required_checks": [GATE, "buildkite/test"],
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
    FETCH([f"a/one#{number}" for number in range(1, count + 1)])
    OPEN_ON([("a/one", f"branch-{number}") for number in range(count)])
    assert sizes == [*((size, 0) for size in queries), (0, count)]


@pytest.mark.parametrize(
    ("decision", "approvals", "review"),
    [("APPROVED", ["abc", "abc"], "approved"), ("APPROVED", ["abc", "0ld"], "stale"), ("APPROVED", [None], "stale"), ("CHANGES_REQUESTED", ["0ld"], "changes"), (None, ["0ld"], "none")],
)
def test_an_approval_counts_only_on_the_prs_head_and_one_github_still_counts_from_an_older_head_reads_stale(
    decision: str | None, approvals: list[str | None], review: str
) -> None:
    assert github.live(pr_read(GREEN, reviewDecision=decision, approvals=approvals), stored())["review"] == review


def test_a_refused_query_loses_only_its_own_prs_and_names_them(monkeypatch: pytest.MonkeyPatch) -> None:
    def second_refused(cmd: list[str], **_: object) -> str:
        if "p26: pullRequest" in cmd[4]:
            raise github.GithubError("gh: HTTP 502 ccx: gh failed after 4 tries")
        return json.dumps({"data": {"r0": {"defaultBranchRef": RULED} | {f"p{number}": node() for number in range(1, 61)}}})

    monkeypatch.setattr(github, "run", second_refused)
    reads, refused = FETCH([f"a/one#{number}" for number in range(1, 61)])
    assert sorted(reads, key=lambda key: github.split(key)[2]) == [f"a/one#{number}" for number in [*range(1, 26), *range(51, 61)]]
    assert str(refused) == "could not read 25 PRs of a/one from GitHub: gh: HTTP 502 ccx: gh failed after 4 tries"


def test_a_failed_graphql_read_names_what_it_could_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def limited(cmd: list[str], **_: object) -> str:
        raise github.GithubError("gh: API rate limit already exceeded for installation ID 1.")

    monkeypatch.setattr(github, "run", limited)
    reads, refused = FETCH(["a/one#1", "b/two#7"])
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
    reads, refused = FETCH(["a/one#1"])
    assert reads == {}
    assert str(refused).startswith("unreadable GraphQL reply for 1 PRs of a/one")


def test_live_maps_a_read_onto_the_stored_vocabulary() -> None:
    native = [("a/one#1", "merged"), ("a/one#2", "closed"), ("a/one#3", "open")]
    read = pr_read([*GREEN, {"context": "buildkite/deploy", "state": "ERROR"}], reviewDecision="CHANGES_REQUESTED", mergeable="CONFLICTING", native=native)
    assert github.live(read, stored()) == {
        "state": "open",
        "checks": "fail",
        "failed_checks": ["buildkite/deploy"],
        "review": "changes",
        "head": "abc",
        "base": "main",
        "branch": "lane",
        "title": "T",
        "author": "yasyf",
        "hold_label": False,
        "native": ["a/one#1", "a/one#3"],
        "conflict": True,
        "required": {"checks": "none", "failed": {}},
    }


BUILD = "https://buildkite.com/forge/monorepo/builds/7"
TEST = {"context": "buildkite/test", "targetUrl": BUILD}
LINT = {"name": "Lint", "conclusion": "FAILURE", "isRequired": False, "detailsUrl": "https://github.com/a/one/actions/runs/1/job/10", "workflowName": "CI"}
UNIT = {"name": "test", "detailsUrl": "https://github.com/yasyf/cc-skills/actions/runs/2/job/20", "workflowName": "long-running CI"}


@pytest.mark.parametrize(
    ("rollup", "names", "verdict"),
    [
        ([LINT, {**TEST, "state": "PENDING"}], ["buildkite/test"], {"checks": "pending", "failed": {}}),
        ([LINT, {**TEST, "state": "SUCCESS"}], ["buildkite/test"], {"checks": "pass", "failed": {}}),
        ([LINT, {**TEST, "state": "FAILURE"}], ["buildkite/test"], {"checks": "fail", "failed": {"buildkite/test": BUILD}}),
        ([LINT], ["buildkite/test"], {"checks": "pending", "failed": {}}),
        ([], ["buildkite/test"], {"checks": "pending", "failed": {}}),
        ([LINT, {**TEST, "state": "PENDING", "isRequired": True}], [], {"checks": "pending", "failed": {}}),
        ([LINT, {**TEST, "state": "FAILURE", "isRequired": True}], [], {"checks": "fail", "failed": {"buildkite/test": BUILD}}),
        ([{**UNIT, "conclusion": "FAILURE"}], [], {"checks": "none", "failed": {}}),
        ([{**UNIT, "conclusion": None}], [], {"checks": "none", "failed": {}}),
        ([], [], {"checks": "none", "failed": {}}),
    ],
)
def test_the_required_verdict_reads_the_checks_the_default_branchs_rules_name_a_missing_one_pending_and_none_when_they_name_none(
    rollup: list[dict], names: list[str], verdict: dict
) -> None:
    assert github.required(pr_read(rollup, required_checks=names)) == verdict


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
    pr = node(commits={"nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": contexts}}}}]})
    monkeypatch.setattr(github, "run", lambda cmd, **_: json.dumps({"data": {"r0": {"defaultBranchRef": UNRULED, "p1": pr}}}))
    assert FETCH(["a/one#1"])[0]["a/one#1"]["checks"] == [
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


def test_below_follows_a_native_stack_split_off_another_open_prs_branch_down_to_the_default_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    def pull(base: str, opened: list[int], members: list[int]) -> dict:
        stack = {"entries": {"nodes": [{"position": at, "pullRequest": {"number": number, "state": "OPEN"}} for at, number in enumerate(members)]}}
        return {"baseRefName": base, "baseRef": {"associatedPullRequests": {"nodes": [{"number": number} for number in opened]}}, "stack": stack}

    reads = {
        54872: pull("chat-pins", [54866], [54866, 54872]),
        54866: pull("chat-organize", [54404], [54866, 54872]),
        54404: pull("chat-folders-2", [54378], [54355, 54378, 54404]),
        54355: pull("main", [], [54355, 54378, 54404]),
    }
    asked: list[int] = []

    def graphql(text: str, what: str) -> dict:
        asked.append(number := int(text.split("pullRequest(number: ")[1].split(")")[0]))
        return {"data": {"repository": {"pullRequest": reads[number]}}}

    monkeypatch.setattr(github, "graphql", graphql)
    monkeypatch.setattr(github, "below", BELOW)
    assert github.below("Forge-AI/monorepo#54872") == [f"Forge-AI/monorepo#{number}" for number in (54355, 54378, 54404, 54866)]
    assert asked == [54872, 54866, 54404, 54355]
