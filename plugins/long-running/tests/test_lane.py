import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from lane import facts, queue, rank, stager, store, supervisor, verbs, voice
from lane import owner as owners
from lane import say as saying

DRIVE = "lanes-test"
LIB = Path(__file__).resolve().parents[1] / "lib"


def pull(state: str = "OPEN", rollup: str | None = "PENDING", head: str = "abc", base: str = "main", branch: str = "feature") -> dict:
    return {"state": state, "merged": state == "MERGED", "head": head, "base": base, "branch": branch, "default": "main", "review": None, "rollup": rollup, "title": ""}


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
    monkeypatch.setattr(facts, "read_prs", lambda keys, commits=facts.COMMITS_READ, failed=None: {key: seen["prs"][key] for key in keys if key in seen["prs"]})
    monkeypatch.setattr(facts, "his_messages", lambda lane: seen["messages"].pop(lane["id"], []))
    monkeypatch.setattr(facts, "his_login", lambda drive: "him")
    monkeypatch.setattr(voice, "followed", lambda drive, moment: [])
    monkeypatch.setattr(rank, "judge", lambda text, told: dict(seen["scores"]))
    monkeypatch.setattr(supervisor.lane_land, "spawn", lambda drive: None)
    from lane import ack

    monkeypatch.setattr(ack, "react", lambda lane, ask, add, remove: seen.setdefault("slack", []).append((ask["ts"], add, remove)))
    from lane import work

    monkeypatch.setattr(work, "launch", lambda drive, cwd: seen.setdefault("workers", []).append(str(cwd)) or {"id": f"worker-{len(seen['workers'])}", "job": f"worker-job-{len(seen['workers'])}"})
    folder = store.drive_dir(DRIVE)
    folder.mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps({"active": 3, "slots": 2, "posting": False}))
    supervisor.heartbeat(DRIVE, store.now(), [])
    return seen


def token_of(lane_id: str) -> str:
    return store.load(DRIVE, lane_id)["owner"]["token"]


def opened(lane_id: str, **extra) -> dict:
    return verbs.open_lane(DRIVE, lane_id, lane_id.title(), words=f"do {lane_id}", ts=extra.pop("ts", "100.0"), worktree="/checkout", **extra)


def answered(lane_id: str) -> None:
    saying.say(DRIVE, lane_id, "on it", token=token_of(lane_id))


def ended(lane_id: str, on: list[str], until: str = "1h") -> None:
    answered(lane_id)
    verbs.end_turn(DRIVE, lane_id, token_of(lane_id), [verbs.parse_event(text) for text in on], until, "test wait")


def asked(lane_id: str, cut: str) -> Path:
    path = stager.lanes_dir(DRIVE) / stager.WANTS / f"{lane_id}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"lane": lane_id, "cut": cut, "at": store.stamp()}))
    return path



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
    asked("b", "cut-1")
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


HIS_229 = "Better answer: to catch things that a unit test won't catch but a staging run would. Let's narrow it to only the most dangerous areas (eg ssql handoff, run journal schema, etc) and make it a suggestion not a requirement "
LINK_229 = "https://poetic-technology.slack.com/archives/C0BQSADC7SS/p1794868148017949"
PARAPHRASE_252 = "Only migrations, backfills, deletes of user data, SandSQL storage and consistency, and wire or storage formats need a staging test before merge. Dangerous merges still happen only while he is present."


