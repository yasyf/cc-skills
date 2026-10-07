from __future__ import annotations

import re

import pytest
from livedash import Cell, Col, Dist, Graph, Matrix, Node, Table, payloads, registry, secrets
from livedash.server import PAGE


def test_every_payload_kind_has_a_page_renderer_and_a_markdown_form():
    rendered = set(re.search(r"const RENDER=\{(.*?)\};", PAGE.read_text())[1].split(","))
    names = {entry.split(":", 1)[0] for entry in rendered}
    for cls in registry.PAYLOADS.values():
        example = cls.example()
        assert cls.kind in names
        assert payloads.problems(example) == []
        assert payloads.markdown(cls.kind, example.json())


def test_empty_payloads_keep_the_arrays_the_page_reads():
    assert Graph([], []).json() == {"kind": "graph", "nodes": [], "edges": []}
    assert Table([], []).json() == {"kind": "table", "cols": [], "rows": []}
    assert payloads.markdown("graph", Graph([], []).json()) == ""


def test_percentiles_use_the_nearest_rank():
    dist = Dist.of("x", [float(n) for n in range(1, 101)])
    assert (dist.n, dist.p50, dist.p95, dist.p99, dist.max) == (100, 50.0, 95.0, 99.0, 100.0)
    assert Dist.of("one", [7.0]).p99 == 7.0


def test_a_matrix_is_checked_against_the_shared_schema():
    good = Matrix(["#101"], ["Ruling A", "Ruling B"], [[Cell("meets", "ok"), Cell("deviates", "warn", "uses the old name")]])
    assert good.schema_errors() == []
    assert Matrix(["#101"], ["Ruling A", "Ruling B"], [[Cell(""), Cell("meets")]]).schema_errors()
    with pytest.raises(payloads.PayloadError, match="one row of 2 cells per row label"):
        Matrix(["#101"], ["Ruling A", "Ruling B"], [[Cell("meets")]])


def test_payloads_reject_bad_tones_kinds_and_dangling_edges():
    with pytest.raises(payloads.PayloadError):
        Table([Col("a")], [{"a": 1, "tone": "red"}])
    with pytest.raises(payloads.PayloadError):
        Col("a", "A", "sparkline")
    with pytest.raises(payloads.PayloadError):
        Graph([Node("a", "A")], [["a", "b"]])


def test_an_oversized_payload_is_a_problem():
    assert payloads.problems(Table([Col("a")], [{"a": "x" * 1024} for _ in range(300)])) == ["payload is 303KB, over the 256KB cap"]


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("head " + "a1b2c3d4" * 5, []),
        ("key sk-" + "A" * 24, ["token-shaped"]),
        ("slack xoxb-1234567890-abcdef", ["token-shaped"]),
        ("auth Bearer abcdefghijklmnop", ["token-shaped"]),
        ("value hunter2hunter2", ["MY_TOKEN"]),
        ("short pw", []),
    ],
)
def test_secret_scan_names_what_it_found_and_never_the_value(text, found):
    assert secrets.hits(text, {"MY_TOKEN": "hunter2hunter2", "SHORT_PASSWORD": "pw", "HOME": "/Users/x"}) == found
