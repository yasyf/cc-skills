from __future__ import annotations

from conftest import FIXTURES, LEDGER, fake, fixture
from livedash.components import cci, ledger, notes, orca


def records(params, extra=()):
    found = [*fixture("cci-records.json"), *extra]
    kinds = params.get("kind")
    kinds = None if kinds is None else kinds if isinstance(kinds, list) else [kinds]
    return [record for record in found if (kinds is None or record["kind"] in kinds) and record["seq"] > params.get("since", 0)]


def test_holds_join_the_ledger_and_the_holds_file(tmp_path):
    table = ledger.held_prs(fake(tmp_path, replies={LEDGER: fixture("ledger-rows.json")}), ledger="L1", holds=FIXTURES / "holds.md")
    assert [(row["key"], row["file"], row["since"]) for row in table.rows] == [("101", "line 1, line 2", "2026-10-06T20:21:59Z"), ("102", "line 2", "2026-10-06T20:21:59Z")]
    assert table.rows[0]["reason"] == "owner standing: no PR lands until owner reviews"


def test_a_lifted_hold_line_names_nothing(tmp_path):
    assert [line["prs"] for line in ledger.hold_lines(FIXTURES / "holds.md")] == [[], ["101", "102"]]


def test_asks_drop_delivered_and_answered_rows_and_join_cci_asks_to_the_owner(tmp_path):
    ctx = fake(tmp_path, replies={LEDGER: fixture("ledger-rows.json")}, cci_replies={"digest": fixture("cci-digest.json"), "records": records})
    table = ledger.asks(ctx, ledger="L1")
    assert [(row["key"], row["state"]) for row in table.rows] == [("ask/000002", "open"), ("ask/000004", "in-pr"), ("cci:31", "open")]
    assert table.rows[2]["accept"] == "a cap is chosen"


def test_an_ask_whose_ledger_state_settled_it_leaves_and_a_cci_ask_leaves_once_answered(tmp_path):
    rows = [row | {"fields": row["fields"] | {"state": "live", "state_note": "landed #28909"}} if row["key"] == "ask/000002" else row for row in fixture("ledger-rows.json")]
    reply = {"seq": 32, "lane": "root", "kind": "decision", "at": "2026-10-06T22:00:00Z", "text": "cap is 40", "re": 31, "refs": {}, "fields": {}}
    ctx = fake(tmp_path, replies={LEDGER: rows}, cci_replies={"digest": fixture("cci-digest.json"), "records": lambda params: records(params, [reply])})
    assert [row["key"] for row in ledger.asks(ctx, ledger="L1").rows] == ["ask/000004"]


def test_open_records_mark_what_moved_on_and_drop_what_was_closed(tmp_path):
    ctx = fake(tmp_path, replies={LEDGER: fixture("ledger-rows.json")}, cci_replies={"digest": fixture("cci-digest.json"), "records": records})
    table = cci.open_records(ctx, ledger="L1")
    assert [(row["cite"], row["stale"]) for row in table.rows] == [("cci:10", f"#101 moved to {'a' * 10}"), ("cci:11", "ready #20 came after"), ("cci:12", None)]
    assert [row["cite"] for row in cci.open_records(ctx, ledger="L1", stale_only=True).rows] == ["cci:10", "cci:11"]


def test_open_records_drop_answered_landed_and_expired_records_but_keep_old_holds(tmp_path):
    digest = fixture("cci-digest.json") | {"open_holds": [{"seq": 5, "lane": "root", "kind": "hold", "at": "2026-10-04T00:00:00Z", "text": "HOLD escape-hatch", "refs": {}, "fields": {}}]}
    digest["open_blockers"] = [*digest["open_blockers"], {"seq": 6, "lane": "desk", "kind": "blocker", "at": "2026-10-05T00:00:00Z", "text": "an old blocker", "refs": {}, "fields": {}}]
    rows = [row | {"fields": row["fields"] | {"state": "landed"}} if row["key"] == "103" else row for row in fixture("ledger-rows.json")]
    answer = {"seq": 22, "lane": "lane-one", "kind": "note", "at": "2026-10-06T20:00:00Z", "text": "restacked", "re": 11, "refs": {}, "fields": {}}
    ctx = fake(tmp_path, replies={LEDGER: rows}, cci_replies={"digest": digest, "records": lambda params: records(params, [answer])})
    table = cci.open_records(ctx, ledger="L1")
    assert [row["cite"] for row in table.rows] == ["cci:10", "cci:5"]
    assert table.note == "Not listed: 1 blockers and defects older than 24h with no answer."


