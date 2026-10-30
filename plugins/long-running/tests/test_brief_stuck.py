from brief import now_checks, now_settle

FACTS = {"waiting_on_owner": [], "open_changes": []}


def brief(paths):
    return {"headline": "Shipping", "progress": {"sentence": "", "stops": [{"state": "broken", "label": "Stuck in testing", "why": "checks failed"}]}, "paths": paths, "work": []}


def test_a_stuck_milestone_without_an_open_blocker_is_sent_back_and_never_shown_stuck():
    alone = brief([])
    assert any(p.startswith("progress.stops.0.state") for p in now_checks(alone, FACTS))
    now_settle(alone, FACTS)
    assert alone["progress"]["stops"][0]["state"] == "progress"


def test_a_stuck_milestone_with_an_open_blocker_stands():
    held = brief([{"milestone": 0, "nodes": [{"id": "n", "state": "fixing", "since": "5:51 PM", "how": "the Mac worker reruns the gate by 6:40 PM"}], "edges": []}])
    assert not any(p.startswith("progress.stops.0.state") for p in now_checks(held, FACTS))
    now_settle(held, FACTS)
    assert held["progress"]["stops"][0]["state"] == "broken"
