"""One registry row per worker, written only by the lanes mover through its verbs, and every sender routes by that row."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from lr.movers import lanes
from lr.store import Store
from test_lr_lanes import T0, Fake, adopted, record, spawned, who

PLACEMENTS = ("orca", "sprite", "teammate")


def ask(seq: int, verb: str, lane: str, at=T0, prs: tuple[int, ...] = (), by: str = "root", **attrs) -> dict:
    typed = {"verb": verb, "lane": lane} | {key.replace("_", "-"): value for key, value in attrs.items()}
    return record(seq, by, "decide", at, f"registry request: {verb} {lane}", ("supervisor",), {"prs": [f"o/r#{n}" for n in prs]}, typed)


def spawn(seq: int, lane: str, brief: Path, at=T0, **attrs) -> dict:
    typed = {"brief": str(brief)} | {key.replace("_", "-"): value for key, value in attrs.items()}
    return {"seq": seq, "lane": "root", "kind": "spawn", "at": at.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "text": "", "to": [], "refs": {"lanes": [lane]}, "fields": {"attrs": typed}}


def posted(fake: Fake, kind: str) -> list[list[str]]:
    return [argv for argv in fake.posts if argv[argv.index("--kind") + 1] == kind]


def flag(argv: list[str], name: str) -> list[str]:
    return [argv[i + 1] for i, arg in enumerate(argv) if arg == name]


@pytest.mark.parametrize("n", [1, 1000])
def test_every_worker_has_one_row_written_only_by_the_supervisor_and_every_sender_routes_by_it(tmp_path, brief, n):
    store = Store.memory()
    names = [f"w{i:04d}" for i in range(n)]
    feed = [spawned(i + 1, name, brief, T0 - timedelta(hours=2), placement=PLACEMENTS[i % 3], role="fix" if i % 5 == 0 else "lane") for i, name in enumerate(names)]
    feed += [record(n + i + 1, name, "opened", T0 - timedelta(hours=1), refs={"prs": [f"o/r#{10_000 + i}"]}) for i, name in enumerate(names)]
    fake = Fake(tmp_path, feed, [who(name, True, T0 - timedelta(hours=1)) for name in names])

    assert store.all_lanes() == []
    lanes.tick(store, fake.deps, T0 - timedelta(hours=1), 1)

    rows = {lane.name: lane for lane in store.all_lanes()}
    assert sorted(rows) == names
    assert [rows[name].placement for name in names] == [PLACEMENTS[i % 3] for i in range(n)]
    assert [rows[name].role for name in names] == ["fix" if i % 5 == 0 else "lane" for i in range(n)]
    assert all(rows[name].prs == (10_000 + i,) for i, name in enumerate(names))

    seq = 2 * n
    relaunched, parked, stood, dropped = names[0::4], names[1::4], names[2::4], names[3::4]
    for name in relaunched:
        seq += 1
        fake.feed.append(spawned(seq, f"{name}-next", brief, T0, placement="orca", **{"successor-of": name}))
    for name in parked:
        seq += 1
        fake.feed.append(ask(seq, "park", name))
    for name in stood:
        seq += 1
        fake.feed.append(ask(seq, "stand-down", name, successor=f"{name}-heir", last_read=str(seq)))
    for name in dropped:
        seq += 1
        fake.feed.append(ask(seq, "stand-down", name, last_read=str(seq)))
    fake.peers = [who(name, True, T0) for name in [*names, *(f"{name}-next" for name in relaunched), *(f"{name}-heir" for name in stood)]]
    fake.posts.clear()

    lanes.tick(store, fake.deps, T0, 1)

    rows = {lane.name: lane for lane in store.all_lanes()}
    assert len(rows) == n + len(relaunched) + len(stood)
    for i, name in enumerate(names):
        heir = f"{name}-next" if name in relaunched else f"{name}-heir" if name in stood else None
        assert lanes.route(store, name) == (heir or (lanes.ROOT if name in dropped else name))
        owner = heir or (lanes.ROOT if name in dropped else name)
        assert store.owner_of([10_000 + i]) == {10_000 + i: owner}
        assert (rows[name].waiting is not None) == (name in parked)
    downs = posted(fake, "stand-down")
    assert sorted(flag(argv, "--to")[0] for argv in downs) == sorted([*relaunched, *stood, *dropped])
    assert {flag(argv, "--to")[0]: flag(argv, "--successor") for argv in downs} == {name: [heir] if (heir := f"{name}-next" if name in relaunched else f"{name}-heir" if name in stood else None) else [] for name in [*relaunched, *stood, *dropped]}
    assert {row["lane"]: row["route"] for row in lanes.liveness(store, T0) if row["lane"] in rows} == {name: lanes.route(store, name) for name in rows}


def test_records_alone_never_write_the_registry(tmp_path, brief):
    store = Store.memory()
    Fake(tmp_path, [spawned(1, "w1", brief), ask(2, "stand-down", "w1", successor="w2")])
    assert store.all_lanes() == [] and store.owners() == {}


def test_a_stand_down_request_routes_mail_to_the_successor_through_one_supervisor_record(tmp_path, brief):
    """cc-skills#1311 and #1335: mail to a stood-down lane reaches its successor, and cci learns it from one stand-down record."""
    store = Store.memory()
    fake = adopted(tmp_path, brief, store, prs=(46001,))
    fake.feed.append(ask(5, "stand-down", "conflicts-2", successor="conflicts-heir", last_read="1"))

    lanes.tick(store, fake.deps, T0, 1)

    assert lanes.route(store, "conflicts-2") == "conflicts-heir" and store.owner_of([46001]) == {46001: "conflicts-heir"}
    [down] = posted(fake, "stand-down")
    assert flag(down, "--to") == ["conflicts-2"] and flag(down, "--successor") == ["conflicts-heir"] and flag(down, "--pr") == ["46001"] and flag(down, "--re") == ["5"]


