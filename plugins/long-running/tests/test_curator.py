import datetime as dt
import json
import threading

import brief
from brief import Curator, digest

T0 = dt.datetime(2026, 10, 27, 0, 52, tzinfo=dt.timezone.utc)


def record(minutes, lane, text, kind="decision"):
    return {"at": (T0 + dt.timedelta(minutes=minutes)).isoformat(), "lane": lane, "kind": kind, "text": text}


def model():
    rulings = [record(0, "root", "#43421 fails only because the new code does not compile; its fixers are p8a-step7 and p8a-step5-4.")]
    rulings += [record(1 + i, "root", f"#4{i:04d} lands after its tests pass.") for i in range(20)]
    return {
        "records": [record(-30, "p8f-replica-3", "#43421 opened.", "opened"), *rulings, record(25, "p8a-step7", "Moving the run store callers.", "report")],
        "workers": [{"lane": "p8a-step7", "at": T0.isoformat(), "prs": []}, {"lane": "p8a-step5-4", "at": T0.isoformat(), "prs": []}],
        "drive": {"prs": [{"number": 43421, "state": "failing", "worker": {"lane": "p8f-replica-3", "at": T0.isoformat()}, "owner": "p8f-replica-3", "next": "p8f-replica-3 fixes buildkite/test."}]},
        "prs": {"43421": {"title": "Cloud viewer reads chats through the replica"}},
        "now": {"releases": []},
        "milestone_state": [],
        "steps": [],
        "needs": [],
        "fetch": lambda kinds, since: [],
        "pages": {},
        "words": [],
        "story": {},
        "status_now": {"at": T0.isoformat(), "paragraphs": [{"label": "Main work", "text": "The cloud viewer reading chats through the storage engine passed its tests and can land."}]},
    }


def blocked(state):
    node = {"id": "viewer", "text": "Cloud viewer change fails its tests", "state": state, "how": "The cloud runs worker moves the callers off the deleted store", "item": "", "change": 43421, "since": ""}
    return {"headline": "Two milestones are live.", "because": [], "band": {"kind": "none", "text": ""}, "progress": {"sentence": "", "stops": []},
            "paths": [{"milestone": 0, "nodes": [node], "edges": []}], "calm": "", "work": []}


def test_a_change_keeps_the_coordinators_ruling_after_many_later_rulings():
    facts = digest(model(), {"milestones": []}, None, T0 + dt.timedelta(minutes=30))
    [change] = facts["open_changes"]
    assert change["fixers"] == ["p8a-step5-4", "p8a-step7"]
    assert "its fixers are p8a-step7 and p8a-step5-4" in " ".join(change["coordinator_rulings"])
    assert not any("#43421" in r for r in facts["coordinator_rulings"])


def test_the_writer_reads_the_status_paragraphs_the_owner_sees_above_its_page():
    facts = digest(model(), {"milestones": []}, None, T0)
    assert facts["status_paragraphs"] == ["Main work: The cloud viewer reading chats through the storage engine passed its tests and can land."]


def test_an_active_worker_is_the_fixer_when_no_ruling_names_one():
    m = model()
    m["records"] = [r for r in m["records"] if "#43421" not in r["text"]]
    m["drive"]["prs"][0]["owner"] = "p8a-step7"
    [change] = digest(m, {"milestones": []}, None, T0)["open_changes"]
    assert change["fixers"] == ["p8a-step7"]


def test_a_successor_of_the_changes_worker_is_its_fixer():
    m = model()
    m["records"] = [r for r in m["records"] if "#43421" not in r["text"]]
    m["drive"]["prs"][0]["owner"] = "p8f-replica-6"
    m["workers"].append({"lane": "p8f-replica-7", "at": T0.isoformat(), "prs": []})
    [change] = digest(m, {"milestones": []}, None, T0)["open_changes"]
    assert change["fixers"] == ["p8f-replica-7"]


def test_the_ledger_owner_and_active_lanes_a_ruling_names_are_the_fixers():
    m = model()
    m["records"] = [r for r in m["records"] if "#43421" not in r["text"]] + [record(40, "root", "#43421 was p8f-replica-3's, which finished; landing-desk-3 re-owns its row to p8a-step7.")]
    [change] = digest(m, {"milestones": []}, None, T0)["open_changes"]
    assert change["fixers"] == ["p8a-step7"]
    m["records"] = m["records"][:-1]
    m["drive"]["prs"][0]["owner"] = "p8a-step7"
    [change] = digest(m, {"milestones": []}, None, T0)["open_changes"]
    assert (change["owner"], change["fixers"]) == ("p8a-step7", ["p8a-step7"])


