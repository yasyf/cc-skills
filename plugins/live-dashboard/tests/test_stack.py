from __future__ import annotations

from conftest import LEDGER, PR_STATUS, fake, fixture
from livedash import Col, Table
from livedash.components import stack

TREE = {101: "main", 102: 101, 104: 102, 103: "graphite-base/103", 105: 101}


def test_parents_follow_branches_and_overrides():
    prs = {101: {"base": "main", "branch": "a"}, 102: {"base": "a", "branch": "b"}, 103: {"base": "graphite-base/103", "branch": "c"}}
    assert stack.parents_of(prs, {}) == {101: "main", 102: 101, 103: "graphite-base/103"}
    assert stack.parents_of(prs, {"103": "102"}) == {101: "main", 102: 101, 103: 102}
    assert stack.parents_of(prs, {"103": "dev"}) == {101: "main", 102: 101, 103: "dev"}


def test_ordered_walks_depth_first_from_each_trunk():
    assert stack.ordered(TREE) == [103, 101, 102, 104, 105]


def test_the_bottom_prefix_stops_at_the_first_unlandable_pr():
    landable = dict.fromkeys(TREE, "landable")
    assert stack.bottom_prefix(TREE, landable) == [103, 101, 102, 104, 105]
    assert stack.bottom_prefix(TREE, landable | {102: "blocked:ci-red"}) == [103, 101, 105]
    assert stack.bottom_prefix(TREE, landable | {101: "blocked:review"}) == [103]


def test_the_graph_tones_nodes_and_highlights_what_lands(tmp_path):
    ctx = fake(tmp_path, replies={LEDGER: fixture("ledger-rows.json"), PR_STATUS: fixture("pr-status.json")})
    graph = stack.stack_graph(ctx, ledger="L1", repo="o/r")
    assert [(node.id, node.tone) for node in graph.nodes] == [("graphite-base/103", "warn"), ("main", "muted"), ("103", "bad"), ("101", "warn"), ("102", "warn")]
    assert sorted(map(tuple, graph.edges)) == [("101", "102"), ("graphite-base/103", "103"), ("main", "101")]
    assert graph.highlight == ["101"]
    assert graph.nodes[3].label == "#101 lane-one" and graph.nodes[3].link == "https://github.com/o/r/pull/101"


def test_the_graph_drops_landed_prs_takes_the_status_base_and_names_the_root_session(tmp_path):
    session = "900424b6-7393-480c-a26a-f1bd21da6e57"
    rows = [{"key": "201", "fields": {"state": "open", "lane": session}}, {"key": "202", "fields": {"state": "open", "lane": "walker"}}]
    statuses = [{"number": 201, "state": "OPEN", "base": "dev", "verdict": "landable"}, {"number": 202, "state": "MERGED", "base": "dev", "verdict": "landed"}]
    graph = stack.stack_graph(fake(tmp_path, replies={LEDGER: rows, PR_STATUS: statuses}), ledger="L1", repo="o/r")
    assert [(node.id, node.label) for node in graph.nodes] == [("dev", "dev"), ("201", "#201 root")]


def test_the_landing_preview_reads_both_cards(tmp_path):
    ctx = fake(tmp_path, replies={LEDGER: fixture("ledger-rows.json"), PR_STATUS: fixture("pr-status.json")})
    graph = stack.stack_graph(ctx, ledger="L1", repo="o/r")
    table = Table([Col("pr")], [{"key": "101", "verdict": "landable"}, {"key": "102", "verdict": "blocked:ci-pending"}, {"key": "103", "verdict": "blocked:ci-red"}])
    ctx.lookup = {"stack": graph, "review": table}.get
    assert stack.landing_preview(ctx).pairs == {"Approve #101 (main)": "lands #101; stops at #102: blocked:ci-pending", "Approve #103 (graphite-base/103)": "lands nothing; stops at #103: blocked:ci-red"}


def test_the_landing_preview_waits_for_its_cards(tmp_path):
    assert stack.landing_preview(fake(tmp_path)).pairs == {"waiting on": "cards stack and review to run once"}
