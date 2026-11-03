"""Today's review, owner and go records read into the fact rows typed records would write."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from lr import legacy
from lr.store import Store

REPO = "org/repo"
HEAD = "e718a7434fa01ee9835f992a9a647a825350732c"


def record(seq, kind, lane, text, prs=(1,), attrs=None):
    return {"seq": seq, "at": "2026-11-02T21:00:00Z", "kind": kind, "lane": lane, "text": text, "refs": {"prs": [f"{REPO}#{n}" for n in prs]}, "fields": {"attrs": attrs} if attrs else {}}


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (record(1, "review", "finder-desk", f"finder: ok {HEAD} luna finder over delta: no majors"), [(1, HEAD, "finder", "clear")]),
        (record(2, "review", "finder-desk-2", f"finder: major {HEAD[:9]} 2 majors in the restack"), [(1, HEAD[:9], "finder", "ask")]),
        (record(3, "review", "fidelity-desk-22", f"fidelity: ok {HEAD[:7]} stays in scope"), [(1, HEAD[:7], "fidelity", "pass")]),
        (record(4, "review", "fidelity-pipeline", f"fidelity: missing {HEAD[:7]} plan:12 the plan's cap is gone"), [(1, HEAD[:7], "fidelity", "fail")]),
        (record(5, "owner", "board-watch", "Owner on board: 7 approved at e718a74", prs=(7,), attrs={"block": "merge-prs", "verdict": "approved", "head": "e718a74", "row": "7"}), [(7, "e718a74", "tap", "tapped")]),
        (record(6, "owner", "board-watch", "Owner on board: 7 rejected at e718a74", prs=(7,), attrs={"block": "merge-prs", "verdict": "rejected", "head": "e718a74", "row": "7"}), [(7, "e718a74", "tap", "withdrawn")]),
        (record(7, "go", "root", "5:26 PM go #9 at e718a74 on green [exempt: regression]", prs=(9,)), [(9, "e718a74", "exempt", "regression")]),
        (record(8, "decide", "root", "finder-override #9 at e718a74: the finding reads a deleted file", prs=(9,)), [(9, "e718a74", "verdict", "accept")]),
    ],
)
def test_each_grade_tap_and_exemption_becomes_one_fact(row, expected):
    assert legacy.facts_of(row, REPO) == expected


@pytest.mark.parametrize(
    "row",
    [
        record(1, "review", "lane-7", f"finder: ok {HEAD} my own view of the finder"),
        record(2, "review", "fidelity-desk-22", f"old-app: unchanged {HEAD[:7]} baseline"),
        record(3, "go", "lane-7", "go #9 at e718a74 [exempt: test-only]", prs=(9,)),
        record(4, "go", "root", "go #9 at e718a74 on green", prs=(9,)),
        record(5, "owner", "board-watch", "Owner picked an option", attrs={"block": "memory-choice", "event": "choice.selected"}),
    ],
)
def test_a_record_that_grades_taps_or_exempts_nothing_writes_nothing(row):
    assert legacy.facts_of(row, REPO) == []


def test_a_go_lane_the_config_names_may_name_an_exempt_class():
    row = record(1, "go", "go-carry-sweep-3", "go #9 at e718a74 on green [exempt: owner-pick]", prs=(9,))

    assert legacy.facts_of(row, REPO, ("go-carry-sweep",)) == [(9, "e718a74", "exempt", "owner-pick")]


def test_ingest_keys_facts_by_the_head_the_record_names_and_moves_its_own_cursor():
    store = Store.memory()

    legacy.ingest(store, REPO, [record(41, "review", "finder-desk", f"finder: ok {HEAD[:10]} no majors"), record(42, "owner", "board-watch", "approved", attrs={"block": "merge-prs", "verdict": "approved", "head": HEAD[:7], "row": "1"})])

    assert store.facts(REPO, 1, HEAD) == {"finder": "clear", "tap": "tapped"}
    assert store.cursor(legacy.CURSOR) == 42


def test_the_first_read_starts_three_days_back_and_later_reads_from_the_cursor():
    calls = []

    def run(argv):
        calls.append(argv)
        return ""

    legacy.read(run, "d", 0)
    legacy.read(run, "d", 512)

    assert [argv[argv.index("--since") + 1] for argv in calls] == ["72h", "512"]
    assert {arg for arg in calls[0] if arg.startswith("--kind=")} == {f"--kind={kind}" for kind in legacy.KINDS}


def test_a_later_withdrawal_supersedes_an_earlier_tap_on_the_same_head():
    store = Store.memory()
    at = datetime(2026, 11, 2, 21, 0, tzinfo=UTC)

    legacy.ingest(store, REPO, [record(1, "owner", "board-watch", "approved", attrs={"block": "merge-prs", "verdict": "approved", "head": HEAD[:7], "row": "1"})])
    store.put_fact(REPO, 1, HEAD, "tap", "withdrawn", at.replace(hour=22))

    assert store.facts(REPO, 1, HEAD)["tap"] == "withdrawn"
