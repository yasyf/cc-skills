"""The lanes mover keeps every desired lane alive with no root action and touches no live process."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lr.movers import lanes
from lr.movers.lanes import Peer, classify_lane, successor_name
from lr.store import Store

T0 = datetime(2026, 10, 31, 17, 0, tzinfo=UTC)
DRIVE = "d1"
CHECKOUT = "/work/monorepo"


def iso(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def spawned(seq: int, lane: str, brief: Path, at: datetime = T0 - timedelta(hours=1), **attrs) -> dict:
    return {"seq": seq, "lane": "root", "kind": "state", "at": iso(at), "text": f"SPAWNED {lane} {brief}", "fields": {"attrs": {"brief": str(brief), **attrs}}}


def record(seq: int, lane: str, kind: str, at: datetime = T0, text: str = "", to: tuple[str, ...] = (), refs: dict | None = None, attrs: dict | None = None) -> dict:
    return {"seq": seq, "lane": lane, "kind": kind, "at": iso(at), "text": text, "to": list(to), "refs": refs or {}, "fields": {"attrs": attrs} if attrs else {}}


def who(lane: str, live: bool, seen: datetime, **fields) -> dict:
    return {"lane": lane, "live": live, "seen_at": iso(seen), "session": "d50e80e8", "agent": f"a{lane}-0123456789abcdef"} | fields


class Fake:
    def __init__(self, tmp: Path, records: list[dict] | None = None, peers: list[dict] | None = None, tokens: dict[str, int] | None = None):
        self.feed = records or []
        self.peers = peers or []
        self.token_counts = tokens or {}
        self.posts: list[list[str]] = []
        self.launches: list[tuple[str, str, str, Path]] = []
        self.withdrawals: list[str] = []
        self.deps = lanes.Deps(
            drive=DRIVE,
            checkout=CHECKOUT,
            briefs=tmp / "lanes",
            records=lambda since: [r for r in self.feed if r["seq"] > since],
            who=lambda names: self.peers,
            tokens=lambda row: self.token_counts.get(row["lane"]),
            transcript=lambda row: Path(f"/projects/x/{row['session']}/subagents/agent-{row['agent']}.jsonl"),
            cci=self.cci,
            launch=lambda name, model, effort, brief: self.launches.append((name, model, effort, brief)),
            withdraw=self.withdrawals.append,
            rotate_at=600_000,
        )

    def cci(self, argv: list[str]) -> str:
        self.posts.append(argv)
        return ""

    def kinds(self) -> list[tuple[str, str]]:
        return [(argv[argv.index("--kind") + 1], argv[argv.index("--to") + 1]) for argv in self.posts]


@pytest.fixture
def brief(tmp_path: Path) -> Path:
    path = tmp_path / "brief-conflicts-2.md"
    path.write_text("# conflicts-2: restack the five approved stacks\nRegister slice: lane=conflicts-2 step=tooling\n")
    return path


def adopted(tmp_path: Path, brief: Path, store: Store, handoff: str | None = None, prs: tuple[int, ...] = ()) -> Fake:
    fake = Fake(tmp_path, [spawned(1, "conflicts-2", brief, model="opus", effort="xhigh")], [who("conflicts-2", True, T0 - timedelta(minutes=1))])
    lanes.tick(store, fake.deps, T0 - timedelta(minutes=1), 1)
    if handoff:
        store.lane_handoff("conflicts-2", handoff, None)
    store.lane_prs("conflicts-2", prs)
    fake.posts.clear()
    return fake


def test_a_dead_lane_relaunches_with_its_handoff_and_stands_down_once_its_successor_is_up(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store, handoff="9f3c2e1", prs=(46001, 46002))
    assert [row["name"] for row in store.workers()] == ["conflicts-2"]
    fake.peers = [who("conflicts-2", False, T0 - timedelta(minutes=3)), who("conflicts-3", False, T0 - timedelta(hours=9))]

    out = lanes.tick(store, fake.deps, T0, 1)

    assert out.relaunched == [("conflicts-2", "conflicts-4", "stopped at 9:57 AM")]
    [(name, model, effort, path)] = fake.launches
    assert (name, model, effort) == ("conflicts-4", "opus", "xhigh")
    text = path.read_text()
    assert text.startswith("Replaces: conflicts-2\n")
    assert f"ccn -R {CHECKOUT} doc show 9f3c2e1" in text and "#46001, #46002" in text
    assert text.endswith(brief.read_text())
    assert fake.kinds() == [("report", "root")]
    assert store.lane("conflicts-2").state == "retired" and store.lane("conflicts-2").retired_by == "conflicts-4"
    successor = store.lane("conflicts-4")
    assert (successor.state, successor.handoff, successor.prs, successor.predecessor) == ("launching", "9f3c2e1", (46001, 46002), "conflicts-2")
    assert store.workers() == []
    fake.posts.clear()
    fake.peers = [who("conflicts-4", True, T0 + timedelta(minutes=2))]

    lanes.tick(store, fake.deps, T0 + timedelta(minutes=2), 1)

    [stand_down] = fake.posts
    assert stand_down[stand_down.index("--to") + 1] == "conflicts-2" and stand_down[stand_down.index("--successor") + 1] == "conflicts-4"
    assert [stand_down[i + 1] for i, arg in enumerate(stand_down) if arg == "--pr"] == ["46001", "46002"]
    assert store.lane("conflicts-4").state == "desired"


def test_a_relaunch_brief_keeps_the_lane_on_the_mac_and_carries_its_register_slice(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.peers = [who("conflicts-2", False, T0 - timedelta(minutes=30))]

    lanes.tick(store, fake.deps, T0, 1)

    assert fake.launches[0][3].read_text().splitlines()[:3] == ["Replaces: conflicts-2", "ccx: place=local", "Register slice: lane=conflicts-3 step=tooling"]


def test_a_dead_lane_with_a_live_numbered_successor_is_retired_not_relaunched(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.peers = [who("conflicts-2", False, T0 - timedelta(minutes=30)), who("conflicts-5", True, T0)]

    out = lanes.tick(store, fake.deps, T0, 1)

    assert out.relaunched == [] and fake.launches == [] and fake.posts == []
    assert store.lane("conflicts-2").retired_by == "conflicts-5"


@pytest.mark.parametrize(("by_hand", "withdrawn"), [(True, ["conflicts-3"]), (False, [])])
def test_a_lane_spawned_by_hand_under_a_pending_relaunchs_name_withdraws_the_relaunch(tmp_path, brief, by_hand, withdrawn):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.peers = [who("conflicts-2", False, T0 - timedelta(minutes=30))]
    lanes.tick(store, fake.deps, T0, 1)
    spawned_from = tmp_path / "root" / "conflicts-3.md" if by_hand else fake.launches[0][3]
    fake.feed.append(spawned(2, "conflicts-3", spawned_from, at=T0 + timedelta(minutes=1)))

    out = lanes.tick(store, fake.deps, T0 + timedelta(minutes=2), 1)

    assert fake.withdrawals == withdrawn and out.withdrawn == withdrawn


def test_a_dead_lane_with_no_handoff_gets_a_successor_that_writes_one_from_the_transcript(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.peers = [who("conflicts-2", False, T0 - timedelta(minutes=30))]

    lanes.tick(store, fake.deps, T0, 1)

    text = fake.launches[0][3].read_text()
    assert "conflicts-2 left no handoff" in text and "named `conflicts-2-handoff`" in text
    assert "/projects/x/d50e80e8/subagents/agent-aconflicts-2-0123456789abcdef.jsonl" in text
    assert str(lanes.HANDOFF_BRIEF) in text


def test_a_flushed_lane_rotates_without_root(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.peers = [who("conflicts-2", True, T0)]
    fake.token_counts = {"conflicts-2": 640_000}

    lanes.tick(store, fake.deps, T0, 1)

    assert fake.kinds() == [("ask", "conflicts-2")] and "ROTATE" in fake.posts[0][-1]
    fake.posts.clear()
    fake.feed.append(record(2, "conflicts-2", "handoff", T0 + timedelta(minutes=4), "flushed 9a1b2c3", ("root",), {"ccn": "9a1b2c3"}))
    out = lanes.tick(store, fake.deps, T0 + timedelta(minutes=5), 1)

    assert [(old, new) for old, new, _ in out.relaunched] == [("conflicts-2", "conflicts-3")]
    assert "doc show 9a1b2c3" in fake.launches[0][3].read_text()
    assert fake.kinds() == [("report", "root")]
    assert not [argv for argv in fake.posts if argv[argv.index("--kind") + 1] in ("ask", "blocker", "decide")]


def test_a_lane_that_ignores_rotate_is_rotated_after_one_interval(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.peers = [who("conflicts-2", True, T0)]
    fake.token_counts = {"conflicts-2": 640_000}
    lanes.tick(store, fake.deps, T0, 1)
    fake.peers = [who("conflicts-2", True, T0 + timedelta(minutes=15))]

    out = lanes.tick(store, fake.deps, T0 + timedelta(minutes=15), 1)

    assert out.relaunched[0][2] == "posted no handoff within 15 minutes of its ROTATE ask at 10:00 AM"


def test_a_protected_session_is_never_touched(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    root = who("root", True, T0, role="root")
    other = who("p8a-step7", True, T0 - timedelta(hours=2), env={"ORCA_TERMINAL_HANDLE": "term_08c7"})
    fake.peers = [who("conflicts-2", True, T0 - timedelta(minutes=20)), root, other]

    lanes.tick(store, fake.deps, T0, 1)

    assert all(argv[0] == "post" for argv in fake.posts)
    assert fake.kinds() == [("report", "root")]
    assert [name for name, *_ in fake.launches] == ["conflicts-3"]
    assert {lane.name for lane in store.all_lanes()} == {"conflicts-2", "conflicts-3"}


def test_the_registry_survives_a_root_compaction(tmp_path, brief):
    path = tmp_path / "lr.sqlite"
    fake = Fake(tmp_path, [spawned(1, "conflicts-2", brief), record(2, "conflicts-2", "opened", refs={"prs": ["Forge-AI/monorepo#46001"]}), record(3, "conflicts-2", "handoff", refs={"ccn": "9f3c2e1"})], [who("conflicts-2", True, T0)])
    lanes.tick(Store.open(path), fake.deps, T0, 1)

    reopened = Store.open(path).lane("conflicts-2")
    rebuilt = Store.memory()
    lanes.tick(rebuilt, fake.deps, T0, 1)

    assert (reopened.state, reopened.brief, reopened.handoff, reopened.prs) == ("desired", str(brief), "9f3c2e1", (46001,))
    assert rebuilt.lane("conflicts-2") == reopened


def test_an_opened_record_naming_its_pr_by_bare_number_or_by_repo_adds_both(tmp_path, brief):
    store = Store.memory()
    fake = Fake(tmp_path, [spawned(1, "conflicts-2", brief), record(2, "conflicts-2", "opened", refs={"prs": [46001, "Forge-AI/monorepo#46002"]})], [who("conflicts-2", True, T0)])

    lanes.tick(store, fake.deps, T0, 1)

    assert store.lane("conflicts-2").prs == (46001, 46002)


def test_a_lane_that_ended_its_turn_on_an_ask_to_root_waits_until_the_answer_goes_unheeded(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.feed.append(record(2, "conflicts-2", "ask", T0, "pick A or B?", ("root",)))
    fake.peers = [who("conflicts-2", False, T0)]

    for later in (timedelta(minutes=1), timedelta(hours=3)):
        lanes.tick(store, fake.deps, T0 + later, 1)
    fake.feed.append(record(3, "root", "answer", T0 + timedelta(hours=3), "A", ("conflicts-2",)) | {"re": 2})
    lanes.tick(store, fake.deps, T0 + timedelta(hours=3, minutes=10), 1)

    assert fake.launches == [] and fake.posts == []

    out = lanes.tick(store, fake.deps, T0 + timedelta(hours=3, minutes=16), 1)

    assert out.relaunched == [("conflicts-2", "conflicts-3", "stopped at 10:00 AM")]


def test_a_lane_waiting_on_root_whose_session_cci_no_longer_lists_is_gone(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.feed.append(record(2, "conflicts-2", "ask", T0, "pick A or B?", ("root",)))
    fake.peers = []

    out = lanes.tick(store, fake.deps, T0 + timedelta(minutes=16), 1)

    assert [old for old, *_ in out.relaunched] == ["conflicts-2"]


def test_a_desk_whose_last_record_is_a_blocker_to_another_lane_is_not_waiting(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.feed.append(record(2, "conflicts-2", "blocker", T0, "#46001 red", ("p8-lane",)))
    fake.peers = [who("conflicts-2", False, T0)]

    out = lanes.tick(store, fake.deps, T0 + timedelta(minutes=16), 1)

    assert [old for old, *_ in out.relaunched] == ["conflicts-2"]


def test_a_fresh_store_never_adopts_a_lane_cci_lists_live_but_silent_past_the_interval(tmp_path, brief):
    store = Store.memory()
    fake = Fake(tmp_path, [spawned(1, "conflicts-2", brief)], [who("conflicts-2", True, T0 - timedelta(hours=7))])

    out = lanes.tick(store, fake.deps, T0, 1)

    assert out.adopted == [] and fake.launches == [] and store.lane("conflicts-2") is None


def test_a_numbered_successors_spawn_retires_the_older_lane_instead_of_relaunching_it(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store, prs=(46001,))
    fake.feed.append(spawned(2, "conflicts-7", brief, at=T0 - timedelta(minutes=1)))
    fake.peers = [who("conflicts-2", False, T0 - timedelta(hours=7)), who("conflicts-7", True, T0)]

    out = lanes.tick(store, fake.deps, T0, 1)

    assert out.relaunched == [] and fake.launches == []
    assert (store.lane("conflicts-2").state, store.lane("conflicts-2").retired_by) == ("retired", "conflicts-7")


def test_a_handoff_writers_record_is_the_newest_handoff_of_the_lane_holding_the_work(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.feed += [record(2, "root", "stand-down", to=("conflicts-2",), attrs={"successor": "conflicts-3"}), record(3, "conflicts-2-handoff", "state", text="11:08 AM handoff conflicts-2: doc 7d102a9", to=("root",))]
    fake.peers = [who("conflicts-3", True, T0)]

    lanes.tick(store, fake.deps, T0, 1)

    assert store.lane("conflicts-3").handoff == "7d102a9"


def test_a_root_record_naming_a_replacement_retires_the_old_lane_and_hands_over_its_prs(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store, prs=(46738,))
    fake.feed += [spawned(2, "pod-volume-4", brief, at=T0), record(3, "root", "state", text="8:59 AM pod-volume-4 replaces conflicts-2 (handoff doc 96f1cc8). Same PRs.")]
    fake.peers = [who("conflicts-2", False, T0 - timedelta(hours=3)), who("pod-volume-4", True, T0)]

    out = lanes.tick(store, fake.deps, T0, 1)

    assert out.relaunched == [] and store.lane("conflicts-2").retired_by == "pod-volume-4"
    assert store.lane("pod-volume-4").prs == (46738,)


def test_root_prose_saying_something_replaces_a_lane_retires_nothing(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.feed.append(record(2, "root", "decide", text="12:30 PM the new hook replaces conflicts-2's poll loop"))
    fake.peers = [who("conflicts-2", True, T0)]

    lanes.tick(store, fake.deps, T0, 1)

    assert store.lane("conflicts-2").state == "desired"


def test_a_lane_whose_heads_take_every_pr_of_a_successor_that_never_launched_retires_it(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store, prs=(46612, 46615))
    fake.feed += [
        spawned(2, "n-enum11-restack", brief, at=T0),
        record(3, "root", "stand-down", to=("conflicts-2",), attrs={"successor": "conflicts-3"}),
        record(4, "n-enum11-restack", "head", refs={"prs": [46612]}),
        record(5, "n-enum11-restack", "head", refs={"prs": ["Forge-AI/monorepo#46615"]}),
    ]
    fake.peers = [who("n-enum11-restack", True, T0 + timedelta(minutes=20))]

    lanes.tick(store, fake.deps, T0 + timedelta(minutes=20), 1)

    assert fake.posts == [] and fake.launches == []
    assert (store.lane("conflicts-3").state, store.lane("conflicts-3").retired_by) == ("retired", "n-enum11-restack")
    assert store.lane("n-enum11-restack").prs == (46612, 46615)


def test_a_fresh_store_adopts_only_the_lanes_live_now(tmp_path, brief):
    store = Store.memory()
    fake = Fake(tmp_path, [spawned(1, "conflicts-2", brief), spawned(2, "placement-build-4", brief), spawned(3, "slack-read-1", brief, role="reader"), spawned(4, "x-handoff", brief)], [who("conflicts-2", True, T0), who("placement-build-4", False, T0 - timedelta(hours=3)), who("slack-read-1", True, T0), who("x-handoff", True, T0)])

    out = lanes.tick(store, fake.deps, T0, 1)

    assert out.adopted == ["conflicts-2"] and fake.launches == [] and fake.posts == []


def test_a_lane_spawned_after_adoption_is_watched_from_its_launch(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.feed.append(spawned(5, "fidelity-desk-14", brief, at=T0))
    fake.peers.append(who("fidelity-desk-14", True, T0))

    lanes.tick(store, fake.deps, T0, 1)

    assert store.lane("fidelity-desk-14").state == "desired"


def test_a_successor_that_never_comes_up_escalates_once(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.peers = [who("conflicts-2", False, T0)]
    lanes.tick(store, fake.deps, T0, 1)
    fake.posts.clear()

    for minutes in (15, 16):
        lanes.tick(store, fake.deps, T0 + timedelta(minutes=minutes), 1)

    assert fake.kinds() == [("blocker", "root")]
    assert store.lane("conflicts-3").state == "held"


def test_a_successor_that_dies_within_an_interval_twice_is_held(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    life = [("conflicts-2", False, 0, 0), ("conflicts-3", True, 1, 1), ("conflicts-3", False, 4, 5), ("conflicts-4", True, 6, 6), ("conflicts-4", False, 9, 10)]
    for lane, live, seen, at in life:
        fake.peers = [who(lane, live, T0 + timedelta(minutes=seen))]
        lanes.tick(store, fake.deps, T0 + timedelta(minutes=at), 1)

    assert [name for name, *_ in fake.launches] == ["conflicts-3", "conflicts-4"]
    assert store.lane("conflicts-4").state == "held"
    assert fake.kinds()[-1] == ("blocker", "root") and "died within 15 minutes of launch 2 times" in fake.posts[-1][-1]


def test_done_and_a_root_stand_down_retire_a_lane(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.feed += [spawned(2, "red-sweep-3", brief), record(3, "conflicts-2", "done", text="all five stacks restacked", to=("root",)), record(4, "root", "stand-down", to=("red-sweep-3",))]
    fake.peers = [who("conflicts-2", False, T0), who("red-sweep-3", False, T0)]

    lanes.tick(store, fake.deps, T0 + timedelta(minutes=30), 1)

    assert fake.launches == [] and fake.posts == []
    assert [(lane.name, lane.retired_by) for lane in store.all_lanes()] == [("conflicts-2", "done"), ("red-sweep-3", "root")]


def test_a_root_decide_moves_pr_ownership(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store, prs=(46001,))
    fake.feed += [spawned(2, "restack-children-2", brief), record(3, "root", "decide", text="#46001 owner=restack-children-2", refs={"prs": ["Forge-AI/monorepo#46001"]})]
    fake.peers.append(who("restack-children-2", True, T0))

    lanes.tick(store, fake.deps, T0, 1)

    assert store.lane("conflicts-2").prs == () and store.lane("restack-children-2").prs == (46001,)


def test_the_owner_hears_one_line_an_hour_and_only_when_a_lane_restarted(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)
    fake.peers = [who("conflicts-2", False, T0)]
    lanes.tick(store, fake.deps, T0 + timedelta(minutes=10), 1)
    fake.peers = [who("conflicts-3", True, T0 + timedelta(minutes=50))]
    lanes.tick(store, fake.deps, T0 + timedelta(minutes=50), 1)
    fake.posts.clear()
    fake.peers = [who("conflicts-3", True, T0 + timedelta(hours=2, minutes=1))]

    lanes.tick(store, fake.deps, T0 + timedelta(hours=1, minutes=1), 1)
    lanes.tick(store, fake.deps, T0 + timedelta(hours=1, minutes=2), 1)
    lanes.tick(store, fake.deps, T0 + timedelta(hours=2, minutes=1), 1)

    [line] = fake.posts
    assert line[line.index("--attr") + 1] == "audience=owner"
    assert line[-1] == "Between 10:00 AM and 11:00 AM the lane supervisor restarted 1 lane with no root action: conflicts-2 stopped at 10:00 AM, so conflicts-3 took over."


@pytest.mark.parametrize(
    ("state", "peer", "heard", "asked", "flushed", "tokens", "expected"),
    [
        ("desired", (True, 1), None, None, None, None, "ok"),
        ("desired", (True, 20), None, None, None, None, "dead"),
        ("desired", (True, 20), 2, None, None, None, "ok"),
        ("desired", (False, 1), None, None, None, None, "dead"),
        ("desired", None, None, None, None, None, "dead"),
        ("desired", (True, 1), None, None, None, 700_000, "ask-rotate"),
        ("desired", (True, 1), None, 5, None, None, "ok"),
        ("desired", (True, 1), None, 15, None, None, "rotating"),
        ("desired", (True, 1), None, 5, 2, None, "rotating"),
        ("launching", None, None, None, None, None, "ok"),
        ("launching", (True, 1), None, None, None, None, "came-up"),
    ],
)
def test_classify_lane(tmp_path, brief, state, peer, heard, asked, flushed, tokens, expected):
    store = Store.memory()
    store.want_lane("l-1", str(brief), "opus", "xhigh", "lane", state, T0 - timedelta(minutes=10))
    minutes = lambda n: T0 - timedelta(minutes=n) if n is not None else None
    if heard is not None:
        store.lane_heard("l-1", minutes(heard))
    store.lane_asked("l-1", minutes(asked))
    if flushed is not None:
        store.lane_handoff("l-1", "9a1b2c3", minutes(flushed))
    seen = Peer("l-1", peer[0], minutes(peer[1]), {}) if peer else None

    assert classify_lane(store.lane("l-1"), seen, T0, tokens, 600_000).kind == expected


def test_a_launching_lane_that_never_comes_up_is_a_no_show():
    store = Store.memory()
    store.want_lane("l-2", "/b.md", "opus", "xhigh", "lane", "launching", T0 - timedelta(minutes=15))

    assert classify_lane(store.lane("l-2"), None, T0).kind == "no-show"


@pytest.mark.parametrize(("name", "taken", "expected"), [("conflicts-2", set(), "conflicts-3"), ("conflicts-2", {"conflicts-3", "conflicts-4"}, "conflicts-5"), ("mq-health", set(), "mq-health-2"), ("tooling-misc-3-ccx-stack", set(), "tooling-misc-3-ccx-stack-2"), ("n-enum11-46068", set(), "n-enum11-46068-2")])
def test_successor_name(name, taken, expected):
    assert successor_name(name, taken) == expected


def test_the_registry_refuses_an_unknown_state():
    store = Store.memory()
    with pytest.raises(Exception, match="CHECK"):
        store.want_lane("l-3", "/b.md", "opus", "xhigh", "lane", "zombie", T0)


def test_a_tick_whose_liveness_source_does_not_answer_acts_on_nothing(tmp_path, brief):
    store = Store.memory()
    fake = adopted(tmp_path, brief, store)

    def unread(names):
        raise lanes.Unread("orca: connection refused")

    gone = lanes.Deps(**{**fake.deps.__dict__, "who": unread})
    out = lanes.tick(store, gone, T0 + timedelta(hours=1), 1)

    assert out == lanes.Outcome() and fake.posts == [] and store.lane("conflicts-2").state == "desired"
