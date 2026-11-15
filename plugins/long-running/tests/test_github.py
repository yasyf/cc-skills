import json
import os
import subprocess
from pathlib import Path

import pytest
from conftest import commit_files, git_in
from drive import github

GATE = "LTX Behavioral Gate (short)"
OPEN_ON = github.open_on
VISIBILITY = github.visibility
FETCH = github.fetch
BELOW = github.below
SAVED_BRANCHES = github.saved_branches
GREEN = [{"name": "Lint", "conclusion": "SUCCESS"}, {"context": "buildkite/test", "state": "SUCCESS"}]
RULED = {
    "rules": {
        "nodes": [
            {"type": "PULL_REQUEST", "parameters": {"requiredApprovingReviewCount": 1, "allowedMergeMethods": ["SQUASH", "REBASE"]}},
            {"type": "REQUIRED_STATUS_CHECKS", "parameters": {"requiredStatusChecks": [{"context": "buildkite/test"}, {"context": GATE}]}},
            {"type": "REQUIRED_STATUS_CHECKS", "parameters": {"requiredStatusChecks": [{"context": "buildkite/test"}]}},
            {"type": "NON_FAST_FORWARD", "parameters": None},
        ]
    },
    "name": "main",
}
UNRULED = {"name": "main", "rules": {"nodes": []}}
ALLOWED = {"squashMergeAllowed": True, "rebaseMergeAllowed": True, "mergeCommitAllowed": True}


def node(**overrides: object) -> dict:
    base = {
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": "abc1234def",
        "baseRefName": "main",
        "headRefName": "fix-the-thing",
        "title": "Fix the thing",
        "lastEditedAt": None,
        "author": {"login": "yasyf"},
        "reviewDecision": None,
        "latestOpinionatedReviews": {"nodes": []},
        "mergeable": "MERGEABLE",
        "mergeCommit": None,
        "mergedAt": None,
        "labels": {"nodes": []},
        "timelineItems": {"nodes": []},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": GREEN}}}}]},
        "stack": None,
    }
    return {**base, **overrides}


def stored(head: str = "abc", failed: list[str] | None = None) -> dict:
    return {"head": head, "failed_checks": failed or []}


def pr_read(rollup: list[dict], **overrides: object) -> dict:
    return {"state": "OPEN", "checks": rollup, "reviewDecision": None, "approvals": [], "mergeable": "MERGEABLE", "headRefOid": "abc", "baseRefName": "main", "headRefName": "lane", "title": "T", "author": "yasyf", "labels": [], "native": [], "trunk": None, "required_checks": [], "required_reviews": 0, "merge_method": "squash", "mergeCommit": None, "default": "main", "base_pr": None, **overrides}


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
    assert 'r0: repository(owner: "a", name: "one") { defaultBranchRef { name rules(first: 50)' in sent[0]


def test_rule_reviews_reads_the_approvals_a_default_branchs_pull_request_rule_requires_and_none_without_one() -> None:
    assert (github.rule_reviews({"defaultBranchRef": RULED}), github.rule_reviews({"defaultBranchRef": UNRULED})) == (1, 0)


@pytest.mark.parametrize(
    ("settings", "allowed", "method"),
    [
        ((True, True, True), None, "squash"),
        ((False, False, True), None, "rebase"),
        ((False, True, False), ["SQUASH", "REBASE"], "squash"),
        ((True, True, True), ["REBASE", "MERGE"], "rebase"),
        ((True, False, False), ["MERGE"], "merge"),
    ],
)
def test_merge_method_is_squash_else_rebase_else_merge_among_those_the_repository_and_every_pull_request_rule_allow(
    settings: tuple[bool, bool, bool], allowed: list[str] | None, method: str
) -> None:
    rules = [{"type": "PULL_REQUEST", "parameters": {"requiredApprovingReviewCount": 0, "allowedMergeMethods": allowed}}] if allowed else []
    repository = dict(zip(("mergeCommitAllowed", "squashMergeAllowed", "rebaseMergeAllowed"), settings)) | {"defaultBranchRef": {"name": "main", "rules": {"nodes": rules}}}
    assert github.merge_method(repository) == method


