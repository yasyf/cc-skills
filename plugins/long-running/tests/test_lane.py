import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from lane import facts, queue, rank, store, supervisor, verbs
from lane import owner as owners
from lane import say as saying

DRIVE = "lanes-test"
LIB = Path(__file__).resolve().parents[1] / "lib"


def pull(state: str = "OPEN", rollup: str | None = "PENDING", head: str = "abc", base: str = "main", branch: str = "feature") -> dict:
    return {"state": state, "merged": state == "MERGED", "head": head, "base": base, "branch": branch, "default": "main", "review": None, "rollup": rollup}


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
    monkeypatch.setattr(supervisor.lane_land, "spawn", lambda drive: None)
    folder = store.drive_dir(DRIVE)
    folder.mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps({"active": 3, "slots": 2, "posting": False}))
    return seen


def token_of(lane_id: str) -> str:
    return store.load(DRIVE, lane_id)["owner"]["token"]


def opened(lane_id: str, **extra) -> dict:
    return verbs.open_lane(DRIVE, lane_id, lane_id.title(), words=f"do {lane_id}", ts=extra.pop("ts", "100.0"), **extra)


def answered(lane_id: str) -> None:
    saying.say(DRIVE, lane_id, "on it", token=token_of(lane_id))


def ended(lane_id: str, on: list[str], until: str = "1h") -> None:
    answered(lane_id)
    verbs.end_turn(DRIVE, lane_id, token_of(lane_id), [verbs.parse_event(text) for text in on], until, "test wait")


def due(lane_id: str) -> None:
    store.update(DRIVE, lane_id, lambda lane: lane.__setitem__("wait", {**lane["wait"], "until": store.stamp(store.now() - timedelta(seconds=1))}))


def test_opening_a_lane_starts_its_owner_in_the_same_call(world: dict) -> None:
    lane = opened("run-page")
    assert lane["state"] == store.RUNNING
    assert lane["owner"]["session"].startswith("session-")
    assert world["launched"] == [("run-page", "opened")]
    assert store.token_path(DRIVE, "run-page").read_text().strip() == lane["owner"]["token"]


def test_a_lane_past_the_cap_must_name_the_lane_it_parks_with_his_words(world: dict) -> None:
    for lane_id in ("a", "b", "c"):
        opened(lane_id)
    with pytest.raises(store.Refused, match="name the lane this one parks"):
        opened("d")
    with pytest.raises(store.Refused, match="his words"):
        opened("d", parks="c")
    opened("d", parks="c", park_words="not now")
    assert store.load(DRIVE, "c")["state"] == store.PARKED
    assert store.load(DRIVE, "c")["parked"] == {"words": "not now", "link": None}
    assert store.load(DRIVE, "a")["state"] == store.RUNNING


def test_a_running_lane_he_parks_parks_at_its_turn_end(world: dict) -> None:
    opened("a")
    verbs.park(DRIVE, "a", "later")
    assert store.load(DRIVE, "a")["state"] == store.RUNNING
    ended("a", ["his_reply"])
    lane = store.load(DRIVE, "a")
    assert (lane["state"], lane["parked"]["words"], lane["owner"], lane["wait"]) == (store.PARKED, "later", None, None)


def test_the_writer_refuses_every_shape_outside_the_three_states(world: dict) -> None:
    opened("a")
    with pytest.raises(store.Refused, match="not running, waiting or parked"):
        store.update(DRIVE, "a", lambda lane: lane.__setitem__("state", "stalled"))
    with pytest.raises(store.Refused, match="running with no owner"):
        store.update(DRIVE, "a", lambda lane: lane.__setitem__("owner", None))
    answered("a")
    with pytest.raises(store.Refused, match="no outside fact"):
        store.update(DRIVE, "a", lambda lane: lane.update(state=store.WAITING, owner=None, wait={"on": [], "until": store.stamp()}))
    with pytest.raises(store.Refused, match="no time bound"):
        store.update(DRIVE, "a", lambda lane: lane.update(state=store.WAITING, owner=None, wait={"on": [{"kind": "his_reply"}]}))
    with pytest.raises(store.Refused, match="not an outside fact"):
        store.update(DRIVE, "a", lambda lane: lane.update(state=store.WAITING, owner=None, wait={"on": [{"kind": "session", "ref": "x"}], "until": store.stamp()}))
    with pytest.raises(store.Refused, match="parked without his words"):
        store.update(DRIVE, "a", lambda lane: lane.update(state=store.PARKED, owner=None, parked={"words": ""}))
    assert store.load(DRIVE, "a")["state"] == store.RUNNING


