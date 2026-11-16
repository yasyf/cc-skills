from pathlib import Path

import pytest
from drive import github


def page(head: str = "head", base: str = "base", start: int = 0, count: int = 101) -> dict:
    end = min(start + 100, count)
    return {
        "headRefOid": head,
        "baseRefOid": base,
        "changedFiles": count,
        "files": {
            "nodes": [{"path": f"file-{i}"} for i in range(start, end)],
            "pageInfo": {"hasNextPage": end < count, "endCursor": str(end)},
        },
    }


def test_large_file_lists_reuse_complete_snapshot_but_refresh_metadata(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    current = {"head": "head", "base": "base"}

    def read(query: str, what: str, **kwargs: object) -> dict:
        calls.append(query)
        start = 100 if 'after: "100"' in query else 0
        return {"data": {"r0": {"p1": page(**current, start=start)}}}

    monkeypatch.setattr(github, "graphql", read)
    expected = [f"file-{i}" for i in range(101)]
    assert github.paths(["o/r#1"]) == {"o/r#1": ("head", expected)}
    assert len(calls) == 2
    assert github.files("o/r#1") == expected
    assert len(calls) == 3
    current["head"] = "pushed"
    assert github.files("o/r#1") == expected
    assert len(calls) == 5
    current["base"] = "advanced"
    assert github.files("o/r#1") == expected
    assert len(calls) == 7


def test_small_file_lists_need_one_batched_read_and_no_disk_cache(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def read(query: str, what: str, **kwargs: object) -> dict:
        calls.append(query)
        return {"data": {"r0": {"p1": page(count=1), "p2": page(count=0)}}}

    monkeypatch.setattr(github, "graphql", read)
    assert github.paths(["o/r#1", "o/r#2"]) == {"o/r#1": ("head", ["file-0"]), "o/r#2": ("head", [])}
    assert len(calls) == 1
    assert not (state_root / "pr-paths.sqlite3").exists()


@pytest.mark.parametrize("changed", ["headRefOid", "baseRefOid"])
def test_changed_snapshot_during_pagination_is_not_cached(state_root: Path, monkeypatch: pytest.MonkeyPatch, changed: str) -> None:
    calls = []

    def read(query: str, what: str, **kwargs: object) -> dict:
        calls.append(query)
        node = page(start=100) if 'after: "100"' in query else page()
        if len(calls) == 2:
            node[changed] = "moved"
        return {"data": {"r0": {"p1": node}}}

    monkeypatch.setattr(github, "graphql", read)
    with pytest.raises(github.GithubError, match="changed while reading"):
        github.files("o/r#1")
    assert len(github.files("o/r#1")) == 101
    assert len(calls) == 4


def test_incomplete_file_list_is_not_cached(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def read(query: str, what: str, **kwargs: object) -> dict:
        node = page(start=100) if 'after: "100"' in query else page()
        if 'after: "100"' in query:
            node["files"]["nodes"] = []
        return {"data": {"r0": {"p1": node}}}

    monkeypatch.setattr(github, "graphql", read)
    with pytest.raises(github.GithubError, match="incomplete"):
        github.files("o/r#1")


def test_unknown_pr_remains_a_github_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "graphql", lambda *args, **kwargs: {"data": {"r0": {"p1": None}}})
    with pytest.raises(github.GithubError, match="o/r#1"):
        github.files("o/r#1")


def test_large_lists_follow_every_cursor_once(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def read(query: str, what: str, **kwargs: object) -> dict:
        calls.append(query)
        start = next((n for n in (100, 200) if f'after: "{n}"' in query), 0)
        return {"data": {"r0": {"p1": page(start=start, count=250)}}}

    monkeypatch.setattr(github, "graphql", read)
    assert github.files("o/r#1") == [f"file-{i}" for i in range(250)]
    assert len(calls) == 3
    assert github.files("o/r#1") == [f"file-{i}" for i in range(250)]
    assert len(calls) == 4
