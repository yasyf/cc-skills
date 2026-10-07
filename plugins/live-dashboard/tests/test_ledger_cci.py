from __future__ import annotations

from conftest import FIXTURES, LEDGER, fake, fixture
from livedash.components import cci, ledger, notes


def records(params):
    found = fixture("cci-records.json")
    kinds = params["kind"] if isinstance(params["kind"], list) else [params["kind"]]
    return [record for record in found if record["kind"] in kinds and record["seq"] > params.get("since", 0)]


def test_holds_join_the_ledger_and_the_holds_file(tmp_path):
    table = ledger.held_prs(fake(tmp_path, replies={LEDGER: fixture("ledger-rows.json")}), ledger="L1", holds=FIXTURES / "holds.md")
    assert [(row["key"], row["file"], row["since"]) for row in table.rows] == [("101", "line 1, line 2", "2026-10-06T20:21:59Z"), ("102", "line 2", "2026-10-06T20:21:59Z")]
    assert table.rows[0]["reason"] == "owner standing: no PR lands until owner reviews"


def test_a_lifted_hold_line_names_nothing(tmp_path):
    assert [line["prs"] for line in ledger.hold_lines(FIXTURES / "holds.md")] == [[], ["101", "102"]]


def test_asks_drop_delivered_and_answered_rows_and_join_cci_asks_to_the_owner(tmp_path):
    ctx = fake(tmp_path, replies={LEDGER: fixture("ledger-rows.json")}, cci_replies={"digest": fixture("cci-digest.json")})
    table = ledger.asks(ctx, ledger="L1")
    assert [(row["key"], row["state"]) for row in table.rows] == [("ask/000002", "open"), ("ask/000004", "in-pr"), ("cci:31", "open")]
    assert table.rows[2]["accept"] == "a cap is chosen"


def test_open_records_mark_what_moved_on_and_drop_what_was_closed(tmp_path):
    ctx = fake(tmp_path, replies={LEDGER: fixture("ledger-rows.json")}, cci_replies={"digest": fixture("cci-digest.json"), "records": records})
    table = cci.open_records(ctx, ledger="L1")
    assert [(row["cite"], row["stale"]) for row in table.rows] == [("cci:10", f"#101 moved to {'a' * 10}"), ("cci:11", "ready #20 came after"), ("cci:12", None)]
    assert [row["cite"] for row in cci.open_records(ctx, ledger="L1", stale_only=True).rows] == ["cci:10", "cci:11"]


def test_the_close_action_posts_a_resolving_done_record(tmp_path):
    ctx = fake(tmp_path, replies={("cci", "post"): {"seq": 90}})
    assert cci.close_record(ctx, {"cite": "cci:10", "stale": "#101 moved"}, "") == "cci #90 closes cci:10"
    assert ctx.calls[-1][ctx.calls[-1].index("--topic") + 1] == "resolved:cci:10"
    assert ctx.calls[-1][ctx.calls[-1].index("--text") + 1] == "closed cci:10: #101 moved"


def test_lanes_group_by_activity_and_flag_a_stood_down_lane_holding_a_worktree(tmp_path):
    listing = "worktree /checkout\nHEAD abc\n\nworktree /trees/lane-old\nHEAD def\n"
    ctx = fake(tmp_path, replies={("git", "-C", "/checkout", "worktree", "list"): listing}, cci_replies={"lanes": fixture("cci-lanes.json")})
    table = cci.lanes(ctx)
    assert [(row["lane"], row["group"], row["tone"]) for row in table.rows] == [("lane-one", "active", "ok"), ("lane-quiet", "idle over 1h", "muted"), ("lane-old", "holds a worktree", "bad")]
    assert table.group_by == "group"


def test_rulings_merge_owner_answers_with_root_decisions_and_their_lanes(tmp_path):
    answers = [{"id": "646bc30" + "0" * 33, "title": "Where do the routes live?", "body": "Go runtime-v2\nOptions: Go | TS", "updated_at": "2026-10-06T17:21:09Z"}]
    decided = [
        {"seq": 5, "kind": "decision", "lane": "root", "to": ["mem-12-bench"], "at": "2026-10-06T18:46:08Z", "text": "V7 must set the floor"},
        {"seq": 6, "kind": "decision", "lane": "mem-08", "to": ["design-links"], "at": "2026-10-06T19:10:09Z", "text": "PR implements DQ6"},
    ]
    ctx = fake(tmp_path, replies={("ccn", "-R", "/checkout", "answer", "list"): answers}, cci_replies={"records": lambda params: decided if "since" not in params else []})
    feed = notes.rulings(ctx, program="iris-chat-memory")
    assert [(entry.actor, entry.text, entry.cite) for entry in feed.entries] == [
        ("root → mem-12-bench", "V7 must set the floor", "cci:5"),
        ("owner", "Where do the routes live? → Go runtime-v2", "ccn:646bc30"),
    ]
