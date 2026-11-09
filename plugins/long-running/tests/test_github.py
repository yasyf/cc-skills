import json
import subprocess

import pytest
from drive import github


def node(**overrides: object) -> dict:
    base = {
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": "abc1234def",
        "title": "Fix the thing",
        "reviewDecision": None,
        "mergeCommit": None,
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
    }
    return {**base, **overrides}


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
        merged = node(state="MERGED", mergeCommit={"oid": "fff000"}, commits={"nodes": []}, reviewDecision="APPROVED")
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
            "checks": "SUCCESS",
            "reviewDecision": None,
            "title": "Fix the thing",
        },
        "a/one#2": {
            "state": "MERGED",
            "isDraft": False,
            "headRefOid": "abc1234def",
            "mergeCommit": "fff000",
            "checks": None,
            "reviewDecision": "APPROVED",
            "title": "Fix the thing",
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