def test_end_turn_refuses_while_his_ask_is_unanswered(world: dict) -> None:
    opened("a")
    with pytest.raises(store.Refused, match="unanswered"):
        verbs.end_turn(DRIVE, "a", token_of("a"), [{"kind": "his_reply"}], "1h", "his reply")
    sent = saying.say(DRIVE, "a", "On it: the fix is #7, first ready by 3", next_step="first PR ready", by="2h", token=token_of("a"))
    lane = store.load(DRIVE, "a")
    assert [ask["replied"] for ask in lane["asks"]] == [sent]
    assert lane["promise"]["next"] == "first PR ready"


def test_end_turn_refuses_no_fact_no_bound_a_past_bound_and_a_held_fact(world: dict) -> None:
    opened("a")
    answered("a")
    token = token_of("a")
    with pytest.raises(store.Refused, match="at least one outside fact"):
        verbs.end_turn(DRIVE, "a", token, [], "1h", "nothing")
    with pytest.raises(store.Refused, match="has passed"):
        verbs.end_turn(DRIVE, "a", token, [{"kind": "his_reply"}], (store.now() - timedelta(minutes=1)).isoformat(), "late")
    with pytest.raises(store.Refused, match="longest wait"):
        verbs.end_turn(DRIVE, "a", token, [{"kind": "his_reply"}], "3d", "too long")
    with pytest.raises(store.Refused):
        verbs.parse_event("session:abc")
    world["prs"][("acme/app", 7)] = pull("MERGED")
    with pytest.raises(store.Refused, match="already true"):
        verbs.end_turn(DRIVE, "a", token, [verbs.parse_event("pr_merged:acme/app#7")], "1h", "merge")


def test_end_turn_writes_the_wait_and_frees_the_slot_in_one_write(world: dict) -> None:
    opened("a")
    job = store.load(DRIVE, "a")["owner"]["job"]
    ended("a", ["pr_merged:acme/app#7"])
    lane = store.load(DRIVE, "a")
    assert (lane["state"], lane["owner"]) == (store.WAITING, None)
    assert lane["wait"]["on"] == [{"kind": "pr_merged", "ref": "acme/app#7"}]
    assert not store.token_path(DRIVE, "a").exists()
    assert world["stopped"] == [job]


def test_a_replaced_owner_is_fenced_off(world: dict) -> None:
    opened("a")
    with pytest.raises(store.Refused, match="no longer yours"):
        verbs.end_turn(DRIVE, "a", "stale-token", [{"kind": "his_reply"}], "1h", "stale")
    with pytest.raises(store.Refused, match="no longer yours"):
        verbs.log(DRIVE, "a", "stale-token", "hello")


def test_a_time_bound_wakes_the_owner(world: dict) -> None:
    opened("a")
    ended("a", ["his_reply"])
    due("a")
    assert supervisor.run_pass(DRIVE)["started"] == ["a"]
    assert "time bound" in world["launched"][-1][1]


def test_a_merged_pr_wakes_the_owner(world: dict) -> None:
    opened("a")
    ended("a", ["pr_merged:acme/app#7"])
    world["prs"][("acme/app", 7)] = pull()
    assert supervisor.run_pass(DRIVE)["started"] == []
    world["prs"][("acme/app", 7)] = pull("MERGED")
    assert supervisor.run_pass(DRIVE)["started"] == ["a"]


def test_finished_checks_and_a_cut_result_wake_the_owner(world: dict) -> None:
    opened("a")
    opened("b")
    ended("a", ["check:acme/app#7"])
    ended("b", ["staging_result:cut-1"])
    world["prs"][("acme/app", 7)] = pull(rollup="FAILURE")
    cut = facts.side_path(DRIVE, "cuts", "cut-1")
    cut.parent.mkdir()
    cut.write_text(json.dumps({"result": "pass"}))
    assert sorted(supervisor.run_pass(DRIVE)["started"]) == ["a", "b"]


