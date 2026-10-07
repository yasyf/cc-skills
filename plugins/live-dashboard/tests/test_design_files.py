from __future__ import annotations

import json
from pathlib import Path

from conftest import FIXTURES, LEDGER, PR_STATUS, fake, fixture
from livedash import Col, Table
from livedash.components import design, files, health

REGISTERS = (FIXTURES / "registers.json").read_text()


def test_design_gates_read_the_register_at_the_ref(tmp_path):
    ctx = fake(tmp_path, replies={("git", "-C", "/docs", "fetch"): "", ("git", "-C", "/docs", "show"): REGISTERS})
    gates = design.design_gates(ctx, repo_path=Path("/docs"), doc="chat-memory", ids=["V6", "Q6", "Q11", "A9", "DQ1", "Q99"], owners={"V6": "spike-lane"}, link="https://docs.example/chat-memory")
    assert [(gate.id, gate.status, gate.blocker) for gate in gates.items] == [
        ("V6", "open", None),
        ("Q6", "pass", "closed by DQ23"),
        ("Q11", "open", "blocks DQ6"),
        ("A9", "fail", None),
        ("DQ1", "pass", None),
        ("Q99", "not-created", None),
    ]
    assert gates.items[0].owner == "spike-lane" and gates.items[0].link == "https://docs.example/chat-memory#V6"
    assert ctx.calls[0][-2:] == ["origin", "main"] and ctx.calls[1][-1] == "origin/main:chat-memory/registers.json"


def test_gates_read_each_source(tmp_path):
    (tmp_path / "report.md").write_text("done")
    ctx = fake(tmp_path, replies={LEDGER: fixture("ledger-rows.json"), PR_STATUS: lambda argv: [row for row in fixture("pr-status.json") if str(row["number"]) == argv[4]], ("pup", "monitors"): fixture("monitors.json")})
    sources = {"land": "pr:101", "ci": "pr:103", "report": "file:report.md", "bench": "file:bench/*.json", "old": "ledger:99", "missing": "ledger:7", "watch": "monitor:tag:step:13", "manual": "status:waived"}
    gates = design.gates(ctx, gates=sources, repo="o/r", ledger="L1")
    assert [(gate.id, gate.status) for gate in gates.items] == [("land", "open"), ("ci", "blocked"), ("report", "pass"), ("bench", "not-run"), ("old", "pass"), ("missing", "not-created"), ("watch", "fail"), ("manual", "waived")]
    assert gates.items[1].blocker == "ci-red"


def test_the_view_engine_filters_another_cards_rows(tmp_path):
    rows = [{"key": str(n), "lane": f"lane-{n % 2}", "at": f"2026-10-06T2{n}:00:00Z", "text": f"PR #{100 + n} ready"} for n in range(4)]
    ctx = fake(tmp_path)
    ctx.lookup = {"source": Table([Col("lane")], rows)}.get
    table = files.table_view(ctx, source="source", where={"lane": "lane-1"}, match=r"#(?P<pr>\d+)", sort="-at", columns=["pr", "at:age"])
    assert [row["pr"] for row in table.rows] == ["103", "101"]
    assert [(col.key, col.kind) for col in table.cols] == [("pr", "text"), ("at", "age")]


def test_the_view_engine_reads_the_newest_matching_file(tmp_path):
    (tmp_path / "census.md").write_text("# Census\n\n| stack | state |\n|---|---|\n| core | done |\n| edge | drift |\n")
    table = files.table_view(fake(tmp_path), file="census*.md", where={"state": "!done"})
    assert table.rows == [{"stack": "edge", "state": "drift"}]
    assert files.table_view(fake(tmp_path), file="none-*.md").note == "Nothing matches none-*.md yet."


def test_log_tail_dates_lines_and_highlights_matches(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("2026-10-06T10:00:00Z start\nplain line\n2026-10-06T10:01:00Z ERROR lost the lease\n")
    feed = files.log_tail(fake(tmp_path), path=log, lines=2, highlight="ERROR")
    assert [(entry.at, entry.tone) for entry in feed.entries] == [("2026-10-06T10:01:00Z", "bad"), ("", None)]


def test_kv_file_makes_one_tile_per_key(tmp_path):
    path = tmp_path / "numbers.json"
    path.write_text(json.dumps({"recall": 0.91, "rows": 1200, "label": "v6"}))
    tiles = files.kv_file(fake(tmp_path), path=path, keys=["rows", "recall"], units={"rows": "rows"})
    assert [(tile.label, tile.value, tile.unit) for tile in tiles.tiles] == [("rows", 1200, "rows"), ("recall", 0.91, None)]


def test_dashboard_health_puts_broken_cards_first(tmp_path):
    ctx = fake(tmp_path)
    ctx.summary = lambda: [{"id": "a", "title": "A", "use": "tasks", "status": "ok", "as_of": None, "ms": 3, "error": None}, {"id": "b", "title": "B", "use": "asks", "status": "error", "as_of": None, "ms": None, "error": "exit 1: boom"}]
    assert [row["key"] for row in health.dashboard_health(ctx).rows] == ["b", "a"]
