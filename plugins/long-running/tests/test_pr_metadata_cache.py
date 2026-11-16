import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from conftest import card_node
from drive import card, github


def test_card_reads_reuse_overlapping_reordered_batches_without_rewriting_saved_cards(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def read(cmd: list[str], **kwargs: object) -> str:
        calls.append((cmd, kwargs))
        return json.dumps({"data": {"r0": {f"p{n}": card_node({"title": f"PR {n}", "state": "OPEN", "labels": [], "files": []}) for n in re.findall(r"p(\d+):", cmd[-1])}}})

    monkeypatch.setattr(card, "run", read)
    first = card.github_reads(dict.fromkeys(["o/r#1", "o/r#2"]))
    saved = state_root / card.KEPT_READS / "o/r/2.json"
    before = saved.stat().st_mtime_ns
    assert card.github_reads({"o/r#2": None}) == {"o/r#2": first["o/r#2"]}
    assert card.github_reads(dict.fromkeys(["o/r#2", "o/r#1"])) == first
    assert len(calls) == 1
    assert calls[0][1]["max_age"] == "0s"
    assert saved.stat().st_mtime_ns == before


def test_card_with_undrawn_picture_bypasses_snapshot_cache(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def read(cmd: list[str], **kwargs: object) -> str:
        calls.append(cmd)
        return json.dumps({"data": {"r0": {"p1": card_node({"title": str(len(calls)), "state": "OPEN", "labels": [], "files": []})}}})

    monkeypatch.setattr(card, "run", read)
    assert card.github_reads({"o/r#1": None})["o/r#1"]["title"] == "1"
    assert card.github_reads({"o/r#1": {"undrawn": {"why": "picture changed"}, "state": "open"}})["o/r#1"]["title"] == "2"
    assert card.github_reads({"o/r#1": None})["o/r#1"]["title"] == "2"
    assert len(calls) == 2


def test_snapshots_expire_from_network_read_start_and_do_not_extend_on_hits(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    calls = []
    monkeypatch.setattr(github.time, "time", lambda: now[0])

    def read(keys: list[str]) -> dict:
        calls.append(keys)
        now[0] += 5
        return dict.fromkeys(keys, {"title": str(len(calls))})

    assert github.cached_nodes(["o/r#1"], "title", 60, read)["o/r#1"]["title"] == "1"
    now[0] = 159
    assert github.cached_nodes(["o/r#1"], "title", 60, read)["o/r#1"]["title"] == "1"
    now[0] = 160
    assert github.cached_nodes(["o/r#1"], "title", 60, read)["o/r#1"]["title"] == "2"
    assert len(calls) == 2


def test_fields_repositories_and_missing_prs_have_separate_snapshots(state_root: Path) -> None:
    calls = []

    def read(keys: list[str]) -> dict:
        calls.append(keys)
        return {key: {"title": key} for key in keys if not key.endswith("#9")}

    for keys, fields in [(["o/r#1", "o/r#9"], "title"), (["o/r#9"], "title"), (["o/r#1"], "body"), (["o/other#1"], "title")]:
        github.cached_nodes(keys, fields, 60, read)
    assert calls == [["o/r#1", "o/r#9"], ["o/r#9"], ["o/r#1"], ["o/other#1"]]
    assert github.cached_nodes(["O/R#1"], "title", 60, read) == {"O/R#1": {"title": "o/r#1"}}


def test_refused_read_is_not_cached(state_root: Path) -> None:
    def refused(keys: list[str]) -> dict:
        raise github.GithubError("refused")

    with pytest.raises(github.GithubError, match="refused"):
        github.cached_nodes(["o/r#1"], "title", 60, refused)
    assert github.cached_nodes(["o/r#1"], "title", 60, lambda keys: {keys[0]: {"title": "answered"}})["o/r#1"]["title"] == "answered"


def test_concurrent_overlapping_misses_share_one_network_read(state_root: Path) -> None:
    entered, release, second = Event(), Event(), Event()
    calls = []

    def read(keys: list[str]) -> dict:
        calls.append(keys)
        entered.set()
        assert release.wait(5)
        return dict.fromkeys(keys, {"title": "read"})

    def again() -> dict:
        second.set()
        return github.cached_nodes(["o/r#2"], "title", 60, read)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(github.cached_nodes, ["o/r#2", "o/r#1"], "title", 60, read)
        assert entered.wait(5)
        other = pool.submit(again)
        assert second.wait(5)
        release.set()
        assert len(first.result()) == 2
        assert other.result() == {"o/r#2": {"title": "read"}}
    assert calls == [["o/r#2", "o/r#1"]]


def test_stacks_reuse_negative_results_across_groups(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def read(query: str, what: str, **kwargs: object) -> dict:
        calls.append(query)
        return {"data": {"r0": {f"p{n}": {"stack": None} for n in re.findall(r"p(\d+):", query)}}}

    monkeypatch.setattr(github, "graphql", read)
    assert github.stacks(["o/r#1", "o/r#2"]) == {}
    assert github.stacks(["o/r#2"]) == {}
    assert len(calls) == 1