def test_his_message_appends_once_and_is_the_wake(world: dict) -> None:
    opened("a")
    ended("a", ["his_reply"])
    assert verbs.message(DRIVE, "a", "make it faster", ts="200.1") == 1
    assert verbs.message(DRIVE, "a", "make it faster", ts="200.1") == 0
    lane = store.load(DRIVE, "a")
    assert lane["state"] == store.RUNNING
    assert [ask["words"] for ask in store.unreplied(lane)] == ["make it faster"]


def test_his_message_read_from_the_channel_wakes_the_owner(world: dict) -> None:
    opened("a")
    ended("a", ["his_reply"], "2h")
    world["messages"]["a"] = [verbs.ask_row("status?", None, "300.2")]
    assert supervisor.run_pass(DRIVE)["started"] == ["a"]
    assert world["launched"][-1][1] == "his new words"


def test_his_word_in_a_parked_lane_unparks_it(world: dict) -> None:
    opened("a")
    ended("a", ["his_reply"])
    verbs.park(DRIVE, "a", "after the release")
    assert store.load(DRIVE, "a")["state"] == store.PARKED
    verbs.message(DRIVE, "a", "back to this now", ts="400.0")
    assert store.load(DRIVE, "a")["state"] == store.RUNNING


def test_his_ruling_lands_on_the_lane_and_wakes_its_owner(world: dict) -> None:
    opened("a")
    ended("a", ["his_reply"])
    verbs.ruling(DRIVE, "a", "old approvals stand when the head is unchanged", "https://slack/p1", "approvals")
    lane = store.load(DRIVE, "a")
    assert lane["rulings"][0]["binds"] == "approvals"
    assert lane["state"] == store.RUNNING
    verbs.ruling(DRIVE, None, "12 active lanes", None, "the cap")
    assert store.shared_rulings(DRIVE)[0]["words"] == "12 active lanes"


def test_a_lapsed_lease_starts_a_fresh_owner_in_the_same_pass(world: dict) -> None:
    opened("a")
    old = store.load(DRIVE, "a")["owner"]
    world["leases"][old["session"]] = "lapsed"
    assert supervisor.run_pass(DRIVE)["started"] == ["a"]
    lane = store.load(DRIVE, "a")
    assert lane["state"] == store.RUNNING
    assert lane["owner"]["token"] != old["token"]
    assert world["stopped"] == [old["job"]]


def test_repeated_lapses_back_off_to_a_bounded_wait_with_his_ask_still_open(world: dict) -> None:
    opened("a")
    for _ in range(3):
        world["leases"][store.load(DRIVE, "a")["owner"]["session"]] = "lapsed"
        supervisor.run_pass(DRIVE)
    lane = store.load(DRIVE, "a")
    assert lane["state"] == store.WAITING
    assert "lapsed" in lane["wait"]["why"]
    assert store.unreplied(lane)
    assert supervisor.run_pass(DRIVE)["started"] == []
    assert store.parse(lane["wait"]["until"]) > store.now()


def test_a_failed_start_waits_with_a_bound(world: dict) -> None:
    world["fail"].add("a")
    lane = opened("a")
    assert (lane["state"], lane["owner"]) == (store.WAITING, None)
    assert "start failed" in lane["wait"]["why"]


def test_slots_go_by_rank(world: dict) -> None:
    for lane_id in ("a", "b", "c"):
        opened(lane_id)
    assert store.load(DRIVE, "c")["state"] == store.WAITING
    for lane_id in ("a", "b"):
        ended(lane_id, ["his_reply"])
    for lane_id in ("a", "b", "c"):
        due(lane_id)
    world["scores"].update({"a": 0.5, "b": 3.5, "c": 2.0})
    assert sorted(supervisor.run_pass(DRIVE)["started"]) == ["b", "c"]
    assert store.load(DRIVE, "a")["state"] == store.WAITING
    ranked = json.loads((store.drive_dir(DRIVE) / "rank.json").read_text())
    assert ranked["order"][:2] == ["b", "c"]
    assert [ask["lane"] for ask in ranked["asks"]] == ["b", "c", "a"]


