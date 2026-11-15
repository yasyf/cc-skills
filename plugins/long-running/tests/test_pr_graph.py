from dataclasses import FrozenInstanceError

import pytest
from drive import card, pr_graph, state
from drive.pr_graph import PrGraph


def rows() -> dict[str, dict]:
    return {
        key: {"state": status, "native": native, "branch": branch, "base": base}
        for key, status, native, branch, base in [
            ("one/repo#1", "open", [], "first", "main"),
            ("one/repo#2", "open", [], "second", "first"),
            ("one/repo#3", "open", [], "fork", "first"),
            ("one/repo#4", "closed", [], "closed", "second"),
            ("two/repo#1", "open", [], "second", "first"),
            ("two/repo#2", "open", [], "first", "main"),
            ("one/repo#5", "open", ["one/repo#5", "one/repo#6"], "native", "main"),
            ("one/repo#6", "merged", ["one/repo#5", "one/repo#6"], "tip", "native"),
            ("one/repo#7", "open", [], "", ""),
            ("one/repo#8", "open", [], "cycle-a", "cycle-b"),
            ("one/repo#9", "open", [], "cycle-b", "cycle-a"),
            ("one/repo#10", "open", [], "first", "main"),
        ]
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_graph_preserves_native_and_branch_card_grouping(reverse: bool) -> None:
    prs = dict(reversed(rows().items())) if reverse else rows()
    known = {key: ("", pr) for key, pr in prs.items()}
    graph = PrGraph.read(prs)
    for key, pr in prs.items():
        assert graph.stack(key) == tuple(pr["native"] or card.chained(known, key, {}))


def test_graph_is_an_immutable_topology_snapshot() -> None:
    prs = rows()
    graph = PrGraph.read(prs)
    expected = {key: graph.stack(key) for key in prs}
    prs["one/repo#2"]["base"] = "main"
    prs["one/repo#5"]["native"].append("one/repo#99")
    assert {key: graph.stack(key) for key in prs} == expected
    assert PrGraph.read(prs).stack("one/repo#5") != expected["one/repo#5"]
    with pytest.raises(TypeError):
        graph.stacks["one/repo#1"] = ()
    with pytest.raises(FrozenInstanceError):
        graph.opened = frozenset()


def test_one_projection_graph_serves_every_row_and_approval_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {"jobs": {}, "orders": {}, "prs": rows()}
    expected = {key: PrGraph.read(data["prs"]).stack(key) for key in data["prs"]}
    chains = pr_graph.chains
    scans = 0

    def counted(keys: list[str], branches: dict[str, tuple[str, str]]) -> list[list[str]]:
        nonlocal scans
        scans += 1
        return chains(keys, branches)

    monkeypatch.setattr(pr_graph, "chains", counted)
    with state.indexed(data):
        graph = state.pr_graph(data)
        assert scans == 1
        for key, pr in data["prs"].items():
            if pr["state"] != "open":
                continue
            overlay = data | {"prs": data["prs"] | {key: pr | {"tap": "Approve"}}}
            assert state.pr_graph(overlay) is graph
            assert state.pr_graph(overlay).stack(key) == expected[key]
        assert scans == 1
        other = {"jobs": {}, "orders": {}, "prs": {}}
        with state.indexed(other):
            assert state.pr_graph(other) is not graph
        assert state.pr_graph(data) is graph
    assert state.INDEX.get() is None
    data["prs"]["one/repo#5"]["native"].append("one/repo#99")
    with state.indexed(data):
        assert state.pr_graph(data).stack("one/repo#5")[-1] == "one/repo#99"