def test_merge_method_reads_a_pull_request_rule_naming_no_methods_as_allowing_each() -> None:
    rules = [{"type": "PULL_REQUEST", "parameters": {"requiredApprovingReviewCount": 1, "allowedMergeMethods": None}}]
    assert github.merge_method({"squashMergeAllowed": False, "rebaseMergeAllowed": True, "mergeCommitAllowed": True, "defaultBranchRef": {"name": "main", "rules": {"nodes": rules}}}) == "rebase"


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
            lastEditedAt="2026-11-12T23:08:08Z",
            stack={"baseRefName": "main", "entries": {"nodes": [{"position": 2, "pullRequest": {"number": 2, "state": "MERGED"}}, {"position": 1, "pullRequest": {"number": 1, "state": "OPEN"}}]}},
        )
        return json.dumps({"data": {"r0": {**ALLOWED, "defaultBranchRef": RULED, "p1": node(), "p2": merged}, "r1": {**ALLOWED, "defaultBranchRef": UNRULED, "p7": None}}})

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
            "mergedAt": None,
            "checks": GREEN,
            "reviewDecision": None,
            "approvals": [],
            "mergeable": "MERGEABLE",
            "title": "Fix the thing",
            "lastEditedAt": None,
            "author": "yasyf",
            "labels": [],
            "ejected_at": None,
            "key": "a/one#1",
            "native": [],
            "trunk": None,
            "required_checks": [GATE, "buildkite/test"],
            "required_reviews": 1,
            "merge_method": "squash",
            "default": "main",
            "base_pr": None,
        },
        "a/one#2": {
            "state": "MERGED",
            "isDraft": False,
            "headRefOid": "abc1234def",
            "baseRefName": "main",
            "headRefName": "fix-the-thing",
            "mergeCommit": "fff000",
            "mergedAt": None,
            "checks": [],
            "reviewDecision": "APPROVED",
            "approvals": ["abc1234def", None],
            "mergeable": "MERGEABLE",
            "title": "Fix the thing",
            "lastEditedAt": "2026-11-12T23:08:08Z",
            "author": "ghost",
            "labels": ["hold"],
            "ejected_at": None,
            "key": "a/one#2",
            "native": [("a/one#1", "open"), ("a/one#2", "merged")],
            "trunk": "main",
            "required_checks": [GATE, "buildkite/test"],
            "required_reviews": 1,
            "merge_method": "squash",
            "default": "main",
            "base_pr": None,
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
        return json.dumps({"data": {"r0": {**ALLOWED, "defaultBranchRef": RULED} | {f"p{number}": node() for number in range(1, 61)}}})

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


def test_saved_branches_names_each_branch_github_holds_under_the_lanes_own_prefix_from_one_matching_refs_read(monkeypatch: pytest.MonkeyPatch) -> None:
    refs = [{"ref": "refs/heads/saved/durable-agents-j2363/sandsql-iris-none-reads", "object": {"sha": "241f7a761105034c55d68ad9ab5903ddf3492bf3", "type": "commit"}}]
    ran = []
    monkeypatch.setattr(github, "run", lambda cmd, **_: ran.append(cmd) or json.dumps(refs))
    assert SAVED_BRANCHES("Forge-AI/monorepo", "durable-agents-j2363") == ["saved/durable-agents-j2363/sandsql-iris-none-reads at 241f7a7611"]
    assert ran == [["gh", "api", "repos/Forge-AI/monorepo/git/matching-refs/heads/saved/durable-agents-j2363/"]]
    monkeypatch.setattr(github, "run", lambda cmd, **_: "[]")
    assert SAVED_BRANCHES("Forge-AI/monorepo", "durable-agents-j2363-2") == []


def test_a_read_outliving_its_timeout_names_its_command_never_its_query_with_what_it_wrote_to_stderr_by_then(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ccx = tmp_path / "ccx"
    ccx.write_text("#!/bin/sh\necho 'ccx: minting the app token' >&2\nexec sleep 5\n")
    ccx.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    query = 'query { repository(owner: "Forge-AI", name: "monorepo") { pullRequest(number: 42) { baseRefName } } }'
    with pytest.raises(github.GithubError) as raised:
        github.run(["gh", "api", "graphql", "-f", f"query={query}"], timeout=1)
    assert str(raised.value) == "gh api graphql timed out after 1 s: ccx: minting the app token"


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
    read = pr_read([*GREEN, {"context": "buildkite/deploy", "state": "ERROR"}], reviewDecision="CHANGES_REQUESTED", mergeable="CONFLICTING", native=native, trunk="main")
    assert github.live(read, stored()) == {
        "state": "open",
        "into": None,
        "merged_sha": None,
        "default": "main",
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
        "trunk": "main",
        "reviews_required": 0,
        "merge_method": "squash",
        "conflict": True,
        "required": {"checks": "none", "failed": {}},
    }


FOLDED_INTO = {"state": "folded", "into": "a/one#9", "merged_sha": None}
FOLDED_READ = pr_read([], state="MERGED", mergeCommit="fff000", baseRefName="lane-9")


def parent(number: int = 9, **overrides: object) -> dict:
    return pr_read([], **overrides) | {"key": f"a/one#{number}"}


@pytest.mark.parametrize(
    ("read", "landed"),
    [
        (pr_read([]), {"state": "open", "into": None, "merged_sha": None}),
        (pr_read([], state="MERGED", mergeCommit="fff000"), {"state": "merged", "into": None, "merged_sha": "fff000"}),
        (FOLDED_READ, {"state": "merged", "into": None, "merged_sha": "fff000"}),
        (FOLDED_READ | {"base_pr": parent()}, FOLDED_INTO),
        (FOLDED_READ | {"base_pr": parent(state="MERGED", mergeCommit="999aaa")}, {"state": "merged", "into": "a/one#9", "merged_sha": "999aaa"}),
        (FOLDED_READ | {"base_pr": parent(state="CLOSED")}, {"state": "closed", "into": "a/one#9", "merged_sha": None}),
        (FOLDED_READ | {"base_pr": parent(state="MERGED", baseRefName="lane-4", base_pr=parent(4))}, {"state": "folded", "into": "a/one#4", "merged_sha": None}),
        (FOLDED_READ | {"base_pr": parent(state="MERGED", baseRefName="lane-4", base_pr=parent(4, state="MERGED", mergeCommit="444ddd"))}, {"state": "merged", "into": "a/one#4", "merged_sha": "444ddd"}),
    ],
)
def test_landing_reads_a_pr_merged_into_another_prs_branch_folded_until_that_pr_lands_with_its_merge_commit_or_closes_in_whatever_state_it_is_read(read: dict, landed: dict) -> None:
    assert github.landing(read) == landed


def owner(number: int, state: str = "OPEN", closed: str | None = None, created: str = "2026-11-14T03:46:41Z", cross: bool = False, base: str = "main", merge: str | None = None) -> dict:
    merged = closed if state == "MERGED" else None
    return {"number": number, "state": state, "isCrossRepository": cross, "createdAt": created, "closedAt": closed, "mergedAt": merged, "baseRefName": base, "mergeCommit": {"oid": merge} if merge else None}


MERGED_AT = "2026-11-14T21:15:47Z"


def replying(monkeypatch: pytest.MonkeyPatch, *replies: dict | github.GithubError) -> list[str]:
    sent, queue = [], list(replies)

    def reply(cmd: list[str], **_: object) -> str:
        sent.append(cmd[4])
        if isinstance(answer := queue.pop(0), github.GithubError):
            raise answer
        return json.dumps(answer)

    monkeypatch.setattr(github, "run", reply)
    return sent


def test_fetch_follows_a_pr_merged_off_its_default_branch_to_the_same_repo_pr_open_on_its_base_as_it_merged_and_reads_a_native_stacks_pr_merged_where_that_branch_holds_it(monkeypatch: pytest.MonkeyPatch) -> None:
    fold = node(state="MERGED", baseRefName="lane-9", mergeCommit={"oid": "fff000"}, mergedAt=MERGED_AT)
    stacked = node(state="MERGED", baseRefName="lane-8", mergeCommit={"oid": "eee000"}, mergedAt=MERGED_AT)
    owners = [
        owner(12, created="2026-11-14T22:00:00Z"),
        owner(11, cross=True, created="2026-11-14T01:00:00Z"),
        owner(9, "MERGED", "2026-11-14T21:45:45Z", merge="d79dfa"),
        owner(5, "CLOSED", "2026-11-13T01:02:48Z", created="2026-11-13T00:00:00Z"),
    ]
    sent = replying(
        monkeypatch,
        {"data": {"r0": {**ALLOWED, "defaultBranchRef": UNRULED, "p1": fold, "p2": stacked, "p3": node()}}},
        {"data": {"r0": {"defaultBranchRef": {"c0": {"status": "DIVERGED"}, "c1": {"status": "BEHIND"}}, "o0": {"nodes": owners}, "o1": {"nodes": [owner(8, "MERGED", MERGED_AT, merge="888aaa")]}}}},
    )
    reads, refused = FETCH(["a/one#1", "a/one#2", "a/one#3"])
    assert (refused, len(sent)) == (None, 2)
    assert 'c0: compare(headRef: "fff000") { status }' in sent[1] and 'o1: pullRequests(headRefName: "lane-8", first: 100, orderBy: {field: CREATED_AT, direction: DESC})' in sent[1]
    assert sent[1].count("compare(") == 2
    assert [github.landing(reads[key]) for key in ("a/one#1", "a/one#2", "a/one#3")] == [
        {"state": "merged", "into": "a/one#9", "merged_sha": "d79dfa"},
        {"state": "merged", "into": None, "merged_sha": "eee000"},
        {"state": "open", "into": None, "merged_sha": None},
    ]


def test_fetch_follows_a_fold_whose_parent_folded_in_turn_and_the_fold_line_names_the_pr_carrying_it(monkeypatch: pytest.MonkeyPatch) -> None:
    fold = node(state="MERGED", baseRefName="lane-9", mergeCommit={"oid": "fff000"}, mergedAt=MERGED_AT)
    sent = replying(
        monkeypatch,
        {"data": {"r0": {**ALLOWED, "defaultBranchRef": UNRULED, "p1": fold}}},
        {"data": {"r0": {"defaultBranchRef": {"c0": {"status": "DIVERGED"}}, "o0": {"nodes": [owner(9, "MERGED", "2026-11-14T21:20:00Z", base="lane-4", merge="999aaa")]}}}},
        {"data": {"r0": {"defaultBranchRef": {"c0": {"status": "DIVERGED"}}, "o0": {"nodes": [owner(4)]}}}},
    )
    reads, refused = FETCH(["a/one#1"])
    assert (refused, len(sent)) == (None, 3)
    assert 'c0: compare(headRef: "999aaa")' in sent[2] and 'pullRequests(headRefName: "lane-4"' in sent[2]
    assert github.fold_line(github.live(reads["a/one#1"], stored())) == "merged into lane-9 (#4), not main"


@pytest.mark.parametrize(("answered", "landed"), [(True, FOLDED_INTO), (False, None)])
def test_fetch_reads_the_pr_the_drive_holds_a_pr_folded_into_beside_it_in_one_call_and_leaves_the_pr_out_while_github_answers_none_for_it(
    monkeypatch: pytest.MonkeyPatch, answered: bool, landed: dict | None
) -> None:
    fold = node(state="MERGED", baseRefName="lane-9", mergeCommit={"oid": "fff000"}, mergedAt=MERGED_AT)
    sent = replying(monkeypatch, {"data": {"r0": {**ALLOWED, "defaultBranchRef": UNRULED, "p1": fold} | ({"p9": node(headRefName="lane-9")} if answered else {})}})
    reads, refused = FETCH(["a/one#1"], {"a/one#1": "a/one#9"})
    assert (refused, len(sent), "p9: pullRequest(number: 9)" in sent[0], list(reads)) == (None, 1, True, ["a/one#1"] if answered else [])
    assert (github.landing(reads["a/one#1"]) if answered else None) == landed


def test_a_refused_follow_up_leaves_out_only_the_prs_merged_off_their_default_branch_and_names_them(monkeypatch: pytest.MonkeyPatch) -> None:
    fold = node(state="MERGED", baseRefName="lane-9", mergeCommit={"oid": "fff000"}, mergedAt=MERGED_AT)
    replying(monkeypatch, {"data": {"r0": {**ALLOWED, "defaultBranchRef": UNRULED, "p1": fold, "p2": node()}}}, github.GithubError("gh: HTTP 502"))
    reads, refused = FETCH(["a/one#1", "a/one#2"])
    assert (list(reads), str(refused)) == (["a/one#2"], "could not read the PRs whose heads are the bases of a/one#1 from GitHub: gh: HTTP 502")


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


@pytest.mark.parametrize(
    ("mergeable", "head", "was", "conflict"),
    [("CONFLICTING", "abc", True, True), ("MERGEABLE", "abc", True, False), ("UNKNOWN", "abc", True, True), ("UNKNOWN", "abc", False, False), ("UNKNOWN", "def", True, False), ("CONFLICTING", "def", False, True)],
)
def test_live_keeps_a_conflict_through_unknown_at_its_head_and_reads_none_at_a_new_head_until_github_answers_conflicting(mergeable: str, head: str, was: bool, conflict: bool) -> None:
    assert github.live(pr_read(GREEN, mergeable=mergeable, headRefOid=head), stored() | {"conflict": was})["conflict"] is conflict


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
    monkeypatch.setattr(github, "run", lambda cmd, **_: json.dumps({"data": {"r0": {**ALLOWED, "defaultBranchRef": UNRULED, "p1": pr}}}))
    assert FETCH(["a/one#1"])[0]["a/one#1"]["checks"] == [
        {"name": "build", "conclusion": "CANCELLED", "detailsUrl": "https://github.com/a/one/actions/runs/1/job/10", "workflowName": "FDE Docs"},
        {"name": "Socket", "conclusion": "SUCCESS", "detailsUrl": "https://socket.dev/x", "workflowName": ""},
        {"context": "buildkite/test", "state": "SUCCESS"},
    ]


def test_trunks_own_failing_merge_queue_check_is_no_failed_test_of_the_head_it_ejected() -> None:
    trunk = {"name": "Trunk Merge Queue (dev)", "conclusion": "FAILURE", "detailsUrl": "https://app.trunk.io/forge/merge/monorepo/dev", "workflowName": ""}
    fresh = github.live(pr_read([{"context": "buildkite/test", "state": "SUCCESS"}, trunk], required_checks=["buildkite/test"]), stored())
    assert (fresh["checks"], fresh["failed_checks"], fresh["required"]) == ("pass", [], {"checks": "pass", "failed": {}})


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


def test_ejected_at_is_when_trunk_last_put_on_the_trunk_failed_label_a_pr_still_carries() -> None:
    added = {"nodes": [{"createdAt": at, "label": {"name": name}} for at, name in (("2026-11-11T09:00:00Z", github.EJECTED_LABEL), ("2026-11-12T01:35:00Z", "trunk-pending-failure"), ("2026-11-12T01:36:43Z", github.EJECTED_LABEL))]}
    assert github.flatten(node(labels={"nodes": [{"name": github.EJECTED_LABEL}]}, timelineItems=added))["ejected_at"] == "2026-11-12T01:36:43Z"
    assert github.flatten(node(timelineItems=added))["ejected_at"] is None


def test_fetched_reads_origins_head_while_another_fetch_holds_the_clones_remote_tracking_ref_and_moves_no_ref(tmp_path: Path) -> None:
    origin, clone = tmp_path / "origin", tmp_path / "clone"
    origin.mkdir()
    git_in(origin, "init", "-q", "-b", "main")
    cloned = commit_files(origin, "first", {"a": "a\n"})
    git_in(tmp_path, "clone", "-q", str(origin), str(clone))
    head = commit_files(origin, "second", {"b": "b\n"})
    (clone / ".git" / "refs" / "remotes" / "origin" / "main.lock").write_text(f"{head}\n")
    assert github.fetched(str(clone), "main") == head
    assert git_in(clone, "cat-file", "-t", head) == "commit"
    assert git_in(clone, "rev-parse", "refs/remotes/origin/main") == cloned


def test_carrier_counts_a_build_at_or_after_a_merge_on_the_line_or_a_hotfix_cherry_picking_it_and_fetches_a_build_the_clone_lacks(tmp_path: Path) -> None:
    origin, clone = tmp_path / "origin", tmp_path / "clone"
    origin.mkdir()
    git_in(origin, "init", "-q", "-b", "main")
    old = commit_files(origin, "Fix: the lander (#1)", {"a": "a\n"})
    merge = commit_files(origin, "Fix: the watch (#2)", {"b": "b\n"})
    newer = commit_files(origin, "Fix: the card (#3)", {"c": "c\n"})
    git_in(origin, "checkout", "-q", "-b", "picked", old)
    picked = commit_files(origin, "Fix: the watch (#2)", {"b": "b\n"})
    git_in(origin, "checkout", "-q", "-b", "unpicked", old)
    unpicked = commit_files(origin, "Fix: the page (#4)", {"d": "d\n"})
    git_in(origin, "checkout", "-q", "main")
    git_in(tmp_path, "clone", "-q", "--single-branch", "-b", "main", str(origin), str(clone))
    carries = github.carrier(str(clone))
    assert [carries(build, merge, 2) for build in (merge, newer, old, picked, unpicked)] == [True, True, False, True, False]
    assert git_in(clone, "cat-file", "-t", unpicked) == "commit"


@pytest.mark.parametrize(("page", "landed"), [(github.OWNERS_PER_BASE - 1, {"state": "merged", "into": None, "merged_sha": "fff000"}), (github.OWNERS_PER_BASE, None)])
def test_fetch_reads_a_fold_whose_base_no_pr_was_open_on_merged_at_its_own_commit_and_refuses_it_while_githubs_first_page_of_prs_on_that_base_is_full(
    monkeypatch: pytest.MonkeyPatch, page: int, landed: dict | None
) -> None:
    fold = node(state="MERGED", baseRefName="lane-9", mergeCommit={"oid": "fff000"}, mergedAt=MERGED_AT)
    later = [owner(100 + number, created="2026-11-14T22:00:00Z") for number in range(page)]
    replying(monkeypatch, {"data": {"r0": {**ALLOWED, "defaultBranchRef": UNRULED, "p1": fold}}}, {"data": {"r0": {"defaultBranchRef": {"c0": {"status": "DIVERGED"}}, "o0": {"nodes": later}}}})
    reads, refused = FETCH(["a/one#1"])
    assert (github.landing(reads["a/one#1"]) if "a/one#1" in reads else None, refused is None) == (landed, landed is not None)


def test_open_at_reads_a_pr_closed_at_the_moment_a_pr_merged_into_its_branch_as_no_longer_open() -> None:
    assert (github.open_at(owner(9, "MERGED", MERGED_AT), MERGED_AT), github.open_at(owner(9, "MERGED", "2026-11-14T21:15:48Z"), MERGED_AT)) == (False, True)
