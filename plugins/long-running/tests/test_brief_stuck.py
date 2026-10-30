from brief import now_checks, now_settle

FACTS = {"waiting_on_owner": [], "open_changes": [], "milestone_state": [{"state": "progress", "label": "Testing"}]}
BLOCKED = {**FACTS, "milestone_state": [{"state": "broken", "label": "Blocked"}]}


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
    now_settle(held, BLOCKED)
    assert held["progress"]["stops"][0]["state"] == "broken"


def test_the_computed_milestone_state_wins_over_the_summary():
    held = brief([{"milestone": 0, "nodes": [{"id": "n", "state": "fixing", "since": "5:51 PM", "how": "the Mac worker reruns the gate by 6:40 PM"}], "edges": []}])
    now_settle(held, FACTS)
    assert held["progress"]["stops"][0] == {"state": "progress", "label": "Testing", "why": ""}
