import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "dashboard" / "app"))

from screens import SCREENS, changed_screens, screen_kind, screen_view


OWNER_ASKS = {
    "messages": [
        {"m": 1, "iso": "2026-11-03T03:01:00-08:00", "text": "> first order", "state": "applied", "lanes": ["gateway-fix-1"], "prs": ["48019"], "latest": "Done.", "latest_seq": 1},
        {"m": 2, "iso": "2026-11-03T05:17:00-08:00", "text": "keep going and build", "state": "asked", "lanes": [], "prs": [], "latest": "", "latest_seq": None},
    ],
    "taps": [
        {"m": 3, "iso": "2026-11-03T06:00:00-08:00", "card": "c", "said": "picked A", "question": "Which device?", "pr": "", "state": "answered"},
        {"m": 4, "iso": "2026-11-03T07:00:00-08:00", "card": "pr", "said": "48019 approved at abc", "question": "Merge?", "pr": "48019", "state": "applied"},
    ],
    "store": [
        {"key": "ask/000020", "lane": "progress-desk-2", "line": "LIVE ask/000020", "state": "LIVE", "text": "Owner pick Oct 29 4:02 PM on card watchdog: Build."},
        {"key": "ask/000025", "lane": "ci-speed-1", "line": "LOST ask/000025", "state": "LOST", "text": "Owner 4:38 AM Oct 30: cut the job."},
    ],
}


def view(parts=1, prs=0):
    record = {"seq": 1, "lane": "gateway-fix-1", "kind": "report", "at": "2026-11-03T11:00:00Z", "text": "Pool limits hold."}
    return {
        "needs": [
            {"kind": "card", "card": {"id": "pick-1", "title": "Which device?"}, "why": "The board asks you this."},
            {"kind": "prs", "key": "abc", "group": "Chat readers", "why": "The merge board waits on your tap."},
        ],
        "brief": {"headline": "Release is live.", "because": [], "progress": {"sentence": "Four of eight"}},
        "pages": {
            "plan": {"summary": "Four live.", "milestones": [], "parts": [{"id": f"p{n}", "name": f"Step {n}", "state": "stuck", "label": "Stuck on a port"} for n in range(parts)]},
            "work": {"running": [{"item": "w1", "title": "Gateway", "latest": "Fix lands next.", "status": "Landing", "workers": ["gateway-fix-2"]}], "finished": [{"item": "w2", "title": "Why", "result": "Explained.", "outcome": "delivered", "workers": []}]},
            "history": {"summary": "Quiet.", "entries": [{"kind": "landed", "title": "Dream nights stop retrying", "detail": "A refusal ends the night."}]},
        },
        "step_records": {"p0": {"wrong": "", "lanes": ["gateway-fix-1"], "prs": []}},
        "status_tables": [{"id": "gateway", "rows": [{"step": "Gateway pool", "state": "Holding", "lanes": []}, {"step": "Replica", "state": "Waits on your pick", "lanes": []}, {"step": "Signal", "state": "Building", "blocks": "Nobody owns it", "lanes": []}, {"step": "Readers", "state": "Done", "lanes": []}]}],
        "owner_asks": OWNER_ASKS,
        "status_now": {"at": "x", "paragraphs": []},
        "board": {"url": "http://b", "rows": [{"number": 7, "title": "Readers", "blocker": "Your approval.", "group": "Chat readers", "verdict": None}], "cards": [{"id": "c1", "title": "Refuse or rebuild?", "recommended": "A"}]},
        "records": [record],
        "prs": {str(n): {"state": "open", "title": "x" * 200} for n in range(prs)},
    }




def test_every_screen_has_the_one_shape_the_web_and_the_phone_read():
    for name in SCREENS:
        built = screen_view(name, view(), 9)
        assert built["screen"] == name and built["version"] == 9
        assert built["rows"], name
        assert isinstance(built["head"], dict) and isinstance(built["filters"], list)
        for entry in built["rows"]:
            assert {"id", "title", "status", "subtitle", "link", "facets", "records"} <= entry.keys()
            assert entry["status"] in {"needs_you", "doing", "waiting", "stuck", "done", "later", "info"}
            assert entry["link"].startswith("#/")


def test_status_rows_filter_by_feature_and_open_to_their_records():
    built = screen_view("status", view(), 1)
    assert {"facet": "feature", "values": ["plan", "gateway"]} in built["filters"]
    stuck = next(entry for entry in built["rows"] if entry["facets"]["feature"] == "plan")
    assert stuck["status"] == "stuck" and stuck["records"][0]["text"] == "Pool limits hold."
    assert next(entry for entry in built["rows"] if entry["facets"]["feature"] == "gateway")["link"] == "#/status/gateway?row=st-gateway-0"


def test_owner_table_rows_read_their_status_from_their_words():
    rows = {entry["title"]: entry["status"] for entry in screen_view("status", view(), 1)["rows"]}
    assert rows == {"Step 0": "stuck", "Gateway pool": "doing", "Replica": "needs_you", "Signal": "stuck", "Readers": "done"}


def test_asks_put_what_is_live_first_then_the_newest_and_say_where_each_lives():
    rows = {entry["id"]: entry for entry in screen_view("asks", view(), 1)["rows"]}
    assert list(rows) == ["ask-ask/000020", "ask-approved-applied", "ask-m3", "ask-m2", "ask-m1", "ask-ask/000025"]
    assert rows["ask-ask/000020"]["title"] == "Build." and rows["ask-ask/000020"]["facets"] == {"state": "live", "where": "progress-desk-2"}
    assert rows["ask-ask/000025"]["status"] == "waiting" and rows["ask-ask/000025"]["subtitle"] == "Asked, no progress recorded"
    assert rows["ask-m1"]["status"] == "done" and rows["ask-m1"]["subtitle"] == "Applied, Nov 3 3:01 AM" and rows["ask-m1"]["facets"]["where"] == "PR 48019"
    assert rows["ask-m1"]["records"][0]["text"] == "Pool limits hold."
    assert rows["ask-m1"]["title"] == "first order"
    assert rows["ask-m2"]["facets"] == {"state": "asked", "where": "Chat"}
    assert rows["ask-approved-applied"]["title"] == "1 approved, landed"


def test_a_screen_stays_small_however_many_pull_requests_the_drive_holds():
    small = len(json.dumps(screen_view("status", view(prs=1), 1)))
    large = len(json.dumps(screen_view("status", view(prs=1000), 1)))
    assert small == large


def test_status_bytes_grow_with_its_own_rows_and_one_fetch_serves_them_all():
    one = screen_view("status", view(parts=1), 1)
    thousand = screen_view("status", view(parts=1000), 1)
    assert len(thousand["rows"]) == len(one["rows"]) + 999
    assert len(json.dumps(thousand)) < 400 * len(thousand["rows"])


def test_the_stream_names_only_the_screens_that_changed():
    past = {screen_kind(name): {"rows": b"a", "version": b"1"} for name in SCREENS}
    current = {screen_kind(name): {"rows": b"a", "version": b"2"} for name in SCREENS}
    assert changed_screens(past, current) == []
    current[screen_kind("asks")] = {"rows": b"b", "version": b"2"}
    assert changed_screens(past, current) == ["asks"]
    assert changed_screens(None, current) == list(SCREENS)
