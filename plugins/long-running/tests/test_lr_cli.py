"""`lr post` is the one way to write a record and prints the fix when refused; `lr status` is the root's one read."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from lr import cli
from lr.store import Store

T0 = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
HEAD = "e718a7434fa01ee9835f992a9a647a825350732c"
HEAD2 = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
REPO = "org/repo"


def fake_cci(argv):
    body = json.loads(open(argv[argv.index("--body-file") + 1]).read())
    return json.dumps({"seq": 42, "at": "2026-10-31T06:00:00Z", "kind": argv[argv.index("--kind") + 1], "lane": "finder-desk", "to": ["supervisor"], "body": body})


def test_post_writes_one_typed_record(capsys):
    code = cli.main(["post", "grade", "--drive", "d", "--as", "finder-desk", "--pr", "45821", "--head", HEAD, "--grader", "finder", "--verdict", "clear", "--to", "supervisor"], run=fake_cci)

    assert code == 0
    assert capsys.readouterr().out == "#42 grade\n"


def test_post_prints_the_corrected_command_when_refused(capsys):
    code = cli.main(["post", "note", "--drive", "d", "--as", "finder-desk", "--pr", "45821", "--head", HEAD, "--grader", "finder", "--verdict", "clear"], run=fake_cci)

    assert code == 2
    assert capsys.readouterr().err == f"lr post: refused: these fields are a grade record, not note; run: lr post grade --pr 45821 --head {HEAD} --grader finder --verdict clear\n"


def test_post_refuses_a_state_record_addressed_to_root(capsys):
    code = cli.main(["post", "state", "--drive", "d", "--as", "lane-7", "--lane", "lane-7", "--to", "root"], run=fake_cci)

    assert code == 2
    assert "never reaches root" in capsys.readouterr().err


def test_status_counts_states_overdue_items_owner_asks_and_waiting_heads():
    store = Store.memory()
    store.put_pr(REPO, 45583, HEAD, "dev", "lane-1", "ready", T0)
    store.put_pr(REPO, 45601, HEAD2, HEAD, "lane-2", "grading", T0)
    store.put_pr(REPO, 45602, "1" * 40, HEAD, "lane-3", "awaiting-tap", T0)
    store.put_pr(REPO, 45000, "2" * 40, "dev", "lane-4", "landed", T0)
    store.open_item("verdict", REPO, 45601, HEAD2, "lane", T0, timedelta(minutes=20))
    store.open_item("tap", REPO, 45602, "1" * 40, "owner", T0, timedelta(hours=12))

    lines = cli.status_lines(store, REPO, T0 + timedelta(minutes=25))

    assert lines == [
        "states: grading 1, awaiting-tap 1, ready 1, landed 1",
        "overdue: 1",
        "  #45601 verdict lane since 21:20Z",
        "owner asks: 1",
        "  #45602 tap 111111111111",
        "base #45583 holds 2: #45601, #45602",
    ]
