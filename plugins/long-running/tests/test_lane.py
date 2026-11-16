import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from lane import facts, rank, store, supervisor, verbs
from lane import owner as owners
from lane import say as saying

DRIVE = "lanes-test"
LIB = Path(__file__).resolve().parents[1] / "lib"


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Owners that start as fake background jobs, leases the test sets, pull requests the test sets, and a ranking by the scores the test sets."""
    seen: dict = {"launched": [], "stopped": [], "leases": {}, "prs": {}, "scores": {}, "messages": {}, "fail": set()}

    def launch(drive: str, lane: dict, token: str, woke: str) -> dict:
        if lane["id"] in seen["fail"]:
            raise store.Refused("claude --bg exited 1")
        seen["launched"].append((lane["id"], woke))
        return {"job": f"job-{token}", "session": f"session-{token}", "launched": store.stamp()}

    monkeypatch.setattr(owners, "launch", launch)
    monkeypatch.setattr(owners, "stop", lambda owner: seen["stopped"].append(owner.get("job")))
    monkeypatch.setattr(owners, "located", lambda owner: owner)
    monkeypatch.setattr(owners, "lease", lambda owner, moment, grace: seen["leases"].get(owner.get("session"), "held"))
    monkeypatch.setattr(facts, "read_prs", lambda keys: {key: seen["prs"][key] for key in keys if key in seen["prs"]})
    monkeypatch.setattr(facts, "his_messages", lambda lane: seen["messages"].pop(lane["id"], []))
    monkeypatch.setattr(rank, "judge", lambda text, told: dict(seen["scores"]))
    folder = store.drive_dir(DRIVE)
    folder.mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps({"active": 3, "slots": 2}))
    return seen


def token_of(lane_id: str) -> str:
    return store.load(DRIVE, lane_id)["owner"]["token"]


def opened(lane_id: str, **extra) -> dict:
    return verbs.open_lane(DRIVE, lane_id, lane_id.title(), words=f"do {lane_id}", **extra)


def test_opening_a_lane_starts_its_owner_in_the_same_call(world: dict) -> None:
    lane = opened("run-page")
    assert lane["state"] == store.RUNNING
    assert lane["owner"]["session"].startswith("session-")
    assert world["launched"] == [("run-page", "opened")]
    assert store.token_path(DRIVE, "run-page").read_text().strip() == lane["owner"]["token"]


def test_a_lane_past_the_cap_must_name_the_lane_it_parks(world: dict) -> None:
    for lane_id in ("a", "b", "c"):
        opened(lane_id)
    with pytest.raises(store.Refused, match="name the lane this one parks"):
        opened("d")
    opened("d", parks="a", park_words="not now")
    assert store.load(DRIVE, "a")["parked"] == "not now"
    assert len(store.active(store.load_all(DRIVE))) == 3


def test_end_turn_refuses_a_wait_with_no_time_bound_or_a_past_one(world: dict) -> None:
    opened("a")
    token = token_of("a")
    with pytest.raises(store.Refused, match="has passed"):
        verbs.end_turn(DRIVE, "a", token, [{"kind": "reply"}], (store.now() - timedelta(minutes=1)).isoformat(), "late")
    with pytest.raises(store.Refused, match="longest wait"):
        verbs.end_turn(DRIVE, "a", token, [], "3d", "too long")
    with pytest.raises(store.Refused):
        verbs.parse_event("someday")


def test_end_turn_writes_the_wait_and_frees_the_slot_in_one_write(world: dict) -> None:
    opened("a")
    verbs.asks(DRIVE, "a", token_of("a"))
    job = store.load(DRIVE, "a")["owner"]["job"]
    lane = verbs.end_turn(DRIVE, "a", token_of("a"), [verbs.parse_event("pr:acme/app#7:merged")], "1h", "waits on the merge")
    assert lane["state"] == store.WAITING
    assert lane["owner"] is None
    assert lane["wait"]["on"] == [{"kind": "pr", "repo": "acme/app", "number": 7, "fact": "merged"}]
    assert not store.token_path(DRIVE, "a").exists()
    assert world["stopped"] == [job]


def test_end_turn_refuses_a_wait_that_already_holds_and_unread_words(world: dict) -> None:
    opened("a")
    token = token_of("a")
    with pytest.raises(store.Refused, match="already true"):
        verbs.end_turn(DRIVE, "a", token, [{"kind": "reply"}], "1h", "his reply")
    verbs.asks(DRIVE, "a", token)
    world["prs"][("acme/app", 7)] = {"state": "MERGED", "merged": True, "head": "abc", "review": None, "rollup": "SUCCESS"}
    with pytest.raises(store.Refused, match="already true"):
        verbs.end_turn(DRIVE, "a", token, [verbs.parse_event("pr:acme/app#7:merged")], "1h", "merge")


def test_a_replaced_owner_is_fenced_off(world: dict) -> None:
    opened("a")
    with pytest.raises(store.Refused, match="no longer yours"):
        verbs.end_turn(DRIVE, "a", "stale-token", [], "1h", "stale")
    with pytest.raises(store.Refused, match="no longer yours"):
        verbs.log(DRIVE, "a", "stale-token", "hello")


def test_no_write_leaves_a_third_state_or_a_running_lane_without_owner(world: dict) -> None:
    opened("a")
    with pytest.raises(store.Refused, match="neither running nor waiting"):
        store.update(DRIVE, "a", lambda lane: lane.__setitem__("state", "stalled"))
    with pytest.raises(store.Refused, match="running with no owner"):
        store.update(DRIVE, "a", lambda lane: lane.__setitem__("owner", None))
    assert store.load(DRIVE, "a")["state"] == store.RUNNING


def ended(lane_id: str, on: list[str], until: str = "1h") -> None:
    verbs.asks(DRIVE, lane_id, token_of(lane_id))
    verbs.end_turn(DRIVE, lane_id, token_of(lane_id), [verbs.parse_event(text) for text in on], until, "test wait")


def test_a_time_bound_wakes_the_owner(world: dict) -> None:
    opened("a")
    ended("a", [], "1h")
    store.update(DRIVE, "a", lambda lane: lane["wait"].__setitem__("until", store.stamp(store.now() - timedelta(seconds=1))))
    assert supervisor.run_pass(DRIVE)["started"] == ["a"]
    assert "time bound" in world["launched"][-1][1]


def test_a_merged_pr_wakes_the_owner(world: dict) -> None:
    opened("a")
    ended("a", ["pr:acme/app#7:merged"])
    assert supervisor.run_pass(DRIVE)["started"] == []
    world["prs"][("acme/app", 7)] = {"state": "MERGED", "merged": True, "head": "abc", "review": None, "rollup": "SUCCESS"}
    assert supervisor.run_pass(DRIVE)["started"] == ["a"]


def test_his_message_appends_once_and_is_the_wake(world: dict) -> None:
    opened("a")
    ended("a", ["reply"])
    assert verbs.message(DRIVE, "a", "make it faster", ts="100.1") == 1
    assert verbs.message(DRIVE, "a", "make it faster", ts="100.1") == 0
    lane = store.load(DRIVE, "a")
    assert lane["state"] == store.RUNNING
    assert [ask["words"] for ask in lane["asks"] if not ask["seen"]] == ["make it faster"]


def test_his_message_read_from_the_channel_wakes_the_owner(world: dict) -> None:
    opened("a")
    ended("a", [], "2h")
    world["messages"]["a"] = [{"at": store.stamp(), "words": "status?", "link": None, "ts": "200.2", "seen": False}]
    assert supervisor.run_pass(DRIVE)["started"] == ["a"]
    assert world["launched"][-1][1] == "his new words"


def test_a_lapsed_lease_starts_a_fresh_owner_in_the_same_pass(world: dict) -> None:
    opened("a")
    old = store.load(DRIVE, "a")["owner"]
    world["leases"][old["session"]] = "lapsed"
    assert supervisor.run_pass(DRIVE)["started"] == ["a"]
    lane = store.load(DRIVE, "a")
    assert lane["state"] == store.RUNNING
    assert lane["owner"]["token"] != old["token"]
    assert world["stopped"] == [old["job"]]


def test_repeated_lapses_back_off_to_a_time_wait(world: dict) -> None:
    opened("a")
    for _ in range(3):
        world["leases"][store.load(DRIVE, "a")["owner"]["session"]] = "lapsed"
        supervisor.run_pass(DRIVE)
    lane = store.load(DRIVE, "a")
    assert lane["state"] == store.WAITING
    assert "lapsed" in lane["wait"]["why"]
    assert store.parse(lane["wait"]["until"]) > store.now()


def test_a_failed_start_waits_with_a_bound(world: dict) -> None:
    world["fail"].add("a")
    lane = opened("a")
    assert lane["state"] == store.WAITING
    assert lane["owner"] is None
    assert "start failed" in lane["wait"]["why"]
    assert lane["wait"]["until"]


def test_slots_go_by_rank(world: dict) -> None:
    for lane_id in ("a", "b", "c"):
        opened(lane_id)
    assert store.load(DRIVE, "c")["state"] == store.WAITING
    for lane_id in ("a", "b"):
        ended(lane_id, [], "1h")
    for lane_id in ("a", "b", "c"):
        store.update(DRIVE, lane_id, lambda lane: lane.__setitem__("wait", {**lane["wait"], "until": store.stamp(store.now() - timedelta(seconds=1))}))
    world["scores"].update({"a": 0.5, "b": 3.5, "c": 2.0})
    started = supervisor.run_pass(DRIVE)["started"]
    assert sorted(started) == ["b", "c"]
    assert store.load(DRIVE, "a")["state"] == store.WAITING
    assert json.loads((store.drive_dir(DRIVE) / "rank.json").read_text())["order"][:2] == ["b", "c"]


def test_a_failed_rank_keeps_the_last_order_and_never_gates_wakes(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    opened("a")
    ended("a", [], "1h")
    store.update(DRIVE, "a", lambda lane: lane.__setitem__("wait", {**lane["wait"], "until": store.stamp(store.now() - timedelta(seconds=1))}))

    def down(text: str, told: list[dict]) -> dict:
        raise TimeoutError("no answer")

    monkeypatch.setattr(rank, "judge", down)
    result = supervisor.run_pass(DRIVE)
    assert result["started"] == ["a"]
    assert any("rank kept its last order" in error for error in result["errors"])


def test_a_promise_writes_the_lanes_time_bound(world: dict) -> None:
    opened("a")
    ended("a", ["reply"], "6h")
    with pytest.raises(store.Refused, match="--next and --by together"):
        saying.say(DRIVE, "a", "first ready soon", next_step="first ready")
    saying.say(DRIVE, "a", "First ready about 3 PM", next_step="first PR ready", by="2h")
    lane = store.load(DRIVE, "a")
    assert lane["promise"]["next"] == "first PR ready"
    assert store.parse(lane["wait"]["until"]) <= store.now() + timedelta(hours=2)


def test_check_reads_each_lane_as_running_or_waiting_with_a_wake(world: dict) -> None:
    opened("a")
    opened("b")
    ended("b", ["pr:acme/app#7:merged"])
    supervisor.run_pass(DRIVE)
    good, lines = supervisor.check(DRIVE)
    assert good, lines
    assert any(line.startswith("a: running") for line in lines)
    assert any(line.startswith("b: waiting on acme/app#7 merged") for line in lines)


def test_the_lane_model_imports_nothing_of_the_old_drive() -> None:
    probe = "import sys, lane.command, lane.supervisor, lane.say; print(sorted(name for name in sys.modules if name == 'drive' or name.startswith('drive.')))"
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True, env={"PYTHONPATH": str(LIB), "PATH": "/usr/bin:/bin"})
    assert done.stdout.strip() == "[]"