def test_a_stand_down_request_naming_no_successor_hands_the_prs_to_root(tmp_path, brief):
    """cc-skills#1556: a stood-down lane's PRs go to root, never stay on the retired lane."""
    store = Store.memory()
    fake = adopted(tmp_path, brief, store, prs=(46001,))
    fake.feed.append(ask(5, "stand-down", "conflicts-2", last_read="1"))

    lanes.tick(store, fake.deps, T0, 1)

    assert lanes.route(store, "conflicts-2") == lanes.ROOT and store.owner_of([46001]) == {46001: lanes.ROOT}
    assert store.lane("conflicts-2").state == "retired"


def test_a_stand_down_request_for_a_lane_already_done_still_hands_its_prs_to_root(tmp_path, brief):
    """cc-skills#1578: a lane that retired on its own done still hands its PRs to root."""
    store = Store.memory()
    fake = adopted(tmp_path, brief, store, prs=(46001,))
    fake.feed += [record(4, "conflicts-2", "done", T0 - timedelta(minutes=1)), ask(5, "stand-down", "conflicts-2")]

    lanes.tick(store, fake.deps, T0, 1)

    assert store.owner_of([46001]) == {46001: lanes.ROOT}


@pytest.mark.parametrize(("cited", "state"), [(None, "held"), ("3", "held"), ("4", "retired")])
def test_a_stand_down_request_that_read_none_of_the_lanes_newest_records_is_refused_and_holds_the_lane(tmp_path, brief, cited, state):
    """cc-skills#1656 and #1678: a stand-down citing no read of the lane's newest record leaves it with its PRs, held, and tells root and the lane."""
    store = Store.memory()
    fake = adopted(tmp_path, brief, store, prs=(46001,))
    fake.feed += [record(4, "conflicts-2", "state", T0 - timedelta(minutes=1), "restacking #46001"), ask(5, "stand-down", "conflicts-2", successor="conflicts-heir", **({"last_read": cited} if cited else {}))]

    lanes.tick(store, fake.deps, T0, 1)

    assert store.lane("conflicts-2").state == state
    assert store.owner_of([46001]) == {46001: "conflicts-2" if state == "held" else "conflicts-heir"}
    assert bool(posted(fake, "defect")) == (state == "held") and bool(posted(fake, "stand-down")) == (state == "retired")


def test_a_stand_down_request_retires_only_the_lane_it_names(tmp_path, brief):
    """cc-skills#1499 and #1529: a stand-down retires the lane it names and never a lane copied on the record."""
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.feed += [spawned(4, "desk-a", brief, T0 - timedelta(minutes=5)), record(5, "root", "decide", T0, "registry request: stand-down conflicts-2", ("supervisor", "desk-a"), attrs={"verb": "stand-down", "lane": "conflicts-2", "last-read": "5"})]
    fake.peers.append(who("desk-a", True, T0))

    lanes.tick(store, fake.deps, T0, 1)

    assert store.lane("conflicts-2").state == "retired" and store.lane("desk-a").state != "retired"