def test_his_ruling_lands_on_the_lane_and_wakes_its_owner(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(facts, "said_at", lambda link: "old approvals stand when the head is unchanged")
    opened("a")
    ended("a", ["his_reply"])
    verbs.ruling(DRIVE, "a", "old approvals stand when the head is unchanged", "https://slack/p1", "approvals")
    lane = store.load(DRIVE, "a")
    assert lane["rulings"][0]["binds"] == "approvals"
    assert lane["state"] == store.RUNNING
    verbs.ruling(DRIVE, None, "12 active lanes", None, "the cap", relayed_by="team-lead")
    assert store.shared_rulings(DRIVE)[0] | {"at": None} == {"words": "12 active lanes", "link": None, "at": None, "binds": "the cap", "relayed_by": "team-lead"}


def test_replaying_his_229_pm_ruling_stores_his_words_refuses_the_252_paraphrase_and_has_a_model_judge_each_wait(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(facts, "said_at", lambda link: HIS_229)
    from lane import session

    judged = []
    monkeypatch.setattr(session, "argv", lambda config, role, checkout, own, head, tail: ["claude", *tail])
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kwargs: judged.append(argv[-1]))
    opened("a")
    opened("b")
    supervisor.run_pass(DRIVE)
    asked("a", "cut-1")
    ended("a", ["staging_result:cut-1"], "2h")
    ended("b", ["his_reply"], "2h")
    with pytest.raises(store.Refused, match="not his words"):
        verbs.ruling(DRIVE, None, PARAPHRASE_252, LINK_229, "pre-merge staging")
    with pytest.raises(store.Refused, match="--relayed-by"):
        verbs.ruling(DRIVE, None, PARAPHRASE_252, None, "pre-merge staging")
    verbs.ruling(DRIVE, None, None, LINK_229, "pre-merge staging", reading="staging before merge is a suggestion, for SandSQL handoff and the run journal schema")
    [kept] = store.shared_rulings(DRIVE)
    assert "a suggestion not a requirement" in kept["words"] and "while he is present" not in kept["words"] and kept["reading"].startswith("staging before merge")
    assert [store.load(DRIVE, lane_id)["state"] for lane_id in ("a", "b")] == [store.WAITING, store.WAITING]
    assert len(judged) == 1 and '"a"' in judged[0] and '"b"' in judged[0] and "Wake no other lane" in judged[0]
    assert "A reading, not his words: staging before merge" in owners.brief(DRIVE, store.load(DRIVE, "a"), "test")


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


def test_the_queue_grants_by_rank_and_no_incident_takes_a_grant_back(world: dict) -> None:
    for lane_id in ("a", "b", "c"):
        opened(lane_id)
    world["scores"].update({"a": 1.0, "b": 3.0, "c": 0.5})
    supervisor.run_pass(DRIVE)
    assert queue.take(DRIVE, "release", "releaser-a", "a", 30)["granted"] is True
    waiting = queue.take(DRIVE, "release", "releaser-b", "b", 30)
    assert (waiting["granted"], waiting["position"]) == (False, 1)
    verbs.set_incident(DRIVE, "c", None, link="https://alert")
    supervisor.run_pass(DRIVE)
    assert queue.take(DRIVE, "release", "releaser-c", "c", 30)["granted"] is False
    assert queue.renew(DRIVE, "release", "releaser-a", 30)
    with pytest.raises(store.Refused, match="grants release, not 'staging-env'"):
        queue.take(DRIVE, "staging-env", "tester-a", "a", 30)


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


def test_a_start_while_the_lease_is_held_is_refused_and_no_machine_is_stopped(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    opened("a")
    owner = store.load(DRIVE, "a")["owner"]
    reads = iter(["lapsed", "held"])
    monkeypatch.setattr(owners, "lease", lambda owner, moment, grace: next(reads, "held"))
    world["launched"].clear()
    assert supervisor.run_pass(DRIVE)["started"] == []
    assert store.load(DRIVE, "a")["owner"]["token"] == owner["token"]
    assert (world["launched"], world["stopped"]) == ([], [])


def test_a_wake_resumes_a_short_fresh_session_and_starts_fresh_otherwise(world: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(owners, "config_dir", lambda: tmp_path / "claude")
    opened("a")
    session = store.load(DRIVE, "a")["owner"]["session"]
    ended("a", ["his_reply"])
    lane = store.load(DRIVE, "a")
    kept = tmp_path / "claude" / "projects" / "-checkout" / f"{session}.jsonl"
    kept.parent.mkdir(parents=True)
    kept.write_text("x" * 1000)
    assert owners.resumable(lane, store.now()) == session
    assert owners.resumable(lane, store.now() + timedelta(minutes=31)) is None
    kept.write_text("x" * owners.RESUME_BYTES)
    assert owners.resumable(lane, store.now()) is None


def test_adopt_writes_a_first_shape_lane_in_the_agreed_one(world: dict, tmp_path: Path) -> None:
    old = {
        "id": "carried",
        "title": "Carried",
        "created": store.stamp(),
        "updated": store.stamp(),
        "state": "waiting",
        "parked": None,
        "channel": {"id": "C1", "thread": "1.2"},
        "asks": [{"at": store.stamp(), "words": "old words", "link": None, "ts": "1.3", "seen": True, "replied": "carried at the rebuild", "from_orders": ["o1"]}],
        "prs": [{"repo": "acme/app", "number": 7, "head": "abc", "approved_head": "abc", "held": None, "dangerous": None}],
        "rulings": [{"words": "ship it", "link": None, "at": store.stamp(), "binds": "release", "answer_id": "a1"}],
        "also": ["a second title"],
        "next": "land it",
        "wait": {"on": [{"kind": "reply"}], "until": store.stamp(), "why": "rebuilt", "set": store.stamp()},
        "owner": None,
        "log": [{"at": store.stamp(), "text": "rebuilt"}],
    }
    source = tmp_path / "carried.json"
    source.write_text(json.dumps(old))
    verbs.adopt(DRIVE, [str(source)])
    lane = store.load(DRIVE, "carried")
    assert (lane["channel"], lane["thread"], lane["wait"]["on"]) == ("C1", "1.2", [{"kind": "his_reply"}])
    assert not store.unreplied(lane)
    assert (lane["asks"][0]["from_orders"], lane["also"], lane["rulings"][0]["answer_id"]) == (["o1"], ["a second title"], "a1")
    assert "head" not in lane["prs"][0]
    assert "rebuilt" in store.log_path(DRIVE, "carried").read_text()


def test_an_incident_hurts_users_by_its_facts_until_its_fix_is_live(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    opened("a", incident={"link": "https://alert", "monitor": "42", "fix_live": None})
    states = {"42": "Alert"}
    monkeypatch.setattr(facts, "monitor_states", lambda drive, ids: {key: states[key] for key in ids})
    supervisor.run_pass(DRIVE)
    assert rank.last(DRIVE)["hurting"] == ["a"]
    states["42"] = "OK"
    supervisor.run_pass(DRIVE)
    assert rank.last(DRIVE)["hurting"] == []

    def down(drive: str, ids: set[str]) -> dict:
        raise store.Refused("Datadog down")

    monkeypatch.setattr(facts, "monitor_states", down)
    result = supervisor.run_pass(DRIVE)
    assert rank.last(DRIVE)["hurting"] == []
    assert any("last monitor states read stand" in error for error in result["errors"])
    monkeypatch.setattr(facts, "monitor_states", lambda drive, ids: {key: states[key] for key in ids})
    states["42"] = "Alert"
    verbs.set_incident(DRIVE, "a", token_of("a"), fix_live="https://release/live")
    supervisor.run_pass(DRIVE)
    assert rank.last(DRIVE)["hurting"] == []


def test_a_handover_note_reaches_the_owner_brief_and_is_no_ask(world: dict) -> None:
    opened("a")
    verbs.handover(DRIVE, "a", "old job j1 runs on branch b in /w with #7", "drive-redesign")
    lane = store.load(DRIVE, "a")
    assert "old job j1 runs on branch b" in owners.brief(DRIVE, lane, "woken")
    assert len(lane["asks"]) == 1


def test_every_owner_brief_names_the_drive_configs_staging_targets_as_the_one_fact_its_posts_use(world: dict) -> None:
    opened("a")
    (store.drive_dir(DRIVE) / "config.json").write_text(json.dumps(store.config(DRIVE) | {"staging_targets": ["plat-usw2-staging sandsql-nodes-3"]}))
    assert "Staging's targets are exactly plat-usw2-staging sandsql-nodes-3, from the drive config" in owners.brief(DRIVE, store.load(DRIVE, "a"), "woken")


def test_a_verb_on_a_drive_no_supervisor_runs_takes_his_words_and_starts_nothing(world: dict) -> None:
    opened("a")
    ended("a", ["his_reply"])
    (store.drive_dir(DRIVE) / "supervisor.json").unlink()
    world["launched"].clear()
    assert verbs.message(DRIVE, "a", "status?", ts="500.0") == 1
    assert (store.load(DRIVE, "a")["state"], world["launched"]) == (store.WAITING, [])


def test_a_ruling_or_ask_missing_what_the_brief_prints_is_refused(world: dict, tmp_path: Path) -> None:
    opened("a")
    with pytest.raises(store.Refused, match="a ruling 0 with no at"):
        store.update(DRIVE, "a", lambda lane: lane.__setitem__("rulings", [{"words": "ship it", "binds": "release"}]))
    shared = tmp_path / store.RULINGS
    shared.write_text(json.dumps({"shared": [{"words": "12 lanes", "at": store.stamp()}]}))
    with pytest.raises(store.Refused, match="no binds"):
        verbs.adopt(DRIVE, [str(shared)])


def test_adopt_rulings_only_keeps_the_rest_of_the_lane(world: dict, tmp_path: Path) -> None:
    opened("a")
    verbs.set_worktree(DRIVE, "a", "/kept")
    source = tmp_path / "a.json"
    source.write_text(json.dumps({**store.load(DRIVE, "a"), "worktree": None, "rulings": [{"words": "ship it", "at": store.stamp(), "binds": "release"}]}))
    verbs.adopt(DRIVE, [str(source)], rulings_only=True)
    lane = store.load(DRIVE, "a")
    assert (lane["worktree"], lane["rulings"][0]["words"], lane["state"]) == ("/kept", "ship it", store.RUNNING)


def test_adopt_never_replaces_a_lane_already_on_the_drive(world: dict, tmp_path: Path) -> None:
    opened("a")
    source = tmp_path / "a.json"
    source.write_text(json.dumps({**store.load(DRIVE, "a"), "owner": None, "state": "waiting"}))
    with pytest.raises(store.Refused, match="adds new lanes only"):
        verbs.adopt(DRIVE, [str(source)])
    assert store.load(DRIVE, "a")["state"] == store.RUNNING


def test_leaving_running_on_a_past_bound_is_refused(world: dict) -> None:
    opened("a")
    answered("a")
    with pytest.raises(store.Refused, match="has passed"):
        store.update(DRIVE, "a", lambda lane: lane.update(state=store.WAITING, owner=None, wait={"on": [{"kind": "his_reply"}], "until": "2026-01-01T00:00:00Z"}))


def test_a_handover_set_while_the_owner_runs_reaches_it_once(world: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from lane import command

    opened("a")
    verbs.handover(DRIVE, "a", "team-lead's lane builds the parity view; do not", "team-lead")
    monkeypatch.setenv("LANE_OWNER", f"{DRIVE}/a")
    monkeypatch.setenv("LANE_TOKEN", token_of("a"))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("{}"))
    assert command.note_hook(DRIVE, "a") == 0
    assert "parity view" in json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert not store.note_path(DRIVE, "a").exists()


def test_handover_entries_append_signed_and_clear_together(world: dict) -> None:
    opened("a")
    with pytest.raises(store.Refused, match="names its writer"):
        verbs.handover(DRIVE, "a", "no writer")
    verbs.handover(DRIVE, "a", "PR list: #7, #8", "drive-redesign")
    verbs.handover(DRIVE, "a", "old drives are stopped", "team-lead")
    kept = store.load(DRIVE, "a")["handover"]
    assert "#7, #8" in kept and "old drives are stopped" in kept and "by drive-redesign" in kept and "by team-lead" in kept
    verbs.handover(DRIVE, "a", None)
    assert store.load(DRIVE, "a")["handover"] is None


def test_an_owner_with_live_builders_stays_running(world: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from lane import command

    opened("a")
    answered("a")
    monkeypatch.setattr(owners, "in_flight", lambda owner: 2)
    with pytest.raises(store.Refused, match="still run"):
        verbs.end_turn(DRIVE, "a", token_of("a"), [{"kind": "his_reply"}], "1h", "builders run")
    monkeypatch.setenv("LANE_TOKEN", token_of("a"))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("{}"))
    assert command.stop_hook(DRIVE, "a") == 0
    assert capsys.readouterr().out == ""
    assert store.load(DRIVE, "a")["state"] == store.RUNNING


def test_wake_fires_a_waiting_lane_now(world: dict) -> None:
    opened("a")
    ended("a", ["his_reply"], "6h")
    verbs.wake(DRIVE, "a", "its builders died with its turn")
    assert store.load(DRIVE, "a")["state"] == store.RUNNING


def test_a_freed_grant_wakes_the_next_waiting_lane_in_line(world: dict) -> None:
    opened("a")
    opened("b")
    supervisor.run_pass(DRIVE)
    assert queue.take(DRIVE, "release", "tester-a", "a", 30)["granted"] is True
    assert queue.take(DRIVE, "release", "tester-b", "b", 30)["granted"] is False
    ended("b", ["queue_grant:release"], "2h")
    queue.release(DRIVE, "release", "tester-a")
    assert store.load(DRIVE, "b")["wait"]["fired_why"].startswith("the queue freed release")


def test_a_queue_grant_wait_fires_when_the_lane_is_first_in_line_for_a_free_unit(world: dict) -> None:
    opened("a")
    opened("b")
    supervisor.run_pass(DRIVE)
    queue.take(DRIVE, "release", "tester-a", "a", 30)
    queue.take(DRIVE, "release", "tester-b", "b", 30)
    ended("b", ["queue_grant:release"], "2h")
    assert supervisor.run_pass(DRIVE)["started"] == []
    queue.release(DRIVE, "release", "tester-a")
    assert facts.holds(DRIVE, store.load(DRIVE, "b"), {"kind": "queue_grant", "ref": "release"}, {})


def test_his_page_shows_each_lane_its_wait_his_unanswered_asks_and_untapped_prs(world: dict) -> None:
    from lane import page

    opened("a", prs=[("acme/app", 7), ("acme/app", 8)])
    opened("b")
    ended("b", ["pr_merged:acme/app#9"])
    world["prs"][("acme/app", 7)] = pull(head="new")
    world["prs"][("acme/app", 8)] = pull(head="h8")
    store.update(DRIVE, "a", lambda lane: lane["prs"][1].__setitem__("approved_head", "h8"))
    supervisor.run_pass(DRIVE)
    shown = {lane["id"]: lane for lane in page.model(DRIVE)["lanes"]}
    assert [row["number"] for row in shown["a"]["tapless"]] == [7]
    assert shown["a"]["unanswered"][0]["words"] == "do a"
    assert shown["b"]["wait"]["on"] == [{"kind": "pr_merged", "ref": "acme/app#9"}]
    assert "do a" in page.drawn(DRIVE) and "waiting on pr_merged acme/app#9" in page.drawn(DRIVE)


def test_a_lane_in_line_must_wait_on_its_grant(world: dict) -> None:
    opened("a")
    opened("b")
    supervisor.run_pass(DRIVE)
    queue.take(DRIVE, "release", "tester-a", "a", 30)
    queue.take(DRIVE, "release", "tester-b", "b", 30)
    with pytest.raises(store.Refused, match="stands in line for release: wait on queue_grant:release"):
        ended("b", ["his_reply"], "2h")
    ended("a", ["his_reply"], "2h")
    ended("b", ["queue_grant:release", "his_reply"], "2h")


def test_a_lane_in_line_wakes_when_its_grant_frees_whatever_its_wait_names(world: dict) -> None:
    opened("a")
    opened("b")
    supervisor.run_pass(DRIVE)
    queue.take(DRIVE, "release", "tester-a", "a", 30)
    queue.take(DRIVE, "release", "tester-b", "b", 30)
    ended("b", ["queue_grant:release"], "2h")
    store.update(DRIVE, "b", lambda lane: lane["wait"].__setitem__("on", [{"kind": "his_reply"}]))
    found = queue.kept(DRIVE)
    found["leases"]["release"] = []
    queue.written(DRIVE, found)
    assert supervisor.fired(DRIVE, store.load(DRIVE, "b"), {}, store.now()) == "the queue grants release to it now"


def test_his_tap_list_holds_unapproved_and_revoked_prs_never_a_moved_head_still_being_judged(world: dict) -> None:
    from lane import page

    (store.drive_dir(DRIVE) / store.LIVE).touch()
    opened("a", prs=[("acme/app", 7), ("acme/app", 8), ("acme/app", 9)], channel="C1")
    world["prs"][("acme/app", 7)] = pull(head="h7b") | {"commits": [("h7b", "lanes-bot")], "commit_count": 1}
    world["prs"][("acme/app", 8)] = pull(head="h8b") | {"commits": [("h8b", "lanes-bot")], "commit_count": 1}
    world["prs"][("acme/app", 9)] = pull(head="h9")

    def approving(lane: dict) -> None:
        lane["prs"][0]["approved_head"], lane["prs"][1]["approved_head"] = "h7a", "h8a"

    store.update(DRIVE, "a", approving)
    supervisor.run_pass(DRIVE)
    verbs.carry(DRIVE, "acme/app", 8, "h8b", False, "it now drops the retry")
    shown = page.tapless(store.load(DRIVE, "a"), page.view(DRIVE)["prs"])
    assert [(row["number"], row["moved"]) for row in shown] == [(8, "it now drops the retry"), (9, None)]


def test_a_lane_whose_owner_restarted_keeps_its_place_in_line_and_its_grant(world: dict) -> None:
    opened("a")
    opened("b")
    supervisor.run_pass(DRIVE)
    queue.take(DRIVE, "release", "a/old-token", "a", 30)
    queue.take(DRIVE, "release", "b/old-token", "b", 30)
    queue.take(DRIVE, "release", "b", "b", 30)
    assert list(queue.kept(DRIVE)["requests"]["release"]) == ["b"]
    assert queue.renew(DRIVE, "release", "a", 30)
    queue.release(DRIVE, "release", "a")
    assert queue.take(DRIVE, "release", "b", "b", 30)["granted"] is True


def test_his_tap_lands_on_every_lane_row_carrying_the_pr_and_wakes_its_lane(world: dict) -> None:
    from drive import card

    opened("a", prs=[("acme/app", 7)])
    ended("a", ["pr_merged:acme/app#9"], "6h")
    (store.drive_dir(DRIVE) / store.LIVE).touch()
    assert card.answered({"repo": "acme/app", "number": 7, "label": "Approve", "heads": {"acme/app#7": "h7"}, "source": "pressed the button", "action_ts": None, "waits": ""}) == {"ok": True}
    lane = store.load(DRIVE, "a")
    assert (lane["prs"][0]["approved_head"], lane["wait"]["fired_why"]) == ("h7", "his Approve on acme/app#7")
    verbs.tapped(DRIVE, "acme/app", 7, "Hold", "h7", "his dashboard")
    assert store.load(DRIVE, "a")["prs"][0]["held"].startswith("his Hold at h7")
    with pytest.raises(store.Refused, match="no lane of"):
        verbs.tapped(DRIVE, "acme/app", 8, "Approve", "h8", "his dashboard")


def test_his_github_approving_review_is_his_approve_once_and_never_replaces_a_newer_tap(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(facts, "his_login", lambda drive: "him")
    opened("a", prs=[("acme/app", 7), ("acme/app", 8)])
    world["prs"][("acme/app", 7)] = pull(head="h7") | {"approvals": {"him": "h7", "bot": "h7"}}
    world["prs"][("acme/app", 8)] = pull(head="h8b") | {"approvals": {"him": "h8a"}}
    store.update(DRIVE, "a", lambda lane: lane["prs"][1].__setitem__("approved_head", "h8b"))
    supervisor.run_pass(DRIVE)
    rows_now = store.load(DRIVE, "a")["prs"]
    assert [(row["approved_head"], row["reviewed_head"]) for row in rows_now] == [("h7", "h7"), ("h8b", "h8a")]


def test_his_dashboard_queue_on_a_live_lane_drive_lists_lane_taps_in_queue_order_an_incident_first(world: dict) -> None:
    from drive import view
    from lane import board

    opened("a", prs=[("acme/app", 7)])
    opened("b", prs=[("acme/app", 8)])
    world["prs"][("acme/app", 7)] = pull(head="h7", rollup="SUCCESS") | {"review": "APPROVED", "title": "seven"}
    world["prs"][("acme/app", 8)] = pull(head="h8", rollup="SUCCESS") | {"review": "APPROVED"}
    world["scores"] = {"a": 9, "b": 1}
    supervisor.run_pass(DRIVE)
    assert board.taps(DRIVE) is None
    (store.drive_dir(DRIVE) / store.LIVE).touch()
    assert [(tap["key"], tap["position"]) for tap in board.taps(DRIVE)] == [("acme/app#7", 1), ("acme/app#8", 2)]
    item = view.lane_tap_item(board.taps(DRIVE)[0], {"pr_cards": []}, None)
    assert (item["kind"], item["key"], item["head"], item["checks"], item["title"], item["reason"]["label"], [pr["state"] for pr in item["prs"]]) == ("tap", "acme/app#7", "h7", "pass", "seven", "Lane #1 in the queue", ["open"])
    assert [(entry["key"], entry["head"], entry["group"]) for entry in view.lane_reviews([item], set())] == [("acme/app#7", "h7", "you")]
    assert view.lane_reviews([item], {"acme/app#7"}) == []


def test_his_page_shows_only_stacks_ready_for_his_tap_and_counts_the_rest_by_why(world: dict) -> None:
    from lane import page

    opened("a", prs=[("acme/app", 1), ("acme/app", 2), ("acme/app", 3), ("acme/app", 4)])
    world["prs"][("acme/app", 1)] = pull(head="h1", rollup="SUCCESS", branch="one") | {"review": "APPROVED"}
    world["prs"][("acme/app", 2)] = pull(head="h2", rollup="SUCCESS", base="one", branch="two") | {"review": "APPROVED"}
    world["prs"][("acme/app", 3)] = pull(head="h3", rollup="FAILURE", branch="three")
    world["prs"][("acme/app", 4)] = pull(head="h4", rollup="SUCCESS", branch="four")
    supervisor.run_pass(DRIVE)
    shown = {lane["id"]: lane for lane in page.model(DRIVE)["lanes"]}
    stacks, rest = shown["a"]["ready"]
    assert ([(row["number"], row["above"]) for row in stacks], rest) == ([(1, ["acme/app#2"])], {"behind a parent": 1, "red": 1, "no review": 1})
    assert "3 more not ready" in page.drawn(DRIVE)


def test_his_stack_card_tap_lands_on_each_member(world: dict) -> None:
    from drive import card

    opened("a", prs=[("acme/app", 7), ("acme/app", 8)])
    (store.drive_dir(DRIVE) / store.LIVE).touch()
    card.answered({"repo": "acme/app", "number": 7, "label": "Approve", "heads": {"acme/app#7": "h7", "acme/app#8": "h8"}, "source": "pressed the button", "action_ts": "1794870000.000100", "waits": ""})
    assert [row["approved_head"] for row in store.load(DRIVE, "a")["prs"]] == ["h7", "h8"]
    assert not (store.drive_dir(DRIVE) / "presence.json").exists()


def test_his_lane_screens_show_each_lane_its_owner_at_work_and_its_record(world: dict) -> None:
    from lane import board

    opened("a", next_step="land #7")
    opened("b", prs=[("acme/app", 9)])
    ended("b", ["check:acme/app#9"], "2h")
    store.update(DRIVE, "b", lambda lane: store.note(lane, "queued for merge: acme/app#9 at base abc"))
    world["prs"][("acme/app", 9)] = pull(state="MERGED", head="h9") | {"title": "nine"}
    supervisor.run_pass(DRIVE)
    assert board.screens(DRIVE) is None
    (store.drive_dir(DRIVE) / store.LIVE).touch()
    shown = board.screens(DRIVE)
    now = {lane["id"]: lane for lane in shown["now"]}
    assert (now["a"]["state"], now["a"]["next"]) == ("running", "land #7")
    assert [owner["id"] for owner in shown["work"] if owner["id"] == "a"] == ["a"]
    assert [entry["words"] for entry in shown["history"] if entry["kind"] == "merged"] == ["#9 nine"]


def test_an_owner_runs_with_the_old_drive_off_and_its_verbs_refused(world: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import io

    from lane import command

    monkeypatch.setattr(owners, "old_drive_plugins", lambda: ["long-running@skills"])
    shaped = owners.settings(DRIVE, "a", "token")
    assert (shaped["enabledPlugins"], "mcp__plugin_long-running_drive" in shaped["permissions"]["deny"], "Bash(drive:*)" in shaped["permissions"]["deny"]) == ({"long-running@skills": False}, True, True)
    for typed, refused in (("drive pr land --dry-run 57004", 2), ("cd x && ~/.local/bin/drive job list", 2), ("lane next 'x' --says 'y'", 0), ("echo overdrive done", 0)):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_input": {"command": typed}})))
        assert command.bash_hook() == refused, typed


def test_lane_next_keeps_the_plan_and_the_plain_sentence_his_page_shows(world: dict) -> None:
    from lane import board

    opened("a")
    verbs.set_next(DRIVE, "a", token_of("a"), "wait staging_result:cut-1 then card", "Staging is testing the journal stack; results by 3:38 PM.")
    (store.drive_dir(DRIVE) / store.LIVE).touch()
    shown = board.screens(DRIVE)
    assert (shown["now"][0]["says"], shown["now"][0]["next"], shown["work"][0]["doing"]) == ("Staging is testing the journal stack; results by 3:38 PM.", "wait staging_result:cut-1 then card", "Staging is testing the journal stack; results by 3:38 PM.")


def test_his_board_on_a_live_lane_drive_reads_no_old_drive_state(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from drive import pages, slack, view

    monkeypatch.setattr(slack, "dm", lambda: "D1")
    opened("a", prs=[("acme/app", 7)])
    world["prs"][("acme/app", 7)] = pull(head="h7", rollup="SUCCESS") | {"review": "APPROVED", "title": "Fix: seven"}
    supervisor.run_pass(DRIVE)
    (store.drive_dir(DRIVE) / store.LIVE).touch()
    asked = {"asked": store.stamp(store.now() + timedelta(seconds=5)), "url": "https://x.slack.com/archives/C1/p1794870000000100", "question": "Ship it?", "options": ["Yes", "No"], "recommended": "Yes", "card": {"title": "Ship it?", "options": ["Yes", "No"]}}
    model = view.lane_model(DRIVE, {"questions": [asked], "pr_cards": [], "closed": []}, "live", store.now())
    assert [(item["kind"], item["title"]) for item in model["queue"]] == [("question", "Ship it?"), ("tap", "seven")]
    assert [entry["key"] for entry in model["review"]] == ["acme/app#7"]
    shown = pages.lane_shown(model["lanes"]["now"], [{"slug": "p", "lanes": ["a"], "title": "P"}, {"slug": "q", "lanes": [], "title": "Q"}])
    assert [(page["slug"], page["live"]) for page in shown] == [("p", True), ("q", False)]
    assert [entry["key"] for entry in pages.slice_of(model | {"pages": shown}, shown[0])["prs"]] == ["acme/app#7"]


def test_a_stray_json_file_in_the_lanes_folder_never_stops_a_pass(world: dict, capsys: pytest.CaptureFixture[str]) -> None:
    opened("a")
    (store.drive_dir(DRIVE) / "cards.json").write_text(json.dumps({"questions": []}))
    (store.drive_dir(DRIVE) / "broken.json").write_text("{not json")
    assert store.lane_ids(DRIVE) == ["a"]
    supervisor.run_pass(DRIVE)
    assert "skipped broken.json, cards.json" in capsys.readouterr().err


def test_his_word_to_a_running_lane_reaches_its_owner_at_every_tool_call_until_answered(world: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import io

    from lane import command

    opened("a")
    answered("a")
    assert not store.asks_path(DRIVE, "a").exists()
    verbs.message(DRIVE, "a", "Why is #57589 not merged yet?", "https://x.slack.com/archives/C1/p1794872633408209", "1794872633.408209")
    assert store.load(DRIVE, "a")["state"] == store.RUNNING and store.asks_path(DRIVE, "a").exists()
    monkeypatch.setenv("LANE_OWNER", f"{DRIVE}/a")
    monkeypatch.setenv("LANE_TOKEN", token_of("a"))

    def called(payload: dict) -> str:
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        assert command.note_hook(DRIVE, "a") == 0
        return capsys.readouterr().out

    assert called({"agent_id": "builder-1"}) == ""
    for _ in range(2):
        shown = json.loads(called({}))["hookSpecificOutput"]["additionalContext"]
        assert "Why is #57589 not merged yet?" in shown and "lane say" in shown
    answered("a")
    assert not store.asks_path(DRIVE, "a").exists()
    assert called({}) == ""


def test_an_owner_started_before_the_asks_hook_gets_his_word_in_its_note(world: dict) -> None:
    opened("a")
    answered("a")
    store.update(DRIVE, "a", lambda lane: lane["owner"].pop("asks_hook"))
    verbs.message(DRIVE, "a", "lets change our topologies", None, "1794872633.408209")
    assert "lets change our topologies" in store.note_path(DRIVE, "a").read_text()
    opened("b")
    answered("b")
    verbs.message(DRIVE, "b", "and this one", None, "1794872633.408300")
    assert not store.note_path(DRIVE, "b").exists()


def test_an_owner_whose_session_and_builders_wrote_nothing_for_half_an_hour_is_stalled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import os

    main = tmp_path / "project" / "s1.jsonl"
    (main.with_suffix("") / "subagents").mkdir(parents=True)
    main.write_text("{}\n")
    builder = main.with_suffix("") / "subagents" / "agent-b.jsonl"
    builder.write_text("{}\n")
    old = store.now().timestamp() - 3600
    os.utime(main, (old, old))
    os.utime(builder, (old, old))
    monkeypatch.setattr(owners, "transcript", lambda session: main)
    assert store.now() - owners.active_at({"session": "s1"}) > owners.STALLED_AFTER
    builder.write_text("{}\n{}\n")
    assert store.now() - owners.active_at({"session": "s1"}) < owners.STALLED_AFTER


def test_his_word_to_an_owner_between_turns_is_put_before_it_as_a_message_once_in_a_while(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = []
    quiet = {"at": None}
    monkeypatch.setattr(owners, "own_quiet_since", lambda owner: quiet["at"])
    monkeypatch.setattr(owners, "deliver", lambda owner, text: sent.append(text))
    opened("a")
    answered("a")
    verbs.message(DRIVE, "a", "where does #57961 stand?", None, "1794876000.000100")
    supervisor.run_pass(DRIVE)
    assert sent == []
    quiet["at"] = store.now() - timedelta(minutes=2)
    supervisor.run_pass(DRIVE)
    supervisor.run_pass(DRIVE)
    assert len(sent) == 1 and "where does #57961 stand?" in sent[0]


def test_the_old_drives_hooks_know_every_lane_owners_session(world: dict) -> None:
    opened("a")
    supervisor.run_pass(DRIVE)
    (store.drive_dir(DRIVE) / "queue.json").write_text("{}")
    assert store.owner_sessions() == {store.load(DRIVE, "a")["owner"]["session"]}


def test_a_lane_with_done_checks_names_the_one_each_step_serves_and_its_brief_leads_with_them(world: dict) -> None:
    opened("a")
    supervisor.run_pass(DRIVE)
    assert verbs.add_check(DRIVE, "a", None, "from the Mac app on staging, start a Cloud agent and get its reply") == 1
    assert verbs.add_check(DRIVE, "a", None, "the run page shows the old journal") == 2
    with pytest.raises(store.Refused, match="--serves <n> of 1, 2"):
        verbs.set_next(DRIVE, "a", token_of("a"), "land #7", "Landing the fix.")
    verbs.mark_check(DRIVE, "a", token_of("a"), 2, True, "opened run 41 and saw it")
    with pytest.raises(store.Refused, match="no unmet check"):
        verbs.set_next(DRIVE, "a", token_of("a"), "land #7", "Landing the fix.", serves=2)
    verbs.set_next(DRIVE, "a", token_of("a"), "land #7", "Landing the fix.", serves=1)
    lane = store.load(DRIVE, "a")
    assert lane["serves"] == {"check": 1}
    text = owners.brief(DRIVE, lane, "test")
    assert text.index("Done when") < text.index("His asks")
    assert "- 1. not met: from the Mac app" in text and "- 2. met " in text and "(serves done check 1)" in text
    verbs.mark_check(DRIVE, "a", None, 1, True, "replied in 4 s")
    verbs.set_next(DRIVE, "a", token_of("a"), "watch", "Nothing left.")


def test_the_supervisor_asks_cc_slack_for_a_pr_group_card_per_ready_stack_and_his_tap_lands_each(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from drive import card, slack
    from lane import approve

    (store.drive_dir(DRIVE) / store.LIVE).touch()
    opened("a", prs=[("acme/app", 7), ("acme/app", 8), ("acme/app", 9)], channel="C1")
    world["prs"][("acme/app", 7)] = pull(rollup="SUCCESS", head="abc1234aaaa", branch="b7") | {"review": "APPROVED"}
    world["prs"][("acme/app", 8)] = pull(rollup="SUCCESS", head="def5678bbbb", branch="b8") | {"review": "APPROVED"}
    world["prs"][("acme/app", 9)] = pull(state="MERGED", rollup="SUCCESS", head="0123456cccc", branch="b9") | {"review": "APPROVED"}
    supervisor.run_pass(DRIVE)
    supervisor.run_pass(DRIVE)
    lane = store.load(DRIVE, "a")
    assert not lane.get("cards") and [lane["handover"].count(approve.NAME_ASK.format(key=key)) for key in ("acme/app#7", "acme/app#8")] == [1, 1]
    verbs.add_pr(DRIVE, "a", token_of("a"), "acme/app", 7, "Readers take a file with no mint row")
    verbs.add_pr(DRIVE, "a", token_of("a"), "acme/app", 8, "x" * (approve.NAME_CHARS + 1))
    supervisor.run_pass(DRIVE)
    assert "is 141 characters" in store.load(DRIVE, "a")["handover"]
    verbs.add_pr(DRIVE, "a", token_of("a"), "acme/app", 8, "The writer stops minting")
    supervisor.run_pass(DRIVE)
    cards = store.load(DRIVE, "a")["cards"]
    assert [kept["stacks"] for kept in cards] == [[{"acme/app#7": "abc1234aaaa"}], [{"acme/app#8": "def5678bbbb"}]]
    assert [row.get("carded", {}).get("head") if row.get("carded") else None for row in store.load(DRIVE, "a")["prs"]] == ["abc1234aaaa", "def5678bbbb", None]
    called = []
    monkeypatch.setattr(slack, "cc_slack", lambda *argv, timeout: called.append(argv) or {"card_permalink": "https://x.slack.com/archives/C1/p1"})
    approve.drawn(DRIVE, store.load(DRIVE, "a"), [kept["stacks"][0] for kept in cards])
    assert called == [("dm-status", "--agent", "claude", "--drive", DRIVE, "--as-holder", "--channel", "C1", "--tap-pr", "https://github.com/acme/app/pull/7", "--tap-pr", "https://github.com/acme/app/pull/8")]
    asks = len(store.load(DRIVE, "a")["asks"])
    for number, head in ((7, "abc1234aaaa"), (8, "def5678bbbb")):
        assert card.lane_tapped({"repo": "acme/app", "number": number, "label": "Approve", "heads": {f"acme/app#{number}": head}, "source": "his Slack card", "action_ts": "1794880000.000100"})
    lane = store.load(DRIVE, "a")
    assert ([row["approved_head"] for row in lane["prs"][:2]], len(lane["asks"])) == (["abc1234aaaa", "def5678bbbb"], asks)
    assert [kept["tapped"]["label"] for kept in lane["cards"]] == ["Approve", "Approve"] and not facts.asking({"cards": lane["cards"], "asks": []})
    supervisor.run_pass(DRIVE)
    assert len(store.load(DRIVE, "a")["cards"]) == 2
    assert "a: 1 new" in voice.heard("C1", None, 'tapped "Approve" on "something else"', None, "1794880001.000100")


def test_the_card_command_draws_a_lane_stack_as_the_old_pr_group_card_ahead_of_any_old_drive_entry(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from drive import card

    (store.drive_dir(DRIVE) / store.LIVE).touch()
    opened("a", prs=[("acme/app", 7), ("acme/app", 8)], channel="C1")
    world["prs"][("acme/app", 7)] = pull(rollup="SUCCESS", head="abc1234aaaa", branch="b7") | {"review": "APPROVED"}
    world["prs"][("acme/app", 8)] = pull(rollup="SUCCESS", head="def5678bbbb", base="b7", branch="b8") | {"review": "APPROVED"}
    supervisor.run_pass(DRIVE)
    bodies = {7: "## The stack\nBlob pointers everywhere\nEvery value over 4 KB moves to the shared pool.\n\n## Summary\nWrites pointers.", 8: "## Summary\nReads pointers."}
    heads = {7: "abc1234aaaa", 8: "def5678bbbb"}

    def read(entries: dict, fresh: bool = False) -> dict:
        return {key: {"title": f"feat: pointer step {key[-1]}", "body": bodies[int(key[-1])], "url": f"https://github.com/acme/app/pull/{key[-1]}", "headRefOid": heads[int(key[-1])], "state": "OPEN", "files": [], "changedFiles": 0, "comments": []} for key in entries}

    monkeypatch.setattr(card, "github_reads", read)
    monkeypatch.setattr(card, "drive_cards", lambda asked, redraw=False: pytest.fail(f"{asked} went to the old drive"))
    assert card.cards([{"repo": "acme/app", "number": 8}])["skipped"][0]["reason"] == card.LANE_UNNAMED
    name = "Every file goes through blobstore, cells hold pointers, and SandSQL keeps no per-file bookkeeping of its own anywhere: one shared pool"
    store.update(DRIVE, "a", lambda lane: lane["prs"][0].__setitem__("words", name))
    [drawn] = card.cards([{"repo": "acme/app", "number": 8}])["cards"]
    assert (drawn["repo"], drawn["number"], drawn["card"]["title"], drawn["card"]["options"]) == ("acme/app", 7, name, ["Approve", "Hold"])
    assert json.dumps(drawn["card"]).count(name) == 1 and "Blob pointers everywhere" not in drawn["card"]["title"] + drawn["card"]["body"]
    assert [row["url"] for row in drawn["card"]["prs"]] == ["https://github.com/acme/app/pull/7", "https://github.com/acme/app/pull/8"]
    assert "shared pool" in drawn["card"]["body"] and "card " not in json.dumps(drawn["card"]) and "2 PRs" not in drawn["card"]["title"]
    heads[8] = "fff0000dddd"
    assert card.cards([{"repo": "acme/app", "number": 8}], redraw=True)["cards"][0]["card"]["status"] == "needs_you"
    store.update(DRIVE, "a", lambda lane: [row.__setitem__("approved_head", {7: "abc1234aaaa", 8: "def5678bbbb"}[row["number"]]) for row in lane["prs"]])
    assert card.cards([{"repo": "acme/app", "number": 7}])["skipped"][0]["reason"] == card.LANE_UNTAPPED
    assert "options" not in card.cards([{"repo": "acme/app", "number": 7}], redraw=True)["cards"][0]["card"]


def test_a_lane_with_an_unmet_done_check_its_step_serves_not_is_woken_and_cannot_end_its_turn_unserved(world: dict) -> None:
    opened("a")
    supervisor.run_pass(DRIVE)
    ended("a", ["his_reply"], "2h")
    verbs.add_check(DRIVE, "a", None, "start a Cloud agent from the Mac app and get its reply")
    supervisor.run_pass(DRIVE)
    lane = store.load(DRIVE, "a")
    assert lane["state"] == store.RUNNING and "the nearest unmet done check is 1: start a Cloud agent" in lane["owner"]["woke"]
    with pytest.raises(store.Refused, match="no card or question of this lane is open to him, so nothing he could answer would end it: the nearest unmet done check is 1"):
        ended("a", ["his_reply"], "2h")
    with pytest.raises(store.Refused, match="your step names none"):
        ended("a", ["pr_merged:acme/app#7"], "2h")
    verbs.set_next(DRIVE, "a", token_of("a"), "ship the agent fix", "Shipping the Cloud agent fix.", serves=1)
    saying.say(DRIVE, "a", "Ship the agent fix to staging now?", token=token_of("a"), options=["Ship it", "Wait"])
    ended("a", ["his_reply"], "2h")
    supervisor.run_pass(DRIVE)
    assert store.load(DRIVE, "a")["state"] == store.WAITING


def test_a_blocker_lands_on_the_lane_that_waits_on_it_and_wakes_it(world: dict) -> None:
    opened("a")
    supervisor.run_pass(DRIVE)
    ended("a", ["his_reply"], "2h")
    verbs.blocked(DRIVE, "a", "enqueue of #57630 refused: stood-down wait", "lane-land")
    lane = store.load(DRIVE, "a")
    assert lane["wait"]["fired_why"].startswith("a blocker from lane-land")
    assert "blocked, from lane-land: enqueue of #57630 refused" in store.log_path(DRIVE, "a").read_text()
    assert "Bash(cci post:*)" in owners.settings(DRIVE, "a", "t")["permissions"]["deny"]


def test_lane_install_keeps_his_dashboard_beside_the_lanes_and_boots_out_the_old_drives_one(world: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import plistlib

    monkeypatch.setenv("HOME", str(tmp_path))
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    ran = []
    monkeypatch.setattr(supervisor.subprocess, "run", lambda args, **kwargs: ran.append(args[:2]) or subprocess.CompletedProcess(args, 1 if args[1] == "print" else 0, "", ""))
    monkeypatch.setattr(supervisor, "newest_install", lambda: Path("/installs/1.0.9"))
    (store.drive_dir(DRIVE) / store.LIVE).touch()
    mine = agents / f"{supervisor.DASHBOARD_LABEL}{DRIVE}.plist"
    mine.write_bytes(plistlib.dumps({"Label": mine.stem, "StandardOutPath": "/old/dashboard.log", "StandardErrorPath": "/old/dashboard.log"}))
    old = agents / f"{supervisor.DASHBOARD_LABEL}ssql-duckdb.plist"
    old.write_bytes(plistlib.dumps({"Label": old.stem}))
    done = supervisor.dashboard(DRIVE)
    assert done[0] == f"booted out {old.stem}" and done[1].startswith(f"reloaded {mine.stem} on /installs/1.0.9")
    assert not old.exists() and old.with_name(f"{old.name}.archived").exists()
    assert plistlib.loads(mine.read_bytes())["StandardOutPath"] == str(store.drive_dir(DRIVE) / "dashboard.log")
    assert ["launchctl", "bootstrap"] in ran
    ran.clear()
    assert supervisor.dashboard(DRIVE) == [] and ran == []


def test_a_handover_entry_is_stamped_with_its_write_time_and_keeps_a_quoted_past_time(world: dict) -> None:
    opened("a")
    past = (store.now() - timedelta(minutes=20)).astimezone(verbs.PACIFIC).strftime("%-I:%M %p")
    verbs.handover(DRIVE, "a", f"the owner promised the fix at {past}", "drive-redesign")
    verbs.handover(DRIVE, "a", "pushed the fix; results by 9:00 AM tomorrow", "drive-redesign")
    assert store.load(DRIVE, "a")["handover"].startswith("[written ") and " PT (" in store.load(DRIVE, "a")["handover"]


def test_his_approved_prs_needing_a_fix_become_one_queue_item_each_pulled_by_workers_and_done_wakes_the_lane(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from lane import fixes, work

    started = []
    monkeypatch.setattr(work, "launch", lambda drive, cwd: started.append(cwd) or (work.worker_path(drive, f"worker-{len(started)}").write_text(json.dumps({"id": f"worker-{len(started)}", "job": f"job{len(started)}", "session": f"worker-session-{len(started)}", "started": store.stamp()})) and {"id": f"worker-{len(started)}", "job": f"job{len(started)}"}))
    monkeypatch.setattr(owners, "record", lambda path: {"state": "working"})
    monkeypatch.setattr(owners, "active_at", lambda owner: store.now())
    (store.drive_dir(DRIVE) / store.LIVE).touch()
    opened("a", prs=[("acme/app", n) for n in (1, 2, 3, 4, 5)], channel="C1")

    def approving(lane: dict) -> None:
        for row in lane["prs"]:
            row["approved_head"] = f"h{row['number']}"

    store.update(DRIVE, "a", approving)
    moved = {"commits": [("old", "him")], "commit_count": 1}
    world["prs"][("acme/app", 1)] = pull(rollup="SUCCESS", head="h1", branch="b1") | {**moved, "mergeable": "CONFLICTING"}
    world["prs"][("acme/app", 2)] = pull(rollup="FAILURE", head="h2", branch="b2") | {**moved, "failing": ["lint"], "mergeable": "MERGEABLE"}
    world["prs"][("acme/app", 3)] = pull(rollup="SUCCESS", head="h3", branch="b3") | {**moved, "mergeable": "MERGEABLE", "reviewed_heads": []}
    world["prs"][("acme/app", 4)] = pull(rollup="FAILURE", head="h4", branch="b4") | {**moved, "failing": ["unit"], "mergeable": "MERGEABLE"}
    world["prs"][("acme/app", 5)] = pull(state="MERGED", rollup="FAILURE", head="h5", branch="b5")
    supervisor.run_pass(DRIVE)
    queued = {item["subject"]: item for item in work.items(DRIVE)}
    assert sorted(queued) == ["acme/app#1", "acme/app#2", "acme/app#3", "acme/app#4"] and queued["acme/app#1"]["kind"] == "rebase"
    assert queued["acme/app#1"]["words"].startswith("Rebase acme/app#1 (branch b1) onto origin/main") and "lint" in queued["acme/app#2"]["words"]
    assert len(started) == 4
    supervisor.run_pass(DRIVE)
    assert len(work.items(DRIVE)) == 4 and len(started) == 4
    top = work.pull(DRIVE)
    assert top["top"]["lane"] == "a" and len(top["with"]) == 3
    rebase = queued["acme/app#1"]["id"]
    assert work.take(DRIVE, "worker-1", [rebase]) == [rebase] and work.take(DRIVE, "worker-2", [rebase]) == []
    assert fixes.held_keys(DRIVE, store.load(DRIVE, "a")) == {"acme/app#1"} and "worker-session-1" in store.owner_sessions()
    world["prs"][("acme/app", 1)] = pull(rollup="SUCCESS", head="h1b", branch="b1") | {**moved, "mergeable": "CONFLICTING"}
    supervisor.run_pass(DRIVE)
    assert work.load(DRIVE, rebase)["state"] == work.TAKEN
    answered("a")
    with pytest.raises(store.Refused, match="no work item w-nothing"):
        verbs.end_turn(DRIVE, "a", token_of("a"), [verbs.parse_event("work:w-nothing")], "1h", "waiting")
    verbs.end_turn(DRIVE, "a", token_of("a"), [verbs.parse_event(f"work:{rebase}")], "1h", "waiting on the rebase")
    work.done(DRIVE, rebase, "pushed h1c, rebased on main")
    lane = store.load(DRIVE, "a")
    assert lane["state"] != store.WAITING or lane["wait"].get("fired")
    monkeypatch.setattr(owners, "record", lambda path: {"state": "done"})
    work.take(DRIVE, "worker-2", [queued["acme/app#2"]["id"]])
    assert any("back in the queue" in line for line in work.expire(DRIVE))
    assert work.load(DRIVE, queued["acme/app#2"]["id"])["state"] == work.QUEUED


def test_a_pass_step_that_raises_stops_no_other_step(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from lane import approve

    (store.drive_dir(DRIVE) / store.LIVE).touch()
    opened("a", prs=[("acme/app", 7)])
    world["prs"][("acme/app", 7)] = pull(rollup="SUCCESS")
    landed = []
    monkeypatch.setattr(supervisor.lane_land, "spawn", lambda drive: landed.append(drive))
    monkeypatch.setattr(approve, "draw", lambda drive: (_ for _ in ()).throw(RuntimeError("HTTP Error 403: Forbidden")))
    result = supervisor.run_pass(DRIVE)
    assert landed == [DRIVE] and any("no Approve card was drawn: RuntimeError: HTTP Error 403" in error for error in result["errors"])
    assert "HTTP Error 403" in (store.drive_dir(DRIVE) / supervisor.ALERTS).read_text()


def test_his_words_keep_their_eyes_while_read_get_construction_once_answered_with_work_going_on_and_a_check_once_done(world: dict) -> None:
    (store.drive_dir(DRIVE) / store.LIVE).touch()
    opened("a", channel="C1")
    supervisor.run_pass(DRIVE)
    world["messages"]["a"] = [{"words": "have you merged these?", "ts": "200.0", "at": store.stamp(), "link": "https://x.slack.com/archives/C1/p200000"}]
    supervisor.run_pass(DRIVE)
    assert ("200.0", "construction", ["eyes"]) not in world.get("slack", [])

    def answered(lane: dict) -> None:
        next(ask for ask in lane["asks"] if ask.get("ts") == "200.0")["replied"] = "QUEUED a1"

    store.update(DRIVE, "a", answered)
    supervisor.run_pass(DRIVE)
    supervisor.run_pass(DRIVE)
    assert world["slack"].count(("200.0", "construction", ["eyes"])) == 1
    from lane import ack

    ack.finished(DRIVE, "a", "200.0")
    assert ("200.0", "white_check_mark", ["construction", "eyes"]) in world["slack"]


def test_replaying_v3s_257_pm_wait_on_a_cut_no_stager_holds_is_refused_one_whose_ask_goes_fires_in_the_next_pass_and_twice_is_a_defect(world: dict) -> None:
    opened("a")
    supervisor.run_pass(DRIVE)
    with pytest.raises(store.Refused, match="no stager has taken or queued cut run-page-v3-20261116225700"):
        ended("a", ["staging_result:run-page-v3-20261116225700"], "2h")
    ask = asked("a", "run-page-v3-20261116225700")
    ended("a", ["staging_result:run-page-v3-20261116225700"], "2h")
    ask.unlink()
    supervisor.run_pass(DRIVE)
    lane = store.load(DRIVE, "a")
    assert lane["state"] == store.RUNNING and "no stager has an ask open for cut run-page-v3-20261116225700" in lane["owner"]["woke"]
    asked("a", "run-page-v3-20261116225700")
    ended("a", ["staging_result:run-page-v3-20261116225700"], "2h")
    ask.unlink()
    supervisor.run_pass(DRIVE)
    assert store.load(DRIVE, "a")["defect"].startswith("its wait twice had nothing producing it")
    assert any(line.startswith("a: DEFECT") for line in supervisor.check(DRIVE)[1])
    verbs.blocked(DRIVE, "a", "the target sandsql-nodes-4 exists nowhere", "stager")
    assert any(line == "a: BLOCKED stager: the target sandsql-nodes-4 exists nowhere" for line in supervisor.check(DRIVE)[1])


def test_the_brief_prints_each_pr_as_its_row_stands_before_any_carried_history(world: dict) -> None:
    opened("a", prs=[("acme/app", 7), ("acme/app", 8)])

    def adopting(lane: dict) -> None:
        lane["prs"][0] |= {"approved_head": "abc1234def", "dangerous": {"head": "abc1234def", "classes": {}}}
        lane["prs"][1] |= {"held": "his Hold at def"}
        lane["handover"] = "seven PRs need a fresh tap"

    store.update(DRIVE, "a", adopting)
    text = owners.brief(DRIVE, store.load(DRIVE, "a"), "test")
    assert "acme/app#7: approved by him at abc1234; staging after merge" in text and "acme/app#8: held by him: his Hold at def" in text
    assert text.index("approved by him at abc1234") < text.index("seven PRs need a fresh tap") and "the rows are right" in text


def test_the_supervisor_starts_an_audit_every_15_minutes_that_files_defects_for_the_codes_owner(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from lane import audit

    started = []
    monkeypatch.setattr(audit.subprocess, "Popen", lambda argv, **kwargs: started.append(argv))
    monkeypatch.setattr(audit.session, "argv", lambda config, role, checkout, own, head, tail: ["claude", *head, "--model", "sonnet", "--effort", "high", *tail])
    (store.drive_dir(DRIVE) / store.LIVE).touch()
    opened("a", prs=[("acme/app", 7)])
    world["prs"][("acme/app", 7)] = pull()
    audit.missed(DRIVE, "a card asking him to merge PRs he already approved")
    supervisor.run_pass(DRIVE)
    supervisor.run_pass(DRIVE)
    [argv] = started
    assert argv[:4] == ["claude", "-p", "--model", "sonnet"] and argv[argv.index("--effort") + 1] != "max"
    assert "a card asking him to merge PRs he already approved" in argv[-1] and "voice.md" in argv[-1] and "defect --what" in argv[-1]
    sent = []
    monkeypatch.setattr(owners, "send", lambda to, text: sent.append((to, text)))
    path = audit.defect(DRIVE, "no 👀 on his 6:25 PM ask", "https://x/p1", "no ack on receipt", "lane-core")
    filed = json.loads(path.read_text())
    assert (filed["kind"], filed["owner"], sent[0][0]) == ("fix the system", "lane-core", "lane-core") and "no 👀 on his 6:25 PM ask" in sent[0][1]
    assert any(line.startswith(f"defect {path.stem} for lane-core, open 0m") for line in supervisor.check(DRIVE)[1])
    audit.fixed(DRIVE, path.stem, "abc1234")
    assert audit.open_defects(DRIVE) == []


def test_the_pr_read_of_300_prs_goes_in_chunks_of_40_and_a_failed_chunk_drops_only_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = {("acme/app", number) for number in range(1, 301)}
    asked = []

    def reading(chunk: list[tuple[str, int]], commits: int) -> dict:
        asked.append(len(chunk))
        if chunk[0] == ("acme/app", 1):
            raise subprocess.TimeoutExpired("gh", 45)
        return {key: {"state": "OPEN"} for key in chunk}

    monkeypatch.setattr(facts, "read_chunk", reading)
    lost: list[str] = []
    read = facts.read_prs(keys, commits=1, failed=lost)
    assert max(asked) == 40 and len(read) == 260 and len(lost) == 1 and "TimeoutExpired" in lost[0]
    assert asked.count(40) >= 8
    monkeypatch.setattr(facts, "read_chunk", lambda chunk, commits: (_ for _ in ()).throw(store.Refused("GitHub read failed: unexpected end of JSON input")))
    with pytest.raises(store.Refused, match="every chunk"):
        facts.read_prs(keys, commits=1)


def test_a_reply_after_his_approve_tap_keeps_the_approved_state(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    opened("a", prs=[("acme/app", 7)])
    monkeypatch.setattr(voice.page, "view", lambda drive: {"prs": {"acme/app#7": {"head": "h7", "state": "OPEN"}}})
    voice.tapped_pr(DRIVE, store.load(DRIVE, "a"), 'tapped "Approve #7" on "Approve #7?"', "https://x/p1", "1794880000.000100")
    assert store.load(DRIVE, "a")["prs"][0]["approved_head"] == "h7"
    answered("a")
    saying.say(DRIVE, "a", "Landing #7 now.", token=token_of("a"))
    assert store.load(DRIVE, "a")["prs"][0]["approved_head"] == "h7"


def test_every_session_the_lane_drive_starts_runs_light_on_its_roles_model_and_never_max(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from drive import agents
    from lane import session

    monkeypatch.setattr(agents, "session_settings", lambda env: {"env": {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "350000", "DISABLE_AUTOUPDATER": "1", **env}, "permissions": {"deny": ["AskUserQuestion"]}, "enabledPlugins": {}})
    monkeypatch.setattr(agents, "light", lambda settings, role, checkout: settings | {"autoMemoryEnabled": False, "skillListingMaxDescChars": 300, "light_role": role})
    monkeypatch.setattr(agents, "datadog_servers", lambda: {"mcpServers": {"plugin:datadog:mcp": {}}})
    shown = {}
    for role in session.ROLES:
        argv = session.argv({"models": {"owner": ["opus", "max"]}}, role, tmp_path, {"env": {"LANE_OWNER": "d/a"}, "permissions": {"deny": ["Bash(drive:*)"]}}, ["--bg"], ["-n", "x", "prompt"])
        settings = json.loads(argv[argv.index("--settings") + 1])
        shown[role] = (argv[argv.index("--model") + 1], argv[argv.index("--effort") + 1] if "--effort" in argv else None, "--advisor" in argv)
        assert "--strict-mcp-config" in argv and "--append-system-prompt-file" in argv and argv[-1] == "prompt"
        assert settings["autoMemoryEnabled"] is False and settings["env"]["LANE_OWNER"] == "d/a" and settings["permissions"]["deny"] == ["AskUserQuestion", "Bash(drive:*)"]
        assert ("CLAUDE_CODE_DISABLE_ADVISOR_TOOL" in settings["env"]) == (role not in session.ADVISED)
    assert shown == {"owner": ("opus", "high", True), "builder": ("opus", "high", True), "stager": ("opus", "high", True), "auditor": ("sonnet", "high", False), "acker": ("haiku", None, False)}


def test_a_lane_whose_home_is_his_dm_posts_nothing_until_it_moves_to_a_project_channel(world: dict) -> None:
    opened("a", channel="D0DM")
    with pytest.raises(store.Refused, match="never posts there"):
        saying.say(DRIVE, "a", "on it")
    with pytest.raises(store.Refused, match="never his DM"):
        verbs.home(DRIVE, "a", "D0OTHER", None)
    verbs.home(DRIVE, "a", "C0PROJECT", None)
    saying.say(DRIVE, "a", "on it")
    assert store.load(DRIVE, "a")["channel"] == "C0PROJECT"


def test_an_ask_unreplied_two_minutes_after_he_sent_it_is_put_before_its_owner_again_once_and_his_page_shows_reply_minutes(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from lane import board

    delivered = []
    monkeypatch.setattr(owners, "deliver", lambda owner, text: delivered.append(text))
    (store.drive_dir(DRIVE) / store.LIVE).touch()
    sent = store.now().timestamp() - 300
    opened("a", ts=f"{sent:.6f}")
    supervisor.run_pass(DRIVE)
    supervisor.run_pass(DRIVE)
    assert len(delivered) == 1 and "sent 5 minutes ago" in delivered[0] and "do a" in delivered[0]
    answered("a")
    [shown] = board.screens(DRIVE)["now"]
    assert shown["replies"][0]["minutes"] >= 5


def test_an_incident_lane_whose_monitor_starts_alerting_is_woken_that_pass(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    states = {"7": "OK"}
    monkeypatch.setattr(facts, "monitor_states", lambda drive, ids: {key: states[key] for key in ids})
    opened("a")
    supervisor.run_pass(DRIVE)
    ended("a", ["his_reply"], "2h")
    verbs.set_incident(DRIVE, "a", None, link="https://alert", monitor="7")
    supervisor.run_pass(DRIVE)
    states["7"] = "Alert"
    supervisor.run_pass(DRIVE)
    assert "monitor 7 is alerting" in store.load_all(DRIVE)["a"]["owner"]["woke"] or "monitor 7 is alerting" in store.log_path(DRIVE, "a").read_text()


def test_his_page_shows_each_lanes_last_hour_of_spend_from_its_transcripts(world: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from lane import cost

    opened("a")
    supervisor.run_pass(DRIVE)
    transcript = tmp_path / "owner.jsonl"
    turn = lambda at, model: json.dumps({"timestamp": at, "message": {"model": model, "usage": {"input_tokens": 1000, "output_tokens": 2000, "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 0}}})
    transcript.write_text("\n".join([turn(store.stamp(store.now() - timedelta(hours=2)), "claude-opus-5-5"), turn(store.stamp(), "claude-opus-5-5"), turn(store.stamp(), "claude-haiku-4-5")]) + "\n")
    monkeypatch.setattr(owners, "transcript", lambda session: transcript)
    spent = cost.hourly(DRIVE, store.load(DRIVE, "a"))
    assert spent == {"tokens": {"claude-opus-5-5": {"input": 1000, "output": 2000, "cache_read": 1_000_000, "cache_write": 0}, "claude-haiku-4-5": {"input": 1000, "output": 2000, "cache_read": 1_000_000, "cache_write": 0}}, "dollars_per_hour": None}
    (store.drive_dir(DRIVE) / "config.json").write_text(json.dumps({"active": 3, "slots": 2, "posting": False, "prices": {"opus": {"input": 5, "output": 25, "cache_read": 0.5}}}))
    assert cost.hourly(DRIVE, store.load(DRIVE, "a"))["dollars_per_hour"] == round(0.005 + 0.05 + 0.5, 2)


def test_a_done_check_can_be_reworded_or_dropped_keeping_what_the_step_serves(world: dict) -> None:
    opened("a")
    for words in ("unsure (a bot post)", "start a Cloud agent", "team-lead's wording"):
        verbs.add_check(DRIVE, "a", None, words)
    verbs.mark_check(DRIVE, "a", None, 2, True, "ran it")
    verbs.reword_check(DRIVE, "a", None, 1, "share links work as his note asked")
    store.update(DRIVE, "a", lambda lane: lane.__setitem__("serves", {"check": 3}))
    verbs.reword_check(DRIVE, "a", None, 1, None)
    lane = store.load(DRIVE, "a")
    assert [check["words"] for check in lane["done"]] == ["start a Cloud agent", "team-lead's wording"] and lane["done"][0]["met_at"] and lane["serves"] == {"check": 2}
    with pytest.raises(store.Refused, match="1 to 2, not 5"):
        verbs.reword_check(DRIVE, "a", None, 5, "x")


def test_an_owner_whose_builders_write_but_whose_own_transcript_is_quiet_reads_as_quiet_for_a_nudge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import os
    import time

    own = tmp_path / "s1.jsonl"
    own.write_text("{}\n")
    old = time.time() - 300
    os.utime(own, (old, old))
    (tmp_path / "s1" / "subagents").mkdir(parents=True)
    (tmp_path / "s1" / "subagents" / "agent-1.jsonl").write_text("{}\n")
    monkeypatch.setattr(owners, "transcript", lambda session: own)
    assert store.now() - owners.own_quiet_since({"session": "s1"}) > supervisor.QUIET_BEFORE_NUDGE
    assert store.now() - owners.active_at({"session": "s1"}) < supervisor.QUIET_BEFORE_NUDGE


def test_replaying_the_ringo_thread_a_promised_pr_by_345_with_nothing_after_wakes_its_owner_and_files_a_defect(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from lane import audit, verbs as lane_verbs

    filed = []
    monkeypatch.setattr(audit, "defect", lambda drive, what, evidence, mechanism, owner: filed.append((what, owner)))
    (store.drive_dir(DRIVE) / store.LIVE).touch()
    opened("a", channel="C0CJ378CNMP")
    supervisor.run_pass(DRIVE)
    answered("a")
    due = (store.now() + timedelta(minutes=57)).astimezone(lane_verbs.PACIFIC).strftime("%-I:%M %p")
    saying.say(DRIVE, "a", f"Ringo's fix goes up as a PR by {due}; it failed at 2:30 PM before.", token=token_of("a"))
    [kept] = store.load(DRIVE, "a")["promises"]
    assert kept["due"] > store.stamp()
    verbs.end_turn(DRIVE, "a", token_of("a"), [verbs.parse_event("pr_merged:acme/app#7")], "2h", "test wait")
    later = store.now() + timedelta(hours=1)
    monkeypatch.setattr(store, "now", lambda: later)
    supervisor.run_pass(DRIVE)
    lane = store.load(DRIVE, "a")
    assert lane["promises"][0]["overdue"] and "overdue: its post" in (lane.get("wait") or {}).get("fired_why", "") + (lane.get("owner") or {}).get("woke", "")
    alerts = store.drive_dir(DRIVE) / supervisor.ALERTS
    assert len(filed) == 1 and filed[0][1] == "lane-core" and "broke a promise" in filed[0][0], (alerts.read_text() if alerts.is_file() else "no alerts", lane.get("promises"))
    from lane import work

    assert [item["kind"] for item in work.items(DRIVE) if item["subject"].startswith("promise ")] == ["ask"]
    supervisor.run_pass(DRIVE)
    assert len(filed) == 1


def test_each_pass_drops_expired_leases_and_rows_of_resources_the_queue_no_longer_grants(world: dict) -> None:
    from lane import queue

    opened("a")
    stale = (store.now() - timedelta(minutes=5)).isoformat()
    fresh = (store.now() + timedelta(minutes=30)).isoformat()
    queue.written(DRIVE, {"leases": {"release": [{"holder": "x", "lane": "a", "until": stale}, {"holder": "y", "lane": "a", "until": fresh}], "card": [{"holder": "z", "lane": "a", "until": fresh}]}, "requests": {"staging-env": {"w": {"lane": "a", "at": stale}}}})
    supervisor.run_pass(DRIVE)
    left = queue.kept(DRIVE)
    assert [lease["holder"] for lease in left["leases"]["release"]] == ["y"] and "card" not in left["leases"] and "staging-env" not in left["requests"]


def test_a_new_head_under_a_live_card_draws_that_stack_again_and_supersedes_only_its_card(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from lane import approve, page

    drawn = []
    monkeypatch.setattr(approve, "drawn", lambda drive, lane, stacks: drawn.append(stacks) or f"https://x.slack.com/archives/C1/p{len(drawn)}")
    (store.drive_dir(DRIVE) / "config.json").write_text(json.dumps({"active": 3, "slots": 2, "posting": True}))
    (store.drive_dir(DRIVE) / store.LIVE).touch()
    opened("a", prs=[("acme/app", 1), ("acme/app", 2)])
    store.update(DRIVE, "a", lambda lane: [row.__setitem__("words", f"step {row['number']}") for row in lane["prs"]])
    stacks = [{"acme/app#1": "h1"}, {"acme/app#2": "h2"}]
    monkeypatch.setattr(approve, "ready_stacks", lambda lane, seen: stacks)
    monkeypatch.setattr(approve, "stacks_waiting", lambda lane, seen: [heads for heads in stacks if not any(heads == old for card in lane.get("cards", []) for old in card["stacks"])])
    monkeypatch.setattr(page, "view", lambda drive: {"prs": {}})
    approve.draw(DRIVE)
    stacks[1] = {"acme/app#2": "h3"}
    approve.draw(DRIVE)
    first, second, third = store.load(DRIVE, "a")["cards"]
    assert drawn == [[{"acme/app#1": "h1"}, {"acme/app#2": "h2"}], [{"acme/app#2": "h3"}]]
    assert not first.get("superseded") and second["superseded"] == third["id"] and third["stacks"] == [{"acme/app#2": "h3"}]
    assert not facts.asking({"cards": [second], "asks": []})


def test_his_message_reaches_a_running_owner_with_a_builder_out_in_the_call_that_hears_it(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    sent = []
    monkeypatch.setattr(owners, "deliver", lambda owner, text: sent.append((owner["job"], text)))
    monkeypatch.setattr(owners, "in_flight", lambda owner: 1)
    opened("a", channel="C1")
    supervisor.run_pass(DRIVE)
    told = voice.heard("C1", None, "status?", "https://x.slack.com/archives/C1/p1794883276477119", "1794883276.477119")
    assert told.startswith("a: 1 new, delivered to its running owner") and len(sent) == 1 and "status?" in sent[0][1] and "--re 1794883276.477119" in sent[0][1]
    assert voice.heard("C1", None, "status?", None, "1794883276.477119") == "a: seen before" and len(sent) == 1


def test_a_github_delivery_naming_a_lane_pr_wakes_the_lane_waiting_on_it_in_the_call_that_reads_it(world: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from lane import events

    log = tmp_path / "deliveries.jsonl"
    log.write_text(json.dumps({"kind": "delivery", "repo": "acme/app", "event": "push", "base": "old"}) + "\n")
    monkeypatch.setattr(events, "deliveries_path", lambda: log)
    opened("a", prs=[("acme/app", 7)])
    supervisor.run_pass(DRIVE)
    ended("a", ["pr_merged:acme/app#7"])
    assert events.github(DRIVE) == []
    world["prs"][("acme/app", 7)] = pull(state="MERGED", rollup="SUCCESS")
    with log.open("a") as out:
        out.write(json.dumps({"kind": "delivery", "repo": "acme/app", "event": "pull_request", "numbers": [7]}) + "\n")
    said = events.github(DRIVE)
    assert "woke a" in said and store.load(DRIVE, "a")["state"] != store.WAITING or store.load(DRIVE, "a")["wait"].get("fired")
    assert events.github(DRIVE) == []


def test_replaying_57827_a_moved_head_after_his_approve_queues_a_carry_item_and_an_incompatible_verdict_revokes_it(world: dict) -> None:
    from lane import page, work

    (store.drive_dir(DRIVE) / store.LIVE).touch()
    opened("a", prs=[("acme/app", 57827)], channel="C1")
    store.update(DRIVE, "a", lambda lane: lane["prs"][0].__setitem__("approved_head", "149d6c76"))
    world["prs"][("acme/app", 57827)] = pull(rollup="SUCCESS", head="f143038a", branch="b") | {"mergeable": "MERGEABLE", "commits": [], "commit_count": 0}
    supervisor.run_pass(DRIVE)
    [item] = [item for item in work.items(DRIVE) if item["kind"] == "carry"]
    assert item["subject"] == "acme/app#57827" and "range-diff" in item["words"] and "149d6c76" in item["words"]
    verbs.carry(DRIVE, "acme/app", 57827, "f143038a", False, "the turnEndNote conflict was resolved by changing when the note shows")
    row = store.load(DRIVE, "a")["prs"][0]
    assert row["approved_head"] is None and row["revoked"]["head"] == "149d6c76"
    assert [tap["number"] for tap in page.tapless(store.load(DRIVE, "a"), page.view(DRIVE)["prs"])] == [57827]


def test_a_carried_approve_is_judged_against_the_head_he_tapped_not_the_head_the_carry_over_moved_it_to(world: dict) -> None:
    from lane import work

    (store.drive_dir(DRIVE) / store.LIVE).touch()
    opened("a", prs=[("acme/app", 5)], channel="C1")

    def carried_over(lane: dict) -> None:
        lane["prs"][0].update(approved_head="h2", tapped_head="h1", carried={"head": "h2", "compatible": True})

    store.update(DRIVE, "a", carried_over)
    store.update(DRIVE, "a", lambda lane: lane["prs"][0].pop("carried"))
    world["prs"][("acme/app", 5)] = pull(rollup="SUCCESS", head="h2", branch="b") | {"mergeable": "MERGEABLE", "commits": [("h2", "lanes-bot")], "commit_count": 1}
    supervisor.run_pass(DRIVE)
    [item] = [item for item in work.items(DRIVE) if item["kind"] == "carry"]
    assert "his tap at h1" in item["words"]
    verbs.carry(DRIVE, "acme/app", 5, "h2", True, "a clean rebase")
    assert store.load(DRIVE, "a")["prs"][0]["carried"]["from"] == "h1"


def test_a_failed_item_is_queued_once_more_with_why_and_a_second_failure_tells_its_lane_to_re_plan(world: dict) -> None:
    from lane import work

    opened("a")
    item = work.add(DRIVE, "red", "a", "acme/app#3", "fix lint")
    work.take(DRIVE, "worker-1", [item["id"]])
    again = work.done(DRIVE, item["id"], "the lint step needs a token", failed=True)
    assert again["state"] == work.QUEUED and "the lint step needs a token" in again["words"]
    work.take(DRIVE, "worker-2", [item["id"]])
    assert work.done(DRIVE, item["id"], "still no token", failed=True)["state"] == work.FAILED
    assert "re-plan it" in store.note_path(DRIVE, "a").read_text()
