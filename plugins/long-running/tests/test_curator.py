import datetime as dt
import json

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
        "drive": {"prs": [{"number": 43421, "state": "failing", "worker": "p8f-replica-3", "next": "p8f-replica-3 fixes the tests."}]},
        "prs": {"43421": {"title": "Cloud viewer reads chats through the replica"}},
        "now": {"releases": []},
        "steps": [],
        "needs": [],
        "words": [],
        "story": {},
    }


def blocked(state):
    node = {"id": "viewer", "text": "Cloud viewer change fails its tests", "state": state, "how": "The cloud runs worker moves the callers off the deleted store", "item": "", "change": 43421, "since": ""}
    return {"headline": "Two milestones are live.", "because": [], "band": {"kind": "none", "text": ""}, "progress": {"sentence": "", "stops": []},
            "paths": [{"milestone": 0, "nodes": [node], "edges": []}], "calm": "", "work": []}


def test_a_change_keeps_the_coordinators_ruling_after_many_later_rulings():
    facts = digest(model(), {"milestones": []}, None, T0 + dt.timedelta(minutes=30))
    [change] = facts["open_changes"]
    assert change["fixers"] == ["p8a-step5-4", "p8a-step7"]
    assert "its fixers are p8a-step7 and p8a-step5-4" in change["coordinator_ruling"]
    assert not any("#43421" in r for r in facts["coordinator_rulings"])


def test_an_active_worker_is_the_fixer_when_no_ruling_names_one():
    m = model()
    m["records"] = [r for r in m["records"] if "#43421" not in r["text"]]
    m["drive"]["prs"][0]["worker"] = "p8a-step7"
    [change] = digest(m, {"milestones": []}, None, T0)["open_changes"]
    assert change["fixers"] == ["p8a-step7"]


def test_a_blocker_on_a_change_with_fixers_never_reads_no_one_assigned(tmp_path, monkeypatch):
    prompts = []

    def stubborn(name, prompt, schema):
        prompts.append(prompt)
        return blocked("nobody")

    monkeypatch.setattr(brief, "run_model", stubborn)
    curator = Curator({"brief_dir": str(tmp_path), "milestones": []}, model, lambda: None)
    curator.dir, curator.path = tmp_path, tmp_path / "brief.json"
    curator.curate()
    assert len(prompts) == 2
    assert "change 43421 is being fixed by p8a-step5-4, p8a-step7" in prompts[1]
    assert json.loads(curator.path.read_text())["paths"][0]["nodes"][0]["state"] == "fixing"
