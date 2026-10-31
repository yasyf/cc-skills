"""The board shows one row per owner item, never an answered one, and a patch-identical rebase needs no second tap."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from lr import board, gates
from lr.machine import Facts
from lr.store import Store

T0 = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
REPO = "org/repo"
HEAD = "e718a7434fa01ee9835f992a9a647a825350732c"
NEW = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"


def facts(number=45583, head=HEAD, **fields):
    base = dict(repo=REPO, number=number, head=head, base="dev", lane="lane-7", ci="success", mergeable="clean", finder="clear", fidelity="pass", tap=None)
    return Facts(**{**base, **fields})


class Posts(list):
    def __call__(self, kind, to=(), **body):
        self.append((kind, tuple(to), body))


@pytest.fixture
def store():
    store = Store.memory()
    store.open_item("tap", REPO, 45583, HEAD, "owner", T0, timedelta(hours=12))
    return store


def test_an_owner_item_is_one_row_keyed_by_pr_and_head(store):
    [row] = board.rows(store, REPO, {45583: facts()})

    assert (row.pr, row.head, row.kind, row.gates, row.carried_from) == (45583, HEAD, "tap", ("no tap",), None)
    [block] = board.render([row], answered=set())
    assert block == {"id": f"tap-45583-{HEAD[:12]}", "type": "choice", "title": f"#45583 at {HEAD[:12]}: tap", "body": "every other gate is met", "options": ["Merge", "Hold", "Explain more"]}


def test_an_answered_key_never_renders_again(store):
    rows = board.rows(store, REPO, {45583: facts()})

    assert board.render(rows, answered={(45583, HEAD)}) == []


def test_merge_becomes_a_tap_fact_and_a_tap_record(store):
    posts, holds = Posts(), []
    [row] = board.rows(store, REPO, {45583: facts()})

    board.answer(store, REPO, posts, lambda pr, head: holds.append((pr, head)), row, "Merge", T0)

    assert store.facts(REPO, 45583, HEAD)["tap"] == "tapped"
    assert posts == [("tap", ("supervisor",), dict(pr=45583, head=HEAD, by="owner", value="tapped"))]
    assert holds == []
    assert board.rows(store, REPO, {45583: facts(tap="tapped")}) == []


def test_hold_goes_to_the_hold_hook_and_taps_nothing(store):
    posts, holds = Posts(), []
    [row] = board.rows(store, REPO, {45583: facts()})

    board.answer(store, REPO, posts, lambda pr, head: holds.append((pr, head)), row, "Hold", T0)

    assert holds == [(45583, HEAD)]
    assert posts == []
    assert store.facts(REPO, 45583, HEAD) == {}


def test_explain_more_changes_nothing(store):
    [row] = board.rows(store, REPO, {45583: facts()})
    board.answer(store, REPO, Posts(), lambda pr, head: None, row, "Explain more", T0)

    assert store.facts(REPO, 45583, HEAD) == {}


def test_an_unknown_answer_is_refused(store):
    [row] = board.rows(store, REPO, {45583: facts()})
    with pytest.raises(ValueError, match="not a board answer: 'Ship it'"):
        board.answer(store, REPO, Posts(), lambda pr, head: None, row, "Ship it", T0)


def test_a_patch_identical_rebase_carries_the_tap_and_asks_nothing(store):
    store.put_fact(REPO, 45583, HEAD, "tap", "tapped", T0)
    store.open_item("tap", REPO, 45583, NEW, "owner", T0 + timedelta(hours=1), timedelta(hours=12))
    gates.carry_tap(store, REPO, 45583, HEAD, NEW, {HEAD: "p1", NEW: "p1"}, T0 + timedelta(hours=1))

    assert board.rows(store, REPO, {45583: facts(head=NEW, tap="tapped")}) == []


def test_a_rebase_that_changed_the_patch_asks_again_and_says_what_moved(store):
    store.put_fact(REPO, 45583, HEAD, "tap", "tapped", T0)
    store.open_item("tap", REPO, 45583, NEW, "owner", T0 + timedelta(hours=1), timedelta(hours=12))

    [row] = board.rows(store, REPO, {45583: facts(head=NEW)})
    assert (row.head, row.carried_from) == (NEW, None)


def test_a_row_names_every_other_gate_the_head_still_fails(store):
    [row] = board.rows(store, REPO, {45583: facts(ci="pending")})

    assert board.render([row], set())[0]["body"] == "CI is not green"


def test_a_stale_item_for_an_older_head_renders_nothing(store):
    assert board.rows(store, REPO, {45583: facts(head=NEW)}) == []
