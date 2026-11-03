"""The shadow pass names each PR where the v2 machine and the live queue disagree, once, and acts on nothing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from lr import loop, shadow, supervisor
from lr.machine import Facts
from lr.store import Store

T0 = datetime(2026, 11, 2, 21, 0, tzinfo=UTC)
REPO = "org/repo"
HEAD = "e718a7434fa01ee9835f992a9a647a825350732c"


def facts(number, **fields):
    base = dict(repo=REPO, number=number, head=HEAD, base="dev", lane="lane-7", ci="success", mergeable="clean", approved=True, finder="clear", fidelity="pass", tap="tapped")
    return Facts(**{**base, **fields})


def disagreements(store, snapshot, now):
    out = supervisor.run_pass(store, snapshot, now, lambda *_, **__: None)
    return shadow.diffs(store, snapshot, out.verdicts, now)


def test_a_head_ready_for_a_whole_runner_pass_and_still_unqueued_is_named_once():
    store = Store.memory()

    assert disagreements(store, [facts(1)], T0) == []
    assert disagreements(store, [facts(1)], T0 + timedelta(minutes=4)) == []
    [(subject, cause)] = disagreements(store, [facts(1)], T0 + timedelta(minutes=5))
    assert disagreements(store, [facts(1)], T0 + timedelta(minutes=10)) == []

    assert subject == f"#1 at {HEAD[:12]}"
    assert cause == "v2 would have queued it at 21:00Z; the runner has not"


def test_a_head_the_runner_queued_that_v2_would_hold_names_the_gates_it_fails():
    store = Store.memory()

    [(_, cause)] = disagreements(store, [facts(2, queue="queued", queued_head=HEAD, tap=None, approved=False)], T0)

    assert cause == "the runner queued it; v2 holds it: no approving review; no tap"


def test_agreement_is_silent():
    store = Store.memory()
    snapshot = [facts(3, queue="queued", queued_head=HEAD), facts(4, tap=None), facts(5, finder=None, fidelity=None)]

    assert disagreements(store, snapshot, T0) == []
    assert disagreements(store, snapshot, T0 + timedelta(hours=1)) == []


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


def test_the_shadow_mover_posts_notes_from_its_own_store_and_never_touches_the_queue():
    store, mirror, clock, posted = Store.memory(), Store.memory(), Clock(), []
    deps = loop.Deps(movers=frozenset({"shadow"}), clock=clock, sleep=clock.sleep, snapshot=lambda: [facts(2, queue="queued", queued_head=HEAD, tap=None)], post=lambda kind, **body: posted.append((kind, body)), shadow=mirror)

    ticks = loop.run(store, "d", "0.8.0", 1, deps, ticks=12)

    assert [t.prs for t in ticks] == [1] + [0] * 5 + [1] + [0] * 5
    assert posted == [("note", {"to": ["supervisor"], "text": f"shadow #2 at {HEAD[:12]}: the runner queued it; v2 holds it: no tap"})]
    assert store.open_items() == [] and store.prs(REPO) == []
    assert [row["number"] for row in mirror.prs(REPO)] == [2]
