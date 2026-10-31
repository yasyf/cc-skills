"""Recorded GitHub, queue, commit and bus answers replayed into facts rows: one read per source, whatever the PR count."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from lr import facts
from lr.bus import Bus
from lr.store import Store

T0 = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
REPO = "org/repo"
HEAD = "e718a7434fa01ee9835f992a9a647a825350732c"
HEAD2 = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
HEAD3 = "ab0de038c1100e5c66e9ad3b21b1b8bb1b1ad0bb"


def status(number, head=HEAD, queue="not queued", state="OPEN", ci="green", approval="approved", verdict="landable"):
    return {"head": head, "number": number, "queue": queue, "state": state, "base": "dev", "ci": {"state": ci}, "approval": {"state": approval}, "mergeability": "notQueued", "verdict": verdict}


class Recorded:
    def __init__(self, prs=(), statuses=(), graphql=None, holds=(), bus=()):
        self.calls: list[list[str]] = []
        self.answers = {"pr": json.dumps(list(prs)), "status": json.dumps(list(statuses)), "graphql": json.dumps(graphql or {"data": {"repository": {}}}), "hold": "\n".join(json.dumps(row) for row in holds), "bus": "\n".join(json.dumps(row) for row in bus)}

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:3] == ["ccx", "vcs", "pr"]:
            return self.answers["status"]
        if "graphql" in argv:
            return self.answers["graphql"]
        if argv[:2] == ["cci", "tail"]:
            return self.answers["hold"] if "--kind=hold" in argv else self.answers["bus"]
        return self.answers["pr"]


def pr(number, login="drive-bot", labels=()):
    return {"number": number, "author": {"login": login}, "labels": [{"name": name} for name in labels]}


def bus_row(seq, kind, body, lane="lane-7"):
    return {"seq": seq, "at": "2026-10-30T21:00:00Z", "kind": f"lr.{kind}", "lane": lane, "to": ["supervisor"], "body": body}


def test_every_open_pr_by_the_drive_author_or_label_is_admitted_with_no_prefix_registration():
    run = Recorded(prs=[pr(45757, "unregistered-user", ["drive"]), pr(1, "drive-bot"), pr(2, "someone-else")])

    assert facts.open_prs(run, REPO, "drive-bot", "drive") == [1, 45757]


@pytest.mark.parametrize("n", [1, 150])
def test_a_pass_reads_each_source_once_whatever_the_pr_count(n):
    numbers = list(range(1, n + 1))
    graphql = {"data": {"repository": {f"pr{i}": {"headRefOid": f"{i:040x}", "commits": {"nodes": [{"commit": {"oid": f"{i:040x}"}}]}} for i in numbers}}}
    run = Recorded(prs=[pr(i) for i in numbers], statuses=[status(i, head=f"{i:040x}") for i in numbers], graphql=graphql)
    store = Store.memory()

    rows = facts.snapshot(store, Bus("d", "supervisor", run), run, REPO, "d", "drive-bot", "drive")

    assert len(rows) == n
    assert [argv[:3] for argv in run.calls] == [["cci", "tail", "--drive"], ["ccx", "vcs", "gh"], ["ccx", "vcs", "pr"], ["ccx", "vcs", "gh"], ["cci", "tail", "--drive"]]


def test_queue_ci_and_mergeability_come_from_the_one_status_read():
    rows = facts.assemble(Store.memory(), REPO, [status(1, queue="evicted"), status(2, queue="queued"), status(3, ci="red", verdict="blocked:ci-red"), status(4, verdict="blocked:conflict"), status(5, verdict="blocked:mergeable-unknown"), status(6, verdict="blocked:needs-restack (parent landed),unapproved"), status(7, queue="landed", state="MERGED", ci="none")], {}, {})

    by = {row.number: row for row in rows}
    assert (by[1].queue, by[1].ci) == ("evicted", "success")
    assert (by[2].queue, by[2].queued_head) == ("queued", HEAD)
    assert by[3].ci == "failure"
    assert by[4].mergeable == "conflict"
    assert by[5].mergeable == "unknown"
    assert by[6].mergeable == "behind"
    assert (by[7].merged, by[7].open, by[7].ci) == (True, False, "pending")


def test_a_branch_carrying_a_siblings_head_is_marked_whatever_github_says_its_base_is():
    graphql = {"data": {"repository": {"pr45799": {"headRefOid": HEAD, "commits": {"nodes": [{"commit": {"oid": HEAD2}}, {"commit": {"oid": HEAD3}}, {"commit": {"oid": HEAD}}]}}, "pr45501": {"headRefOid": HEAD2, "commits": {"nodes": [{"commit": {"oid": HEAD2}}]}}, "pr45583": {"headRefOid": HEAD3, "commits": {"nodes": [{"commit": {"oid": HEAD3}}]}}}}}
    run = Recorded(graphql=graphql)

    carries = facts.carried(run, REPO, [45501, 45583, 45799])

    assert carries == {45501: (), 45583: (), 45799: (45501, 45583)}
    [argv] = run.calls
    assert argv[:7] == ["ccx", "vcs", "gh", "--max-age", "0s", "--", "api"]
    assert "pr45799: pullRequest(number: 45799)" in argv[-1]


def test_bus_records_land_as_fact_rows_by_pr_head_and_kind_and_move_the_cursor():
    store = Store.memory()
    run = Recorded(bus=[bus_row(5, "head", dict(pr=1, head=HEAD, lane="lane-7")), bus_row(6, "grade", dict(pr=1, head=HEAD, grader="finder", verdict="ask"), lane="finder-desk"), bus_row(7, "verdict", dict(pr=1, head=HEAD, verdict="accept"), lane="root"), bus_row(8, "tap", dict(pr=1, head=HEAD, by="rule", value="tapped", rule="test-only"), lane="supervisor"), bus_row(9, "grade", dict(pr=1, head=HEAD, grader="fidelity", verdict="pass"), lane="fidelity-desk")])
    records, rejected = Bus("d", "supervisor", run).read(0)

    facts.ingest(store, REPO, records)

    assert rejected == []
    assert store.facts(REPO, 1, HEAD) == {"lane": "lane-7", "finder": "ask", "verdict": "accept", "tap": "tapped", "exempt": "test-only", "fidelity": "pass"}
    assert store.cursor(facts.BUS_CURSOR) == 9
    [row] = facts.assemble(store, REPO, [status(1)], {}, {})
    assert (row.lane, row.finder, row.verdict, row.tap, row.exempt, row.cleared, row.landable) == ("lane-7", "ask", True, "tapped", True, True, True)


def test_a_clear_that_lands_after_the_pass_read_the_bus_applies_next_pass():
    store = Store.memory()
    first = Recorded(bus=[bus_row(5, "grade", dict(pr=1, head=HEAD, grader="fidelity", verdict="pass")), bus_row(6, "grade", dict(pr=1, head=HEAD, grader="finder", verdict="ask"))])
    facts.ingest(store, REPO, Bus("d", "supervisor", first).read(store.cursor(facts.BUS_CURSOR))[0])
    [before] = facts.assemble(store, REPO, [status(1)], {}, {})

    second = Recorded(bus=[bus_row(7, "verdict", dict(pr=1, head=HEAD, verdict="accept"), lane="root")])
    facts.ingest(store, REPO, Bus("d", "supervisor", second).read(store.cursor(facts.BUS_CURSOR))[0])
    [after] = facts.assemble(store, REPO, [status(1)], {}, {})

    assert (before.cleared, after.cleared) == (False, True)
    assert second.calls[0][second.calls[0].index("--since") + 1] == "6"


def test_a_hold_names_who_lifts_it():
    run = Recorded(holds=[{"kind": "hold", "status": "open", "lane": "root", "refs": {"prs": ["org/repo#45821"]}}, {"kind": "hold", "status": "open", "lane": "owner", "refs": {"prs": [30388]}}, {"kind": "hold", "status": "resolved", "lane": "root", "refs": {"prs": ["org/repo#46038"]}}])

    held = facts.holds(run, "d")

    assert held == {45821: "root", 30388: "owner"}
    [row] = facts.assemble(Store.memory(), REPO, [status(45821)], {}, held)
    assert row.hold == "root"


def test_facts_belong_to_a_head_so_a_new_push_starts_ungraded():
    store = Store.memory()
    store.put_fact(REPO, 1, HEAD2, "finder", "clear", T0)
    store.put_fact(REPO, 1, HEAD2, "tap", "tapped", T0)

    [row] = facts.assemble(store, REPO, [status(1, head=HEAD)], {}, {})

    assert (row.finder, row.tap, row.graded) == (None, None, False)