@pytest.mark.parametrize(("unpark", "woken"), [(False, []), (True, ["conflicts-2"])])
def test_a_parked_lane_is_never_woken_until_an_unpark_request(tmp_path, brief, unpark, woken):
    """cc-skills#1653: a lane root parked is waiting, never woken, until root lifts the park."""
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.peers = [who("conflicts-2", True, T0 - timedelta(minutes=30), roster="/teams/session-d50e80e8")]
    fake.feed += [ask(2, "park", "conflicts-2", T0 - timedelta(minutes=30)), *([ask(3, "unpark", "conflicts-2", T0 - timedelta(minutes=29))] if unpark else [])]
    lanes.tick(store, fake.deps, T0 - timedelta(minutes=29), 1)
    fake.wakes.clear()

    lanes.tick(store, fake.deps, T0 + timedelta(minutes=20), 1)

    assert fake.wakes == woken


def test_an_owner_request_gives_each_pr_one_owner(tmp_path, brief):
    """cc-skills#1505: each PR has one owner, the row that lists it."""
    store = Store.memory()
    fake = adopted(tmp_path, brief, store, prs=(46001,))
    fake.feed += [spawned(4, "desk-a", brief, T0 - timedelta(minutes=5)), ask(5, "owner", "desk-a", prs=(46001, 46002), last_read="5")]
    fake.peers.append(who("desk-a", True, T0))

    lanes.tick(store, fake.deps, T0, 1)

    assert store.owners() == {"desk-a": [46001, 46002]}


def test_an_owner_request_naming_a_lane_that_left_its_ask_unanswered_tells_root(tmp_path, brief):
    """cc-skills#1532: root naming a quiet owner the supervisor already found gone hears so once."""
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    store.probe("conflicts-2", T0 - timedelta(hours=1))
    store.probe_dead("conflicts-2", T0 - timedelta(minutes=40))
    fake.feed.append(ask(5, "owner", "conflicts-2", prs=(46009,)))

    lanes.tick(store, fake.deps, T0, 1)

    [blocker] = posted(fake, "blocker")
    assert "conflicts-2 for #46009: root named it owner" in blocker[-1]


def test_a_done_request_from_the_runner_retires_a_finished_worker_for_good(tmp_path, brief):
    """cc-skills#1606: a finished worker is terminal, never relaunched."""
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.feed.append(ask(5, "done", "conflicts-2", T0 - timedelta(minutes=20), by="desk-runner"))
    fake.peers = [who("conflicts-2", False, T0 - timedelta(minutes=20))]

    lanes.tick(store, fake.deps, T0, 1)

    assert store.lane("conflicts-2").state == "retired" and fake.launches == []


def test_a_launch_naming_the_worker_it_succeeds_takes_its_prs_whatever_its_name(tmp_path, brief):
    """cc-skills#1529 and #1559: a successor takes its predecessor's PRs from the launch that names it, never from a guess at its name."""
    store = Store.memory()
    fake = adopted(tmp_path, brief, store, prs=(46001,))
    fake.feed.append(spawned(5, "restack-desk", brief, T0, placement="sprite", **{"successor-of": "conflicts-2"}))
    fake.peers.append(who("restack-desk", True, T0))

    lanes.tick(store, fake.deps, T0, 1)

    assert lanes.route(store, "conflicts-2") == "restack-desk" and store.owners() == {"restack-desk": [46001]}
    assert store.lane("restack-desk").placement == "sprite"


def test_a_launch_naming_the_worker_it_succeeds_tells_cci_once_so_every_senders_records_reach_the_successor(tmp_path, brief):
    """cc-skills#1335: a rotation records its successor in cci, so a record any lane addresses to the old name reaches the new one."""
    store = Store.memory()
    fake = adopted(tmp_path, brief, store, prs=(46001,))
    fake.feed.append(spawned(5, "conflicts-3", brief, T0, placement="teammate", **{"successor-of": "conflicts-2"}))
    fake.peers.append(who("conflicts-3", True, T0))

    lanes.tick(store, fake.deps, T0, 1)

    [down] = posted(fake, "stand-down")
    assert flag(down, "--to") == ["conflicts-2"] and flag(down, "--successor") == ["conflicts-3"] and flag(down, "--pr") == ["46001"]


def test_a_cci_spawn_record_is_a_workers_birth(tmp_path, brief):
    store = Store.memory()
    fake = Fake(tmp_path, [spawn(1, "w1", brief, T0 - timedelta(minutes=1), model="opus", placement="teammate")], [who("w1", True, T0)])

    lanes.tick(store, fake.deps, T0, 1)

    row = store.lane("w1")
    assert (row.state, row.model, row.placement) == ("desired", "opus", "teammate")