def test_a_blocker_on_a_change_with_fixers_never_reads_no_one_assigned(tmp_path, monkeypatch):
    prompts = []

    def stubborn(name, prompt, schema, system):
        prompts.append(prompt)
        return blocked("nobody")

    monkeypatch.setattr(brief, "run_model", stubborn)
    curator = Curator({"brief_dir": str(tmp_path), "milestones": []}, model, lambda: None)
    curator.dir, curator.path = tmp_path, tmp_path / "brief.json"
    curator.curate()
    assert len(prompts) == 2
    assert "change 43421 is being fixed by p8a-step5-4, p8a-step7" in prompts[1]
    assert json.loads(curator.path.read_text())["paths"][0]["nodes"][0]["state"] == "fixing"


def test_an_open_blocker_or_work_row_without_its_start_time_is_sent_back(tmp_path, monkeypatch):
    prompts = []
    undated = blocked("fixing")
    undated["work"] = [{"item": "viewer", "workers": ["p8a-step7"], "title": "Cloud viewer reads chats", "status": "Fixing", "latest": "Moving the callers off the deleted store.", "unblocks": "viewer", "since": ""}]

    def reply(name, prompt, schema, system):
        prompts.append(prompt)
        return undated

    monkeypatch.setattr(brief, "run_model", reply)
    curator = Curator({"brief_dir": str(tmp_path), "milestones": []}, model, lambda: None)
    curator.dir, curator.path = tmp_path, tmp_path / "brief.json"
    curator.curate()
    assert len(prompts) == 2
    assert "paths.0.nodes.0.since" in prompts[1]
    assert "work.0.since" in prompts[1]


def test_nothing_waits_on_the_owner_when_his_board_asks_nothing(tmp_path, monkeypatch):
    prompts = []
    phantom = blocked("you")
    phantom["work"] = [{"item": "replica-release", "workers": ["release-5"], "title": "Release of the chat replica changes", "status": "Ready for you", "latest": "Waits on your release-order choice.", "unblocks": "", "since": "3:55 PM", "asks": ["card-release-order"]}]

    def reply(name, prompt, schema, system):
        prompts.append(prompt)
        return json.loads(json.dumps(phantom))

    monkeypatch.setattr(brief, "run_model", reply)
    curator = Curator({"brief_dir": str(tmp_path), "milestones": []}, model, lambda: None)
    curator.dir, curator.path = tmp_path, tmp_path / "brief.json"
    curator.curate()
    written = json.loads(curator.path.read_text())
    assert "may not wait on him" in prompts[1]
    assert [n for p in written["paths"] for n in p["nodes"] if n["state"] == "you"] == []
    assert written["work"] == []


def test_a_row_waits_on_the_owner_only_while_its_ask_is_open():
    open_card = {"kind": "card", "card": {"id": "card-gateway", "title": "Should a model call name its run?"}}
    rows = [
        {"item": "gateway", "status": "Ready for you", "asks": ["card-gateway"]},
        {"item": "forks", "status": "Ready for you", "asks": ["43736"]},
        {"item": "viewer", "status": "Stuck", "asks": []},
    ]
    kept = brief.without_phantoms({"paths": [], "work": rows}, brief.owner_asks([open_card]))
    assert [row["item"] for row in kept["work"]] == ["gateway", "viewer"]


def test_a_resolved_blocker_leaves_the_writers_facts():
    m = model()
    m["records"] += [{**record(26, "p8a-step9", "The current app check expects the earlier release.", "blocker"), "seq": 10}, {**record(27, "root", "Fixed by pinning the baseline.", "decision"), "seq": 11, "resolves": 10}]
    facts = digest(m, {"milestones": []}, None, T0 + dt.timedelta(minutes=30))
    assert not any("current app check" in line for line in facts["recent_trouble"])


def test_each_change_carries_the_newest_records_about_it():
    m = model()
    m["records"].append(record(28, "p8a-step7", "#43421 waits on the replay onto dev, due 8:01 PM.", "report"))
    [change] = digest(m, {"milestones": []}, None, T0 + dt.timedelta(minutes=30))["open_changes"]
    assert "waits on the replay onto dev, due 8:01 PM" in change["newest_records"][-1]
    assert change["next"] == ""


def test_a_headline_without_how_long_the_blocker_has_waited_is_sent_back(tmp_path, monkeypatch):
    prompts = []

    def reply(name, prompt, schema, system):
        prompts.append(prompt)
        return blocked("fixing")

    monkeypatch.setattr(brief, "run_model", reply)
    curator = Curator({"brief_dir": str(tmp_path), "milestones": []}, model, lambda: None)
    curator.dir, curator.path = tmp_path, tmp_path / "brief.json"
    curator.curate()
    assert "headline: say the one thing in the way and how long it has waited" in prompts[1]


def test_a_draining_curator_starts_no_new_run_so_a_code_restart_is_not_starved(tmp_path, monkeypatch):
    runs = []
    monkeypatch.setattr(brief, "run_model", lambda *args: runs.append(args))
    curator = Curator({"brief_dir": str(tmp_path), "milestones": []}, model, lambda: None)
    curator.draining = True

    loop = threading.Thread(target=curator.loop, daemon=True)
    loop.start()
    loop.join(timeout=2)

    assert not loop.is_alive()
    assert (runs, curator.running) == ([], False)
