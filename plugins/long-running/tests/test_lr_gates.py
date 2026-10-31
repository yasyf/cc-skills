"""A tap follows a patch-identical rebase and nothing else; a head lands only once every gate is met."""

from __future__ import annotations

from datetime import UTC, datetime

from lr import gates
from lr.machine import Facts
from lr.store import Store

T0 = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
REPO = "org/repo"
OLD = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
NEW = "e718a7434fa01ee9835f992a9a647a825350732c"


def tapped_store(exempt=None):
    store = Store.memory()
    store.put_fact(REPO, 45744, OLD, "tap", "tapped", T0)
    if exempt:
        store.put_fact(REPO, 45744, OLD, "exempt", exempt, T0)
    return store


def test_a_tap_carries_to_a_patch_identical_head_and_says_where_it_came_from():
    store = tapped_store()

    assert gates.carry_tap(store, REPO, 45744, OLD, NEW, {OLD: "p1", NEW: "p1"}, T0)
    assert store.facts(REPO, 45744, NEW) == {"tap": "tapped", "carried-from": OLD}


def test_a_tap_stays_behind_when_the_patch_changed():
    store = tapped_store()

    assert not gates.carry_tap(store, REPO, 45744, OLD, NEW, {OLD: "p1", NEW: "p2"}, T0)
    assert store.facts(REPO, 45744, NEW) == {}


def test_a_withdrawn_tap_never_carries():
    store = Store.memory()
    store.put_fact(REPO, 45911, OLD, "tap", "withdrawn", T0)

    assert not gates.carry_tap(store, REPO, 45911, OLD, NEW, {OLD: "p1", NEW: "p1"}, T0)


def test_an_unknown_patch_id_never_carries():
    assert not gates.carry_tap(tapped_store(), REPO, 45744, OLD, NEW, {NEW: "p1"}, T0)


def test_an_exemption_carries_with_its_tap():
    store = tapped_store(exempt="test-only")
    gates.carry_tap(store, REPO, 45744, OLD, NEW, {OLD: "p1", NEW: "p1"}, T0)

    assert store.facts(REPO, 45744, NEW)["exempt"] == "test-only"


def test_a_carried_tap_admits_nothing_until_the_new_head_is_green():
    carried = Facts(repo=REPO, number=45717, head=NEW, base="dev", lane="l", ci="pending", mergeable="clean", finder="clear", fidelity="pass", tap="tapped")

    assert gates.missing(carried) == ["CI is not green"]
    assert not carried.landable


def test_missing_names_every_open_gate():
    facts = Facts(repo=REPO, number=1, head=NEW, base="dev", lane="l", ci="failure", mergeable="conflict", finder="ask", fidelity="pass", tap=None, hold="root", carries=(2,))

    assert gates.missing(facts) == ["CI is not green", "mergeability is conflict", "branch carries #2", "a grade needs a verdict", "no tap", "held by root"]


def test_nothing_missing_means_landable():
    facts = Facts(repo=REPO, number=1, head=NEW, base="dev", lane="l", ci="success", mergeable="clean", finder="clear", fidelity="pass", tap="tapped")

    assert gates.missing(facts) == []
    assert facts.landable
