import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "dashboard" / "app"))

from screens import SCREENS, changed_screens, screen_kind, screen_view


def view(steps=1, prs=0):
    record = {"seq": 1, "lane": "gateway-fix-1", "kind": "report", "at": "2026-11-03T11:00:00Z", "text": "Pool limits hold."}
    return {
        "needs": [
            {"kind": "card", "card": {"id": "pick-1", "title": "Which device?"}, "why": "The board asks you this."},
            {"kind": "prs", "key": "abc", "group": "Chat readers", "why": "The merge board waits on your tap."},
        ],
        "brief": {"headline": "Release is live.", "because": [], "progress": {"sentence": "Four of eight"}},
        "pages": {
            "plan": {"summary": "Four live.", "milestones": [], "parts": [{"id": "p0", "name": "Base", "state": "you", "label": "Waits on you"}]},
            "work": {"running": [{"item": "w1", "title": "Gateway", "latest": "Fix lands next.", "status": "Landing", "workers": ["gateway-fix-2"]}], "finished": [{"item": "w2", "title": "Why", "result": "Explained.", "outcome": "delivered", "workers": []}]},
            "history": {"summary": "Quiet.", "entries": [{"kind": "landed", "title": "Dream nights stop retrying", "detail": "A refusal ends the night."}]},
        },
        "steps": [{"title": f"Step {n}", "tone": "bad", "state": "Stuck on a port", "lanes": ["gateway-fix-1"]} for n in range(steps)],
        "status_tables": [{"id": "gateway", "rows": [{"step": "Gateway pool", "tone": "ok", "state": "Holding", "lanes": []}]}],
        "status_now": {"at": "x", "paragraphs": []},
        "board": {"url": "http://b", "rows": [{"number": 7, "title": "Readers", "blocker": "Your approval.", "group": "Chat readers", "verdict": None}], "cards": [{"id": "c1", "title": "Refuse or rebuild?", "recommended": "A"}]},
        "records": [record],
        "prs": {str(n): {"state": "open", "title": "x" * 200} for n in range(prs)},
    }


LEDGER = """| id | time | message | routed | state |
|---|---|---|---|---|
| m1 | Nov 3 3:01 AM | first order | 359389 | addressed |
| m2 | Nov 3 5:17 AM | keep going<br>and build |  | open |
"""


def test_every_screen_has_the_one_shape_the_web_and_the_phone_read():
    for name in SCREENS:
        built = screen_view(name, view(), LEDGER, 9)
        assert built["screen"] == name and built["version"] == 9
        assert built["rows"], name
        assert isinstance(built["head"], dict) and isinstance(built["filters"], list)
        for entry in built["rows"]:
            assert {"id", "title", "status", "subtitle", "link", "facets", "records"} <= entry.keys()
            assert entry["status"] in {"needs_you", "doing", "waiting", "stuck", "done", "later", "info"}
            assert entry["link"].startswith("#/")


def test_status_rows_filter_by_feature_and_open_to_their_records():
    built = screen_view("status", view(), LEDGER, 1)
    assert {"facet": "feature", "values": ["plan", "gateway"]} in built["filters"]
    stuck = next(entry for entry in built["rows"] if entry["facets"]["feature"] == "plan")
    assert stuck["status"] == "stuck" and stuck["records"][0]["text"] == "Pool limits hold."
    assert next(entry for entry in built["rows"] if entry["facets"]["feature"] == "gateway")["link"] == "#/status/gateway?row=st-gateway-0"


def test_asks_list_the_owner_ledger_newest_first_with_their_state():
    rows = screen_view("asks", view(), LEDGER, 1)["rows"]
    assert [entry["id"] for entry in rows] == ["ask-m2", "ask-m1"]
    assert rows[0]["facets"]["state"] == "asked" and rows[1]["facets"] == {"state": "applied", "record": "359389"}
    assert rows[0]["title"] == "keep going and build"


def test_a_screen_stays_small_however_many_pull_requests_the_drive_holds():
    small = len(json.dumps(screen_view("status", view(prs=1), LEDGER, 1)))
    large = len(json.dumps(screen_view("status", view(prs=1000), LEDGER, 1)))
    assert small == large


def test_status_bytes_grow_with_its_own_rows_and_one_fetch_serves_them_all():
    one = screen_view("status", view(steps=1), LEDGER, 1)
    thousand = screen_view("status", view(steps=1000), LEDGER, 1)
    assert len(thousand["rows"]) == len(one["rows"]) + 999
    assert len(json.dumps(thousand)) < 400 * len(thousand["rows"])


def test_the_stream_names_only_the_screens_that_changed():
    past = {screen_kind(name): {"rows": b"a", "version": b"1"} for name in SCREENS}
    current = {screen_kind(name): {"rows": b"a", "version": b"2"} for name in SCREENS}
    assert changed_screens(past, current) == []
    current[screen_kind("asks")] = {"rows": b"b", "version": b"2"}
    assert changed_screens(past, current) == ["asks"]
    assert changed_screens(None, current) == list(SCREENS)