def test_the_close_action_posts_a_resolving_done_record(tmp_path):
    ctx = fake(tmp_path, replies={("cci", "post"): {"seq": 90}})
    assert cci.close_record(ctx, {"cite": "cci:10", "stale": "#101 moved"}, "") == "cci #90 closes cci:10"
    assert ctx.calls[-1][ctx.calls[-1].index("--topic") + 1] == "resolved:cci:10"
    assert ctx.calls[-1][ctx.calls[-1].index("--text") + 1] == "closed cci:10: #101 moved"


def test_lanes_list_only_active_lanes_and_stood_down_lanes_holding_a_worktree(tmp_path):
    listing = "worktree /checkout\nHEAD abc\n\nworktree /trees/lane-old\nHEAD def\n"
    ctx = fake(tmp_path, replies={("git", "-C", "/checkout", "worktree", "list"): listing}, cci_replies={"lanes": fixture("cci-lanes.json")})
    table = cci.lanes(ctx)
    assert [(row["lane"], row["group"], row["tone"]) for row in table.rows] == [("lane-one", "active", "ok"), ("lane-old", "holds a worktree", "bad")]
    assert (table.group_by, table.note) == ("group", "Not listed: 1 idle over 1h, 0 stood down.")


def test_lanes_name_live_orca_workers_needing_attention_and_skip_settled_ones(tmp_path):
    def worker(dispatch, task, status, verdict):
        return {"dispatchId": dispatch, "taskId": task, "dispatchStatus": status, "projection": {"liveness": {"verdict": verdict}, "attention": {"requiresAction": True, "categories": ["input"]}}}

    workers = {"result": {"workers": [worker("ctx_1", "task_1", "dispatched", "live"), worker("ctx_2", "task_2", "completed", "live"), worker("ctx_3", "task_3", "dispatched", "unverifiable")], "page": {}}}
    tasks = {"result": {"tasks": [{"id": "task_1", "display_name": "merge-walker"}, {"id": "task_2", "display_name": "done-lane"}, {"id": "task_3", "display_name": "ghost"}]}}
    ctx = fake(tmp_path, facts={"orca_run": "run_1"}, replies={("git", "-C", "/checkout", "worktree", "list"): "", ("orca", "orchestration", "worker-list"): workers, ("orca", "orchestration", "task-list"): tasks}, cci_replies={"lanes": []})
    assert [(row["lane"], row["group"], row["text"]) for row in cci.lanes(ctx).rows] == [("merge-walker", "needs attention", "input")]


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


def test_the_orca_card_lists_only_tasks_whose_worker_is_live(tmp_path):
    def worker(task, status, verdict, attention=()):
        return {"dispatchId": f"ctx_{task}", "taskId": task, "dispatchStatus": status, "projection": {"liveness": {"verdict": verdict}, "attention": {"requiresAction": bool(attention), "categories": list(attention)}}}

    workers = {"result": {"workers": [worker("t1", "dispatched", "live", ["input"]), worker("t2", "dispatched", "unverifiable"), worker("t3", "completed", "live")], "page": {}}}
    tasks = {"result": {"tasks": [
        {"id": "t1", "display_name": "merge-walker", "status": "dispatched", "created_at": "2026-10-07 00:10:00"},
        {"id": "t2", "display_name": "ghost", "status": "dispatched", "created_at": "2026-10-02 12:00:00"},
        {"id": "t3", "display_name": "finished", "status": "completed", "created_at": "2026-10-06 12:00:00"},
        {"id": "t4", "display_name": "never-ran", "status": "ready", "created_at": "2026-09-30 04:00:00"},
    ]}}
    ctx = fake(tmp_path, replies={("orca", "orchestration", "worker-list"): workers, ("orca", "orchestration", "task-list"): tasks})
    table = orca.orca(ctx, orca_run="run_1")
    assert [(row["name"], row["attention"], row["at"]) for row in table.rows] == [("merge-walker", "input", "2026-10-07T00:10:00Z")]
    assert table.note == "Not listed: 2 open tasks without a live worker."