def test_a_failed_rank_keeps_the_last_order_and_never_gates_wakes(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    opened("a")
    ended("a", ["his_reply"])
    due("a")

    def down(text: str, told: list[dict]) -> dict:
        raise TimeoutError("no answer")

    monkeypatch.setattr(rank, "judge", down)
    result = supervisor.run_pass(DRIVE)
    assert result["started"] == ["a"]
    assert any("rank kept its last order" in error for error in result["errors"])


def test_a_promise_writes_the_lanes_time_bound(world: dict) -> None:
    opened("a")
    ended("a", ["his_reply"], "6h")
    with pytest.raises(store.Refused, match="--next and --by together"):
        saying.say(DRIVE, "a", "first ready soon", next_step="first ready")
    saying.say(DRIVE, "a", "First ready about 3 PM", next_step="first PR ready", by="2h")
    lane = store.load(DRIVE, "a")
    assert lane["promise"]["next"] == "first PR ready"
    assert store.parse(lane["wait"]["until"]) <= store.now() + timedelta(hours=2)


def test_a_dropped_post_unanswers_his_ask_and_wakes_the_owner(world: dict) -> None:
    opened("a")
    sent = saying.say(DRIVE, "a", "on it", token=token_of("a"))
    verbs.end_turn(DRIVE, "a", token_of("a"), [{"kind": "his_reply"}], "2h", "wait")
    saying.dropped(DRIVE, "a", sent, "named the wrong lane")
    assert store.unreplied(store.load(DRIVE, "a"))
    assert supervisor.run_pass(DRIVE)["started"] == ["a"]


def test_pr_state_is_viewed_in_memory_and_never_written(world: dict) -> None:
    opened("a", prs=[("acme/app", 7), ("acme/app", 8)])
    world["prs"][("acme/app", 7)] = pull(rollup="SUCCESS", branch="seven")
    world["prs"][("acme/app", 8)] = pull(base="seven", branch="eight")
    supervisor.run_pass(DRIVE)
    rows = {row["number"]: row for row in facts.view(DRIVE)["a"]["prs"]}
    assert (rows[7]["checks"], rows[7]["under"], rows[8]["under"]) == ("pass", None, "acme/app#7")
    kept = store.load(DRIVE, "a")["prs"]
    assert all(not {"head", "checks", "merged", "under"} & set(row) for row in kept)


def test_the_queue_grants_by_rank_and_an_incident_hurting_users_takes_staging(world: dict) -> None:
    for lane_id in ("a", "b", "c"):
        opened(lane_id)
    world["scores"].update({"a": 1.0, "b": 3.0, "c": 0.5})
    supervisor.run_pass(DRIVE)
    assert queue.take(DRIVE, "staging-env", "tester-a", "a", 30)["granted"] is True
    waiting = queue.take(DRIVE, "staging-env", "tester-b", "b", 30)
    assert (waiting["granted"], waiting["position"]) == (False, 1)
    verbs.set_incident(DRIVE, "c", None, True, None)
    taken = queue.take(DRIVE, "staging-env", "tester-c", "c", 30)
    assert taken["granted"] is True
    with pytest.raises(store.Refused, match="incident hurts users"):
        queue.renew(DRIVE, "staging-env", "tester-a", 30)
    queue.release(DRIVE, "staging-env", "tester-c")
    assert queue.take(DRIVE, "staging-env", "tester-b", "b", 30)["granted"] is True


def test_check_reads_each_lane_as_running_waiting_with_a_wake_or_parked(world: dict) -> None:
    opened("a")
    opened("b")
    ended("b", ["pr_merged:acme/app#7"])
    supervisor.run_pass(DRIVE)
    good, lines = supervisor.check(DRIVE)
    assert good, lines
    assert any(line.startswith("a: running") for line in lines)
    assert any(line.startswith("b: waiting on pr_merged acme/app#7") for line in lines)


def test_progress_goes_to_the_log_file_never_the_lane_file(world: dict) -> None:
    opened("a")
    verbs.log(DRIVE, "a", token_of("a"), "read the stack")
    assert "read the stack" in store.log_path(DRIVE, "a").read_text()
    assert "log" not in store.load(DRIVE, "a")


def test_the_lane_model_imports_nothing_of_the_old_drive() -> None:
    probe = "import sys, lane.command, lane.supervisor, lane.say, lane.queue; print(sorted(name for name in sys.modules if name == 'drive' or name.startswith('drive.')))"
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True, env={"PYTHONPATH": str(LIB), "PATH": "/usr/bin:/bin"})
    assert done.stdout.strip() == "[]"
