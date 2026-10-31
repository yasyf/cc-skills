import contextlib
import datetime as dt
import itertools
import gzip
import json
import os
import sqlite3
import subprocess
import threading
import urllib.request
from http.server import ThreadingHTTPServer
import queue
import urllib.error
import urllib.parse

import pytest
from holds import Entry
from pages import stamp
from review_assistant import ReviewAssistant
import server
from server import QUEUED_NEXT, Drive, drive_prs, drive_workers, journeys, needs, open_findings, parse_plan, plan_prs, step_rows, release_path, paths_current, hold_reasons, verdict_state, with_conflicts

NOW = dt.datetime(2026, 10, 27, 8, 0, tzinfo=dt.timezone.utc)
TREES = ["New tree", "Old tree", "Shared"]
PARTS = [{"key": "agent", "scopes": ["sand"]}, {"key": "api", "scopes": ["api"]}, {"key": "tools", "scopes": []}]

DOC = """id: a5262e7
updated: 2026-10-27T06:30:00Z

# Progress

## Ordered plan of action (what lands, in what order)
1. **Now, no owner action needed.** The gateway batch (#42712, #42714) lands, then #43200.
2. **Taps he still owes:** the credential stack (#42788-#42790, #42737).
3. Step 11 cutover.

## Lanes live
- #49999 is not a plan step.
"""


@pytest.mark.parametrize(
    ("text", "numbers"),
    [
        ("#42712 and #42714", [42712, 42714]),
        ("(#42788-#42790, #42737)", [42788, 42789, 42790, 42737]),
        ("#42712 again #42712", [42712]),
        ("no pull requests", []),
    ],
)
def test_plan_prs_expands_ranges_once(text, numbers):
    assert plan_prs(text) == numbers


def test_parse_plan_reads_only_the_ordered_section():
    updated, steps = parse_plan(DOC)
    assert updated == "2026-10-27T06:30:00Z"
    assert [step["title"] for step in steps] == ["Now, no owner action needed", "Taps he still owes", "Step 11 cutover."]
    assert steps[0]["prs"] == [42712, 42714, 43200]
    assert steps[1]["prs"] == [42788, 42789, 42790, 42737]
    assert steps[2]["text"] == ""


def test_parse_plan_skips_a_doc_without_the_section():
    assert parse_plan("id: x\nupdated: now\n\n## Lanes live\n1. **A.** b\n") is None


def test_read_board_finds_a_verdict_keyed_by_the_bare_pr_number():
    row = {"number": 43211, "head": "8242887", "title": "T", "change": "C.", "plan": "P.", "risk": "R.", "review": "V.", "blocker": "B."}
    board = {
        "doc": {"blocks": [{"id": "card-merge", "type": "card", "children": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "title": "G", "summary": "S.", "prs": [row]}]}]}]},
        "interactions": {"triage": {"merge-prs": {"43211": {"verdict": "approved", "head": "8242887"}}}, "choices": {}},
    }
    rows = Drive({"board_url": "http://board/p/x"}, None).read_board(board)["rows"]
    assert rows[0]["block"] == "merge-prs"
    assert rows[0]["verdict"] == {"verdict": "approved", "head": "8242887"}



def test_the_board_still_reads_after_he_submits_it(monkeypatch):
    row = {"number": 43211, "head": "8242887", "title": "T", "change": "C.", "plan": "P.", "risk": "R.", "review": "V.", "blocker": "B."}
    reduced = {
        "doc": {"blocks": [{"id": "card-merge", "type": "card", "children": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "title": "G", "summary": "S.", "prs": [row]}]}]}]},
        "interactions": {"triage": {"merge-prs": {"43211": {"verdict": "approved", "head": "8242887"}}}, "choices": {}},
    }
    since_submit = {"since": 3200, "through": 3240, "interactions": [{"seq": 3234, "type": "choice.selected", "blockId": "b", "payload": {"optionIds": ["a"]}}]}
    monkeypatch.setattr(server, "run", lambda argv: json.dumps(reduced if "--all" in argv else since_submit))
    monkeypatch.setattr(Drive, "store_seq", lambda self: 3240)
    monkeypatch.setattr(Drive, "answer_times", lambda self: {})
    rows = Drive({"board_url": "http://board/p/x", "board_session": "s"}, None).board()["rows"]
    assert rows[0]["verdict"] == {"verdict": "approved", "head": "8242887"}

def card(block_id, *children, status=None):
    return {"id": block_id, "type": "card", **({"status": status} if status else {}), "children": [{"id": child_id, "type": kind} for kind, child_id in children]}


def board(*cards, retired=(), **answers):
    interactions = {"triage": {}, "choices": {}, "decisions": {}, "inputs": {}, "feedback": {}}
    for kind, entries in answers.items():
        interactions[kind] = entries
    return {"doc": {"blocks": list(cards)}, "interactions": interactions, "retired": list(retired)}


@pytest.mark.parametrize(
    ("store", "open_ids"),
    [
        (board(card("ask", ("choice", "pick"))), ["ask"]),
        (board(card("ask", ("choice", "pick")), choices={"pick": {"optionIds": ["a"]}}), []),
        (board(card("ask", ("choice", "pick")), choices={"pick": {"optionIds": [], "other": "both views"}}), []),
        (board(card("plan", ("approval", "ok")), decisions={"ok": {"verdict": "approved"}}), []),
        (board(card("shots", ("image", "phone"), status="open")), ["shots"]),
        (board(card("shots", ("image", "phone"), status="open"), feedback={"shots": [{"text": "evolutionary"}]}), []),
        (board(card("ask", ("choice", "pick")), retired=["ask"]), []),
        (board(card("notes", ("markdown", "body"))), []),
        (board(card("retro", ("choice", "p1"), ("choice", "p2")), choices={"p1": {"optionIds": ["a"]}}), ["retro"]),
        (board(card("retro", ("choice", "p1"), ("approval", "ok")), choices={"p1": {"optionIds": ["a"]}}, decisions={"ok": {"verdict": "rejected"}}), []),
    ],
)
def test_a_question_he_answered_leaves_the_board_count(store, open_ids):
    assert [c["id"] for c in Drive({"board_url": "http://board/p/x"}, None).read_board(store)["cards"]] == open_ids


def test_a_card_he_answered_reads_as_his_picks_at_the_time_of_his_last_answer(monkeypatch):
    release = card("card-release", ("choice", "when"), ("approval", "ok"), ("input", "why"))
    release["children"][0]["options"] = [{"id": "now", "label": "Release now"}, {"id": "later", "label": "Tomorrow"}, {"id": "o", "label": "Other"}]
    store = board(release, card("card-open", ("choice", "pick")), choices={"when": {"optionIds": ["now"], "other": "after the smoke run"}}, decisions={"ok": {"verdict": "approved"}}, inputs={"why": {"text": "ship it"}})
    monkeypatch.setattr(server, "run", lambda argv: json.dumps(store))
    monkeypatch.setattr(Drive, "store_seq", lambda self: 7)
    monkeypatch.setattr(Drive, "answer_times", lambda self: {"when": "2026-10-30T12:53:00+00:00", "ok": "2026-10-30T12:59:00+00:00", "why": "2026-10-30T12:54:00+00:00", "pick": "2026-10-30T11:00:00+00:00"})

    out = Drive({"board_url": "http://board/p/x", "board_session": "s"}, None).board()

    assert out["answered"] == {"card-release": {"picked": "Release now, after the smoke run; approved; ship it", "at": "2026-10-30T12:59:00+00:00"}}
    assert [c["id"] for c in out["cards"]] == ["card-open"]


def test_a_step_or_milestone_still_waiting_on_an_answered_card_a_minute_later_is_flagged_once_to_its_writer():
    answered = {"card-release": {"picked": "Release now", "at": "2026-10-27T07:58:30+00:00"}, "card-fresh": {"picked": "Yes", "at": "2026-10-27T07:59:30+00:00"}}
    rows = [{"release": "R4", "picked": [{**answered["card-release"], "card": "card-release", "seq": 210000, "lane": "progress-desk-2"}, {**answered["card-fresh"], "card": "card-fresh", "seq": 210001, "lane": "progress-desk-2"}]}]
    steps = [{**step([1], step_id="cloud"), "blocker": "You answer the card on your board", "card": "card-release"}, {**step([2], step_id="mac"), "card": "card-fresh"}]
    drive = {"prs": [], "tracked": [1, 2]}

    assert [row["blocker"] for row in step_rows(steps, drive, [], answered)] == [None, None]
    flagged = server.stale_waits(rows, steps, answered, NOW)
    assert [(key, lanes) for key, lanes, _ in flagged] == [("blocker:210000:card-release", ("progress-desk-2",)), ("step:cloud:card-release", ())]
    assert "answered at 12:58 AM Oct 27 (Release now)" in flagged[0][2]

    posted = []
    dashboard = Drive({"board_url": "http://board/p/x", "drive": "d", "keeper_lane": "keeper", "defects_to": ["keeper", "root"]}, None)
    dashboard.flag = lambda text, lanes: posted.append(lanes)
    dashboard.flag_stale(rows, steps, answered, NOW)
    dashboard.flag_stale(rows, steps, answered, NOW)
    assert posted == [("progress-desk-2",), ()]


def test_a_card_keeps_his_earlier_picks_and_offers_the_next_one():
    retro = card("retro", ("record", "why"), ("choice", "p1"), ("choice", "p2"))
    retro["children"][1]["options"] = [{"id": "a", "label": "Check first (Recommended)", "recommended": True}, {"id": "o", "label": "Other"}]
    retro["children"][2]["options"] = [{"id": "b", "label": "Stop on red"}, {"id": "c", "label": "Keep going"}]

    out = Drive({"board_url": "http://board/p/x"}, None).read_board(board(retro, choices={"p1": {"optionIds": ["a"]}}))["cards"][0]

    assert out["choice"] == "p2"
    assert [o["id"] for o in out["options"]] == ["b", "c"]
    assert [(part["kind"], part.get("answer")) for part in out["parts"]] == [("record", None), ("choice", {"optionIds": ["a"]}), ("choice", None)]
    assert [o["id"] for o in out["parts"][1]["options"]] == ["a"]
    assert out["embed"] is None


@pytest.mark.parametrize("block_id", ["plan", "plan&x#1"])
def test_a_card_with_a_block_the_dashboard_does_not_draw_embeds_the_board_rendering(block_id):
    out = Drive({"board_url": "http://board/p/x"}, None).read_board(board(card(block_id, ("table", "rows"), ("choice", "ok"))))["cards"][0]

    assert out["parts"] == []
    assert urllib.parse.parse_qs(out["embed"].split("?", 1)[1]) == {"block": [block_id], "theme": ["dark"]}


@pytest.mark.parametrize(
    ("options", "approval", "decides"),
    [
        ([], False, False),
        (["Got it"], False, False),
        (["Got it", "Other"], False, False),
        (["Keep", "Switch"], False, True),
        ([], True, True),
    ],
)
def test_only_an_open_approval_or_a_real_choice_asks_him_to_decide(options, approval, decides):
    block = card("c", ("record", "why"), *([("choice", "pick")] if options else []), *([("approval", "ok")] if approval else []), status="open")
    if options:
        block["children"][1]["options"] = [{"id": label, "label": label} for label in options]
    assert Drive({"board_url": "http://board/p/x"}, None).read_board(board(block))["cards"][0]["decides"] is decides


def test_a_card_with_a_multiple_choice_pick_embeds_the_board_rendering():
    picks = card("picks", ("choice", "many"))
    picks["children"][0]["multi"] = True

    assert Drive({"board_url": "http://board/p/x"}, None).read_board(board(picks))["cards"][0]["parts"] == []


@pytest.mark.parametrize(
    ("row", "state"),
    [
        ({"head": "bbbbbbb1", "verdict": {"verdict": "approved", "head": "bbbbbbb"}}, "approved"),
        ({"head": "bbbbbbb1", "verdict": {"verdict": "approved", "head": "aaaaaaa"}, "carried": ["aaaaaaa9"]}, "approved"),
        ({"head": "bbbbbbb1", "verdict": {"verdict": "approved", "head": "aaaaaaa"}}, "you"),
        ({"head": "bbbbbbb1", "verdict": None, "go_head": "bbbbbbb"}, "approved"),
        ({"head": "bbbbbbb1", "verdict": {"verdict": "rejected", "head": "bbbbbbb"}}, "held"),
        ({"head": "bbbbbbb1", "verdict": {"verdict": "cleared"}, "go_head": "aaaaaaa"}, "you"),
    ],
)
def test_a_tap_at_the_head_or_a_carried_head_is_not_waiting_on_him(row, state):
    assert verdict_state(row) == state


def ledger(**rows):
    return {number.removeprefix("pr"): {"title": "ci: x", "head": "h" * 8, **fields} for number, fields in rows.items()}


def listed(prs, rows=(), gos=None, records=(), trees=None, steps=(), holds=None):
    return {entry["number"]: entry for entry in drive_prs(prs, list(rows), gos or {}, list(records), trees or {}, TREES, list(steps), PARTS, holds or {}, NOW)["prs"]}


def test_every_drive_pull_request_is_one_light_and_closed_ones_are_none():
    prs = ledger(
        pr42001={"state": "landed", "title": "sand: a"},
        pr42002={"state": "landed"},
        pr42003={"state": "open", "title": "api: b"},
        pr42004={"state": "closed-without-squash"},
        pr42005={},
    )

    lights = listed(prs, trees={"42001": ["Old tree", "New tree"]})

    assert sorted(lights) == [42001, 42002, 42003, 42005]
    assert sum(1 for light in lights.values() if light["state"] == "landed") == 2
    assert [lights[n]["tree"] for n in (42001, 42002)] == ["New tree", "Shared"]
    assert [lights[n]["part"] for n in (42001, 42003, 42005)] == ["agent", "api", "tools"]


@pytest.mark.parametrize(
    ("fields", "row_state", "go", "state"),
    [
        ({"state": "landed"}, "you", None, "landed"),
        ({"state": "open", "test_state": "failure"}, "you", None, "you"),
        ({"state": "open", "test_state": "failure"}, None, None, "failing"),
        ({"state": "open"}, "held", None, "held"),
        ({"state": "open"}, "approved", None, "queued"),
        ({"state": "open"}, None, "hhhhhhh", "queued"),
        ({"state": "open"}, None, "aaaaaaa", "waiting"),
        ({"state": "open", "test_state": "pending"}, None, None, "doing"),
        ({"state": "open", "head": "bbbbbbbb"}, "approved", None, "waiting"),
    ],
)
def test_each_pull_request_takes_the_first_state_in_precedence(fields, row_state, go, state):
    rows = [{"number": 42001, "state": row_state, "head": "h" * 8, "title": "T", "block": "b"}] if row_state else []

    assert listed(ledger(pr42001=fields), rows, {42001: go} if go else {})[42001]["state"] == state


@pytest.mark.parametrize(
    ("base_tests", "state", "base_failing"),
    [("failure", "waiting", True), ("success", "failing", False)],
)
def test_a_red_inherited_from_a_failing_base_is_not_the_pull_requests_own(base_tests, state, base_failing):
    prs = ledger(
        pr42001={"state": "open", "branch": "base", "test_state": base_tests},
        pr42002={"state": "open", "branch": "child", "base": "base", "test_state": "failure"},
    )

    light = listed(prs)[42002]

    assert (light["state"], light["base"], light["base_failing"]) == (state, 42001, base_failing)


def test_a_pending_pull_request_on_a_failing_base_keeps_moving():
    prs = ledger(
        pr42001={"state": "open", "branch": "base", "test_state": "failure"},
        pr42002={"state": "open", "branch": "child", "base": "base", "test_state": "pending"},
    )

    assert listed(prs)[42002]["state"] == "doing"


def test_an_approved_pull_request_on_a_failing_base_reads_approved_not_waiting():
    prs = ledger(
        pr42001={"state": "open", "branch": "base", "test_state": "failure"},
        pr42002={"state": "open", "branch": "child", "base": "base", "test_state": "failure"},
    )

    assert listed(prs, gos={42002: "hhhhhhh"})[42002]["state"] == "queued"


def test_a_release_held_for_the_morning_stops_every_release_after_it():
    releases = [
        {"id": "R1", "state": "First part complete: live everywhere"},
        {"id": "R2", "state": "Waits on #43196"},
        {"id": "R3", "state": "Holds for your morning: the signed build exists only inside the real release"},
        {"id": "R4", "state": "Waits for release 3"},
        {"id": "R5", "state": "Waits for release 4"},
    ]

    assert [release["word"] for release in release_path(releases)] == ["now", "after", "held", "unreachable", "unreachable"]


def test_a_live_release_reads_out_while_an_earlier_one_is_unfinished_and_a_pause_stops_the_rest():
    releases = [
        {"id": "R1", "state": "First part complete: the second part waits on #42723"},
        {"id": "R2", "state": "Live at 7:27 AM; during its rollout it failed customer runs"},
        {"id": "R3", "state": "Paused by you at 7:49 AM until you read the retro"},
        {"id": "R4", "state": "Paused by you at 7:49 AM until you read the retro"},
    ]

    assert [release["word"] for release in release_path(releases)] == ["now", "out", "held", "unreachable"]


def test_a_release_path_with_nothing_held_marks_the_first_unreleased_release():
    releases = [{"id": "R1", "state": "Released 4:33 AM"}, {"id": "R2", "state": "Waits on #43196"}, {"id": "R3", "state": "Waits for release 2"}]

    assert [release["word"] for release in release_path(releases)] == ["out", "blocked", "after"]


@pytest.mark.parametrize(
    ("fields", "row_state", "base_state", "next_step"),
    [
        ({"state": "landed"}, None, None, "Nothing: it has landed."),
        ({"state": "open", "mergeable_state": "dirty", "test_state": "failure"}, None, None, "p8a-step7 rebases it onto its base."),
        ({"state": "open", "test_state": "failure", "failing_checks": [{"name": "buildkite/test", "url": "u"}]}, None, None, "p8a-step7 fixes buildkite/test."),
        ({"state": "open"}, "you", None, "Your tap on the merge board at hhhhhhh."),
        ({"state": "open", "mergeable_state": "dirty"}, "you", None, "Your tap on the merge board at hhhhhhh, and p8a-step7 rebases it onto its base."),
        ({"state": "open"}, "approved", "open", "Lands after #42001 lands."),
        ({"state": "open"}, "approved", "landed", "Lands when the merge queue reaches it."),
        ({"state": "open", "hold_reason": "held for the cutover", "hold_until": "2026-10-30T00:00:00Z"}, None, None, "Held by root: held for the cutover."),
        ({"state": "open", "hold_reason": "root cci hold: landing dependency not live (hold records #134860-#134881, sweep 6:41 PM)", "hold_until": "2026-10-30T00:00:00Z"}, None, None, "Held by root: landing dependency not live."),
    ],
)
def test_every_pull_request_carries_one_next_action_from_its_own_state(fields, row_state, base_state, next_step):
    base = {"pr42001": {"state": base_state, "branch": "base"}} if base_state else {}
    prs = ledger(**base, pr42002={"lane": "p8a-step7", "branch": "child", **({"base": "base"} if base_state else {}), **fields})
    rows = [{"number": 42002, "state": row_state, "head": "h" * 8, "title": "T", "block": "b", "blocker": "Nothing; lands on your tap."}] if row_state else []

    light = listed(prs, rows)[42002]

    assert (light["next"], light["owner"], light["head"]) == (next_step, "p8a-step7", "hhhhhhh")


def test_a_root_hold_is_the_next_action_even_on_a_red_conflicted_pull_request():
    prs = ledger(
        pr42900={"state": "open", "lane": "p8f-replica", "test_state": "failure", "mergeable_state": "dirty"},
        pr42901={"state": "open", "lane": "p8e-desktop-ship-5"},
        pr42902={"state": "open", "lane": "p8a-step7"},
    )
    lines = ["#42780 readers-first: after #42745 lands", "#42900 held for cutover: lands with the reader cutover, not before", "lane:p8e-desktop-ship-5 desktop waits for the morning"]

    lights = listed(prs, holds=hold_reasons([Entry(line) for line in lines], prs))

    assert lights[42900]["next"] == "Held by root: held for cutover: lands with the reader cutover, not before."
    assert lights[42901]["next"] == "Held by root: desktop waits for the morning."
    assert lights[42902]["next"] == "Waits its turn in its stack."


def step(prs, lanes=(), tone="later", step_id="s"):
    return {"id": step_id, "title": "T", "state": "S", "tone": tone, "blocker": None, "next": "N", "prs": list(prs), "lanes": list(lanes)}


@pytest.mark.parametrize(
    ("states", "live", "tone"),
    [
        ({1: "landed", 2: "landed"}, False, "done"),
        ({1: "landed", 2: "you"}, False, "you"),
        ({1: "doing", 2: "failing"}, False, "failing"),
        ({1: "waiting"}, True, "moving"),
        ({1: "landed", 2: "queued"}, False, "queued"),
        ({1: "queued", 2: "waiting"}, False, "waiting"),
        ({}, False, "later"),
    ],
)
def test_a_step_takes_its_tone_from_its_lights_and_workers(states, live, tone):
    drive = {"prs": [{"number": n, "state": state} for n, state in states.items()], "tracked": [*states, 99]}
    workers = [{"lane": "p8a-step7", "at": "2026-10-27T07:55:00Z", "prs": [], "step": "s", "live": live}]

    row = step_rows([step([*states, 99])], drive, workers, {})[0]

    assert row["tone"] == tone
    assert [entry["number"] for entry in row["prs"]] == list(states)


def test_a_step_whose_blocker_names_an_open_pull_request_is_not_done():
    drive = {"prs": [{"number": 42001, "state": "landed"}, {"number": 42002, "state": "failing"}], "tracked": [42001, 42002]}

    row = step_rows([{**step([42001]), "blocker": "a fix for what the check found in #42002"}], drive, [], {})[0]

    assert (row["tone"], [entry["number"] for entry in row["prs"]]) == ("failing", [42001, 42002])


def test_a_step_whose_blocker_names_a_pull_request_outside_the_ledger_is_not_done():
    drive = {"prs": [{"number": 42712, "state": "landed"}, {"number": 42714, "state": "landed"}], "tracked": [42712, 42714]}

    row = step_rows([{**step([42712, 42714, 43196]), "blocker": "a fix for what the plan check found in #43197"}], drive, [], {})[0]

    assert (row["tone"], row["untracked"]) == ("waiting", [43196, 43197])


def test_a_step_the_progress_desk_reads_done_is_done_though_the_ledger_misses_some_of_its_pull_requests():
    drive = {"prs": [{"number": 42712, "state": "landed"}, {"number": 42714, "state": "landed"}], "tracked": [42712, 42714]}

    row = step_rows([{**step([42712, 42714, 43196, 43197]), "tone": "done"}], drive, [], {})[0]

    assert (row["tone"], row["untracked"]) == ("done", [43196, 43197])


SEQ = itertools.count(140000)


def record(lane, minutes_ago, kind="state", text="", topic="", to=()):
    return {"seq": next(SEQ), "lane": lane, "kind": kind, "text": text, "topic": topic, "to": list(to), "at": (NOW - dt.timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")}


def test_a_worker_box_takes_its_most_severe_record_and_desks_are_not_workers():
    records = [
        record("p8e-desktop-ship-5", 1, kind="head", text="pushed #42900"),
        record("p8e-desktop-ship-5", 2, kind="blocker"),
        record("p8e-desktop-ship-5", 33, kind="go"),
        record("landing-desk", 1),
    ]

    workers = drive_workers(records, {}, [step([42900], step_id="journal")], NOW)

    assert [w["lane"] for w in workers] == ["p8e-desktop-ship-5"]
    assert workers[0]["boxes"][-1] == "bad"
    assert workers[0]["boxes"][5] == "ok"
    assert workers[0]["prs"] == [42900]
    assert workers[0]["step"] == "journal"


def test_coordinator_desks_get_their_own_boxes_apart_from_workers():
    records = [record("landing-desk", 1, kind="blocker"), record("root", 7), record("board-refresher", 9), record("p8e-desktop-ship-5", 1), record("dashboard-v3-4", 2)]

    desks = drive_workers(records, {}, [], NOW, desks=True)

    assert [d["lane"] for d in desks] == ["landing-desk", "root", "board-refresher"]
    assert [w["lane"] for w in drive_workers(records, {}, [], NOW)] == ["p8e-desktop-ship-5", "dashboard-v3-4"]
    assert desks[0]["boxes"][-1] == "bad"
    assert desks[1]["boxes"][-2] == "info"


def test_a_lane_that_only_mentions_a_pull_request_is_not_the_one_working_on_it():
    prs = ledger(pr43203={"state": "open", "lane": "p8a-step6b-creds-5"}, pr42900={"state": "open", "lane": "p8f-replica"})
    records = [
        record("p8a-step6b-creds-5", 9, kind="answer", text="#43203 gets restacked onto current dev"),
        record("dashboard-user-test", 2, kind="report", text="round 9: conflicted #43203 shown ready to tap"),
        record("dashboard-v3-5", 3, kind="defect", text="Journal's blocker names #42900"),
        record("p8f-replica-3", 5, kind="head", text="HEAD #42900 abc1234"),
    ]

    lights = listed(prs, records=records)

    assert (lights[43203]["worker"]["lane"], lights[42900]["worker"]["lane"]) == ("p8a-step6b-creds-5", "p8f-replica-3")
    assert {w["lane"]: w["prs"] for w in drive_workers(records, prs, [], NOW)}["dashboard-user-test"] == []


def test_a_pull_requests_last_word_is_its_owners_newest_record_about_it():
    prs = ledger(pr43203={"state": "open", "lane": "p8a-step6b-creds-5"})
    records = [
        record("p8a-step6b-creds-5", 30, kind="head", text="HEAD #43203 abc1234"),
        record("p8a-step6b-creds-5", 9, kind="answer", text="#43203 gets restacked onto current dev"),
        record("p8a-step6b-creds-5", 2, kind="state", text="idle, nothing open"),
        record("dashboard-user-test", 1, kind="report", text="conflicted #43203 shown ready to tap"),
    ]

    word = listed(prs, records=records)[43203]["word"]

    assert word["text"] == "#43203 gets restacked onto current dev"
    assert word["at"] == records[1]["at"]


def journey_drive(*entries):
    base = {"base": None, "base_failing": False, "tree": "New tree", "part": "api", "step": "s1", "worker": None, "head": "abc1234", "owner": "lane-a", "owner_at": None, "word": None, "blocker": None, "checks": [], "next": "x", "read_at": None}
    return {"prs": [{**base, **entry} for entry in entries], "tracked": [entry["number"] for entry in entries]}


def test_the_release_answer_names_its_open_blockers_with_owner_and_next_and_stops_at_the_held_release():
    drive = journey_drive(
        {"number": 42723, "state": "queued", "next": QUEUED_NEXT},
        {"number": 42810, "state": "failing", "next": "lane-a fixes buildkite/test."},
        {"number": 42700, "state": "landed", "next": "Nothing: it has landed."},
    )
    releases = [
        {"id": "R1", "name": "Cloud", "state": "The second part waits on #42723, #42810 and #42700.", "parts": ["api"], "word": "now", "tonight": True},
        {"id": "R2", "name": "Gateway", "state": "Live at 7:27 AM", "parts": ["gateway"], "word": "out", "tonight": True},
        {"id": "R3", "name": "Desktop", "state": "Paused by you at 7:49 AM", "parts": ["app"], "word": "held", "tonight": True},
        {"id": "R4", "name": "New code", "state": "Paused by you at 7:49 AM", "parts": ["agent"], "word": "unreachable", "tonight": True},
    ]
    prs = {"42723": {"title": "sand: restart"}, "42810": {"title": "sand: viewer"}, "42700": {"title": "api: done", "state": "landed"}}

    out = journeys(drive, [], releases, [], [], prs, {}, TREES, NOW)["release"]

    assert [line["text"][:6] for line in out["lines"]] == ["R1 Clo", "#42723", "#42810", "R3 Des"]
    assert [(line["who"], line["next"]) for line in out["lines"][1:3]] == [("lane-a", QUEUED_NEXT), ("lane-a", "lane-a fixes buildkite/test.")]
    assert out["missing"] == ["#42723: what clears its place in the merge queue (landing-desk queue_clears)"]

    prs["42723"]["queue_clears"] = "2nd in the queue; lands after #43350"
    out = journeys(drive, [], releases, [], [], prs, {}, TREES, NOW)["release"]

    assert (out["lines"][1]["next"], out["missing"]) == ("2nd in the queue; lands after #43350", [])


def test_the_red_step_answer_says_what_failed_and_names_a_missing_error_line():
    drive = journey_drive({"number": 42741, "state": "failing", "checks": [{"name": "buildkite/test", "url": "https://bk/1"}]})
    steps = [{"id": "s1", "label": "Journal", "tone": "failing", "prs": [{"number": 42741, "state": "failing"}]}]

    out = journeys(drive, steps, [], [], [], {"42741": {"title": "sandsql: reader"}}, {}, TREES, NOW)["red"]

    assert out["lines"][0]["text"] == "#42741 sandsql: reader: buildkite/test"
    assert out["missing"] == ["#42741 buildkite/test: its first error line (ledger writer failing_checks error)"]

    drive["prs"][0]["checks"][0]["error"] = "TestReplica timed out after 30s"
    out = journeys(drive, steps, [], [], [], {"42741": {"title": "sandsql: reader"}}, {}, TREES, NOW)["red"]

    assert (out["lines"][0]["text"], out["missing"]) == ("#42741 sandsql: reader: buildkite/test: TestReplica timed out after 30s", [])


def test_a_done_step_with_no_pull_requests_finishes_with_no_landing():
    """Root #242768: a done step that lists no pull request crashed every refresh with max() of nothing; no landing finishes it."""
    drive = journey_drive({"number": 42766, "state": "landed"})
    steps = [{"id": "s1", "label": "Plan", "tone": "done", "prs": []}, {"id": "s2", "label": "Signal", "tone": "done", "prs": [{"number": 42766, "state": "landed"}]}]
    prs = {"42766": {"title": "sand: signal", "state": "landed", "landed_at": (NOW - dt.timedelta(minutes=50)).isoformat()}}

    lines = journeys(drive, steps, [], [], [], prs, {}, TREES, NOW)["since"]["lines"]

    assert [(line["text"], line["next"]) for line in lines] == [("#42766 sand: signal landed", "finishes step Signal")]


def test_the_catch_up_answer_says_what_each_landing_and_approval_freed():
    drive = journey_drive({"number": 42766, "state": "landed"}, {"number": 42786, "state": "you", "base": 42766})
    prs = {"42766": {"title": "sand: signal", "state": "landed", "landed_at": (NOW - dt.timedelta(minutes=50)).isoformat()}, "42786": {"title": "sand: child"}}
    records = [record("root", 10, kind="go", text="go #42786 at c182871"), record("orca-desk", 5, kind="go", text="launch a lane")]

    lines = journeys(drive, [], [], [], records, prs, {}, TREES, NOW)["since"]["lines"]

    assert [(line["text"], line["next"]) for line in lines] == [("Approved #42786 sand: child at c182871", "#42786 can land"), ("#42766 sand: signal landed", "#42786 can now land")]


def test_a_release_whose_named_blockers_all_landed_lists_none_and_tonight_names_its_stop_and_proof():
    drive = journey_drive({"number": 42700, "state": "landed"}, {"number": 42999, "state": "failing"})
    releases = [
        {"id": "R1", "name": "Cloud", "state": "Waits on #42700.", "parts": ["api"], "word": "now", "tonight": True},
        {"id": "R3", "name": "Desktop", "state": "Paused by you at 7:49 AM", "parts": ["app"], "word": "held", "tonight": True},
    ]
    proof = {"result": "old-app set PASS", "checked_at": "2026-10-27T17:40:00Z", "how": "release-vm run", "link": "#147640"}

    out = journeys(drive, [], releases, [], [], {"42700": {"state": "landed"}, "42999": {}}, {"preservation": proof}, TREES, NOW)

    assert [line["text"][:2] for line in out["release"]["lines"]] == ["R1", "R3"]
    assert [line["text"].split(":")[0] for line in out["track"]["lines"][2:]] == ["Old app and flagged users", "Tonight's gate", "Tonight's stop"]
    assert (out["track"]["lines"][2]["source"]["href"], out["track"]["missing"]) == ("/record/147640", [])


def merge_board(*groups):
    blocks = [{"id": "card-merge", "type": "card", "children": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": gid, "title": title, "summary": summary, "prs": prs} for gid, title, summary, prs in groups]}]}]
    return {"doc": {"blocks": blocks}, "interactions": {"triage": {}, "choices": {}, "decisions": {}, "inputs": {}, "feedback": {}}}


def board_row(number, state):
    return {"number": number, "head": f"{number}aaaa", "title": f"T{number}", "change": f"C{number}", "plan": f"P{number}", "risk": f"R{number}", "review": f"V{number}", "blocker": f"B{number}", "md": "M", "state": state}


def test_merge_needs_keep_the_board_groups_and_each_rows_facts():
    store = merge_board(
        ("tools", "Faster tools", "Four tooling changes; none reaches a user.", [board_row(43243, "approved"), board_row(43246, "you")]),
        ("done", "All approved", "Nothing left.", [board_row(43300, "approved")]),
        ("gate", "Release 1 waits on this", "The first release cannot start without it.", [board_row(43258, "you")]),
    )
    board = Drive({"board_url": "http://board/p/x"}, None).read_board(store)
    stamp(board)
    rows = board["rows"]

    out = needs(rows, [], [], "http://board/p/x", set())

    assert [(item["group"], item["relation"]) for item in out] == [("Faster tools", "Four tooling changes; none reaches a user."), ("Release 1 waits on this", "The first release cannot start without it.")]
    assert out[0]["prs"] == [{"number": 43246, "head": "43246aaaa", "title": "T43246", "change": "C43246", "plan": "P43246", "risk": "R43246", "review": "V43246", "blocker": "B43246", "md": "M", "block": "merge-prs", "state": "you", "key": rows[1]["key"], "ci_fix_go": None, "checked_at": None}]
    assert out[0]["key"] != out[1]["key"]
    assert [row["number"] for row in out[0]["others"]] == [43243]
    assert out[1]["source"] == {"label": "Merge board: Release 1 waits on this", "href": "http://board/p/x"}
    assert "1 pull request in “Release 1 waits on this”" in out[1]["why"]


@pytest.mark.parametrize(
    ("cached", "pr", "current"),
    [
        (None, {"head": "b", "changed_files": "9"}, False),
        ({"head": "a", "files": "9"}, {"head": "b", "changed_files": "9"}, True),
        ({"head": "a", "files": "9"}, {"head": "b", "changed_files": "10"}, False),
        ({"head": "b"}, {"head": "b", "changed_files": "10"}, True),
    ],
)
def test_a_restack_that_keeps_the_file_count_reuses_the_cached_paths(cached, pr, current):
    assert paths_current(cached, pr) is current


def test_a_row_whose_pull_request_conflicts_with_its_base_never_reads_unblocked():
    clear = {"blocker": "Nothing; lands on your tap."}
    rows = [board_row(43203, "you") | clear, board_row(42786, "you") | {"blocker": "Waits for #42766 to merge first."}, board_row(43323, "you") | clear, board_row(43204, "you")]

    out = with_conflicts(rows, {"43203": {"mergeable_state": "dirty"}, "42786": {"mergeable_state": "dirty"}, "43323": {"mergeable_state": "unknown"}, "43204": {"mergeable_state": "clean"}})

    assert [row["blocker"] for row in out] == [
        "Conflicts with its base, so it cannot land until its worker rebases it.",
        "Conflicts with its base, so it cannot land until its worker rebases it. It also waits on: Waits for #42766 to merge first.",
        "GitHub has not yet rechecked whether it merges cleanly. The merge board said: Nothing; lands on your tap.",
        "B43204",
    ]


def test_a_board_blocker_whose_named_pull_requests_all_landed_reads_cleared():
    rows = [board_row(42786, "you") | {"blocker": "Waits for #42766 to merge first."}, board_row(42787, "you") | {"blocker": "Waits for #42766 and #42770."}]

    rows += [board_row(42788, "you") | {"blocker": "Waits on #42780-#42782."}, board_row(42789, "you") | {"blocker": "Waits for #42766 to merge first, and the red test build must turn green."}]

    out = with_conflicts(rows, {"42766": {"state": "landed"}, "42770": {"state": "open"}, "42780": {"state": "landed"}, "42781": {"state": "open"}, "42782": {"state": "landed"}})

    assert [row["blocker"] for row in out] == [
        "Nothing now: #42766 landed.",
        "Waits for #42766 and #42770.",
        "Waits on #42780-#42782.",
        "Waits for #42766 to merge first, and the red test build must turn green.",
    ]


def test_a_landed_pull_request_with_a_stale_tap_row_is_not_a_need():
    store = merge_board(("tools", "Faster tools", "Two tooling changes.", [board_row(43246, "you"), board_row(43247, "you")]))
    rows = Drive({"board_url": "http://board/p/x"}, None).read_board(store)["rows"]

    out = needs(rows, [], [], "http://board/p/x", {43246})

    assert [row["number"] for row in out[0]["prs"]] == [43247]


def test_a_merge_card_with_no_rows_waiting_still_reaches_needs_you_as_its_feature_table():
    table = {"id": "merge-fixing", "type": "table", "columns": [{"key": "f", "label": "Feature"}, {"key": "l", "label": "Approved, landing"}], "rows": [{"f": "Release 1 waits on this", "l": "1"}]}
    page = {"id": "merge-approved-landing", "type": "display.page", "md": "| Feature | Waiting on |"}
    store = {"doc": {"blocks": [{"id": "card-merge-triage", "type": "card", "title": "Which may merge now?", "summary": "Nothing needs your tap.", "children": [table, page]}]}, "interactions": {"triage": {}, "choices": {}, "decisions": {}, "inputs": {}, "feedback": {}}}

    board = Drive({"board_url": "http://board/p/x"}, None).read_board(store)

    assert board["rows"] == []
    assert board["merge"] == {"title": "Which may merge now?", "summary": "Nothing needs your tap.", "chips": [], "tables": [table], "pages": ["| Feature | Waiting on |"]}


def test_a_board_row_missing_a_fact_still_reaches_needs_you():
    row = board_row(43250, "you")
    del row["risk"], row["review"]
    store = merge_board(("gate", "Release 1 waits on this", "The first release cannot start without it.", [row]))
    rows = Drive({"board_url": "http://board/p/x"}, None).read_board(store)["rows"]

    out = needs(rows, [], [], "http://board/p/x", set())

    assert out[0]["prs"][0]["number"] == 43250
    assert out[0]["prs"][0]["risk"] is None


def test_an_ask_carries_the_lane_and_record_that_raised_it_and_an_unsourced_ask_says_so():
    record = {"seq": 140321, "lane": "release-plan-2", "at": "2026-10-27T09:16:23Z", "text": "Where is that key kept?"}
    asks = [{"what": "Say where the key is kept", "why": "The flag read needs it.", "record": record}, {"what": "Rotate a key", "why": "It sat in chat."}, {"what": "Resume", "why": "W", "optional": True}]

    out = needs([], [{"id": "card-a", "title": "Pick one", "summary": "Two ways.", "recommended": "A", "decides": True}, {"id": "card-b", "title": "Nothing to decide: what blocks the new app", "summary": "", "recommended": None, "decides": False}], asks, "http://board/p/x", set())

    assert [item["kind"] for item in out] == ["card", "ask", "ask"]
    assert out[0]["source"] == {"label": "Board card: Pick one", "href": "http://board/p/x"}
    assert out[0]["why"].endswith("Two ways. Recommended: A.")
    assert out[1]["source"] == {"label": "Open the record", "href": "/record/140321"}
    assert out[1]["why"] == "release-plan-2 asked in #140321, due now: The flag read needs it."
    assert out[1]["ask"]["record"]["text"] == "Where is that key kept?"
    assert out[2]["source"]["href"] is None
    assert out[2]["why"].startswith("No drive record asked for this")


@pytest.mark.parametrize(
    ("body", "interaction"),
    [
        ({"kind": "choice", "block": "pick", "options": ["cockpit"]}, {"type": "choice.selected", "blockId": "pick", "optionIds": ["cockpit"]}),
        ({"kind": "choice", "block": "pick", "other": "both"}, {"type": "choice.selected", "blockId": "pick", "optionIds": [], "other": "both"}),
        ({"kind": "note", "block": "card-a", "text": "looks good"}, {"type": "feedback.created", "blockId": "card-a", "text": "looks good"}),
    ],
)
def test_an_answer_on_the_dashboard_is_the_same_board_interaction(body, interaction):
    drive = Drive({"board_url": "http://board/p/x"}, None)
    sent = []
    drive.interact = lambda posted: sent.append(posted) or (200, "")

    drive.answer(body)

    assert {k: v for k, v in sent[0].items() if k != "id"} == interaction



def test_a_tap_answers_with_the_board_position_and_leaves_the_slow_sources_alone(monkeypatch):
    drive = Drive({"board_url": "http://board/p/x", "board_session": "s"}, None)
    drive.store_seq = lambda: 41
    drive.slow_at = 123.0
    monkeypatch.setattr(server, "run", lambda argv: json.dumps(board_with([{"number": 43211, "head": "8242887"}])))
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: contextlib.nullcontext())

    assert drive.decide("merge-prs", 43211, "approved", "8242887", None) == (200, {"seq": 41})
    assert drive.slow_at == 123.0


def test_a_build_failure_is_reported_once_and_only_when_the_next_build_fails_the_same_way():
    drive = Drive({"board_url": "http://board/p/x"}, None)
    reported = []
    drive.report_defect = reported.append

    def refused(refresh_slow):
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    drive.build = refused
    drive.rebuild()
    assert reported == []
    for _ in range(2):
        drive.dirty_at = 1.0
        drive.rebuild()
    assert reported == ["URLError: <urlopen error [Errno 61] Connection refused>"]


def test_the_record_stream_clears_its_failure_once_it_reconnects(monkeypatch):
    drive = Drive({"drive": "d"}, None)
    drive.errors["records"] = "URLError: <urlopen error [Errno 61] Connection refused>"

    class Stop(Exception):
        pass

    streams = iter([contextlib.nullcontext(iter([]))])

    def urlopen(*args, **kwargs):
        stream = next(streams, None)
        if stream is None:
            raise Stop
        return stream

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(Stop):
        drive.follow_records(0)
    assert "records" not in drive.errors



def test_a_board_event_rebuilds_the_model_without_rereading_the_slow_sources(monkeypatch):
    drive = Drive({"board_session": "s"}, None)
    drive.slow_at = 123.0
    drive.dirty_at = 0.0

    class Stop(Exception):
        pass

    class Watch:
        stdout = iter(['{"type":"choice.selected"}\n'])

        def wait(self):
            raise Stop

    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: Watch())
    with pytest.raises(Stop):
        drive.follow_board()
    assert drive.slow_at == 123.0
    assert drive.dirty_at

def test_a_code_change_restarts_the_server_while_its_curators_are_mid_run(tmp_path, monkeypatch):
    source = tmp_path / "server.py"
    source.write_text("")
    monkeypatch.setattr(server, "HERE", tmp_path)
    drive = Drive({"board_url": "http://board/p/x"}, None)
    for curator in drive.curators:
        curator.running = True
    naps = []

    class Restarted(Exception):
        pass

    class Starved(Exception):
        pass

    def nap(seconds):
        naps.append(seconds)
        if len(naps) == 1:
            os.utime(source, ns=(1, 1))
        if len(naps) > 3:
            raise Starved

    def execv(*args):
        raise Restarted

    monkeypatch.setattr(server.time, "sleep", nap)
    monkeypatch.setattr(server.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, ""))
    monkeypatch.setattr(server.os, "execv", execv)
    with pytest.raises(Restarted):
        drive.follow_code()


def test_review_assistant_queues_each_request_attempt_once_and_only_his_comments(tmp_path):
    assistant = ReviewAssistant({"repo": "a/b", "drive": "d"}, tmp_path, "http://model")
    assistant.queues[7] = queue.Queue()
    request = {"type": "ai.request.created", "request": {"id": "1", "attempt": 1}}
    for event in (
        request,
        request,
        {"type": "ai.request.created", "request": {"id": "1", "attempt": 2}},
        {"type": "comment.created", "id": "c1", "comment": {"author": "claude"}},
        {"type": "comment.created", "id": "c2", "comment": {"author": "remote"}},
        {"type": "comment.created", "id": "c3", "comment": {"author": "user"}},
        {"type": "file.states"},
    ):
        assistant.offer(7, event)
    queued = [assistant.queues[7].get_nowait() for _ in range(assistant.queues[7].qsize())]
    assert [e.get("request", {}).get("attempt") or e["id"] for e in queued] == [1, 2, "c3"]


def test_a_plain_root_go_on_a_tap_row_waits_on_his_tap_and_only_a_carry_reads_approved():
    """Root #204463: #45016 at 12cdc5e and #45508 at 37fa7c6 showed 'approved, landing' on a plain root go while the board held no approval at either head."""
    drive = Drive({"board_url": "http://board/p/x"}, None)
    drive.note_go({"kind": "go", "text": "go #45016 at 12cdc5e: tap row, go on grades per root #203509; fidelity #203204 ok, finder ok #203234. Lands only on his board tap."})
    drive.note_go({"kind": "go", "text": "go #45508 at 37fa7c6af7 (tap row; lands on the owner's tap)"})
    drive.note_go({"kind": "go", "text": "go #45412 at bbbb222 TAP-CARRIED: the owner's tap at aaaa111 carries to bbbb222, a patch-identical restack"})
    rows = [{"number": n, "head": head, "verdict": None} for n, head in ((45016, "12cdc5e"), (45508, "37fa7c6"), (45412, "bbbb222"))]

    drive.check_gos(rows)

    assert [(r["state"], r["checked_at"][:7]) for r in rows] == [("you", "12cdc5e"), ("you", "37fa7c6"), ("approved", "bbbb222")]


def test_a_copy_without_defect_recipients_posts_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr("server.subprocess.run", lambda *args, **kwargs: calls.append(args))
    Drive({"board_url": "http://board/p/x", "drive": "d", "keeper_lane": "keeper"}, None).report_defect("a test board looks unapproved")
    assert calls == []
    Drive({"board_url": "http://board/p/x", "drive": "d", "keeper_lane": "keeper", "defects_to": ["keeper", "root"]}, None).report_defect("a real defect")
    assert calls[0][0][calls[0][0].index("--to") + 1] == "keeper,root"


def test_a_ci_fix_on_a_root_go_lands_without_his_tap_only_outside_the_excluded_tree():
    drive = Drive({"board_url": "http://board/p/x", "ci_fix_excludes": "New tree"}, None)
    drive.paths = {"43644": {"trees": ["Shared"]}, "44138": {"trees": ["Shared"]}, "44087": {"trees": ["New tree"]}}
    drive.note_go({"kind": "go", "text": "go #43644 at a3f2ce2 (CI fix: tests and build fixes; lands on a root go without a tap)"})
    drive.note_go({"kind": "go", "text": "go #44138 at 8783133: one-line tools/ci fix, outside the new tree; CI-fix exemption"})
    drive.note_go({"kind": "go", "text": "go #44087 at c0ffee1: new-tree test fix; CI-fix exemption"})
    drive.note_go({"kind": "go", "text": "go #43700 at b4e5f6a (his tap on the board)"})
    rows = [{"number": n, "head": head, "verdict": None} for n, head in ((43644, "a3f2ce2"), (44138, "8783133"), (44087, "c0ffee1"), (43700, "b4e5f6a"))]

    drive.check_gos(rows)

    assert [(r["ci_fix_go"], r["state"]) for r in rows] == [(True, "approved"), (True, "approved"), (False, "you"), (False, "you")]


def test_a_go_the_coordinator_withdraws_stops_counting_as_a_ci_fix():
    drive = Drive({"board_url": "http://board/p/x", "ci_fix_excludes": "New tree"}, None)
    drive.note_go({"kind": "go", "lane": "root", "text": "go #44087 at 1a2b3c4 CI fix: new-tree test repair", "refs": {}})
    drive.note_go({"kind": "decide", "lane": "root", "text": "landing-desk-3 is right; go #161978 on #44087 is withdrawn: every new-tree fix goes to his tap.", "refs": {"prs": ["pr:Forge-AI/monorepo#44087"]}})
    assert (drive.gos, drive.ci_fixes) == ({}, set())


def test_a_pull_requests_latest_word_is_the_newest_record_naming_it_outside_the_dashboard_lanes():
    drive = Drive({"board_url": "http://board/p/x"}, None)
    drive.note_word({"seq": 1, "at": "2026-10-28T15:01:23Z", "lane": "stall-watch", "kind": "blocker", "text": "#43876 at e00b045 waits on the owner's tap", "refs": {}})
    drive.note_word({"seq": 2, "at": "2026-10-28T15:02:00Z", "lane": "dashboard-user-test-2", "kind": "report", "text": "#43876 row is stale", "refs": {}})
    drive.note_word({"seq": 3, "at": "2026-10-28T15:03:00Z", "lane": "root", "kind": "decide", "text": "go #161978 is withdrawn", "refs": {"prs": ["pr:Forge-AI/monorepo#44087"]}})
    assert {n: w["seq"] for n, w in drive.words().items()} == {"43876": 1, "44087": 3}


def test_the_newest_evidence_record_is_a_pull_requests_state_owner_and_clock():
    drive = Drive({"board_url": "http://board/p/x"}, None)
    record = {"seq": 9, "at": "2026-10-28T20:20:00Z", "lane": "progress-desk-2", "kind": "evidence", "text": "row", "refs": {"prs": [44087]}}
    drive.note_evidence({**record, "fields": {"attrs": {"state": "Waits on his tap", "owner": "p8a-step5-5", "since": "2026-10-28T19:00:00Z", "next": "He taps it"}}})
    drive.note_evidence({**record, "seq": 10, "fields": {"attrs": {"state": "Out of the queue until its tests pass", "owner": "merge-process", "since": "2026-10-28T20:10:00Z"}}})
    drive.note_evidence({**record, "seq": 11, "kind": "evidence", "text": "another lane's evidence with no row attributes"})
    drive.note_evidence({**record, "seq": 12, "fields": {"attrs": {"actor": "platy"}}})
    assert drive.evidences()["44087"] == {"state": "Out of the queue until its tests pass", "owner": "merge-process", "since": "2026-10-28T20:10:00Z", "next": "", "at": "2026-10-28T20:20:00Z", "seq": 10}


def test_a_landed_or_closed_pull_request_drops_out_of_the_work_pages_waiting_rows():
    pages = {"work": {"running": [], "pull_requests": [{"number": 1, "stage": "In the merge queue"}, {"number": 2, "stage": "Waits on your tap"}, {"number": 3, "stage": "Held"}, {"number": 4, "stage": "Grading"}]}}
    prs = {"1": {"state": "landed"}, "2": {"state": "open"}, "3": {"state": "closed"}, "4": {"state": "open"}}
    out = server.without_finished_prs(pages, prs, {"2": {"state": "Landed"}})
    assert [row["number"] for row in out["work"]["pull_requests"]] == [4]


def test_only_a_pull_request_still_open_on_the_ledger_and_the_runner_keeps_its_waiting_evidence():
    prs = {"1": {"state": "open"}, "2": {"state": "open"}, "3": {"state": "closed"}}
    runner = {"2": {"state": "Landed"}}
    assert [n for n in ("1", "2", "3") if server.still_open(n, prs, runner)] == ["1"]


def test_the_model_request_serves_the_prebuilt_view_and_never_builds_it(tmp_path):
    drive = Drive({"board_url": "http://board/p/x"}, None)
    drive.live = lambda model: (_ for _ in ()).throw(AssertionError("built inside a request"))
    body = json.dumps({"version": 3, "prs": {}}).encode()
    snapshot = tmp_path / "served.json.gz"
    snapshot.write_bytes(gzip.compress(body))
    drive.restore(snapshot)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(drive))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/model", timeout=5).read() == body
    finally:
        httpd.shutdown()


def test_a_record_that_answers_the_owners_question_reads_as_his_question_then_prose(tmp_path):
    """Root #203757: the answer to the owner's question opened as the raw cci record, header, routing, pr: tag and /tmp path included."""
    body = tmp_path / "answer.txt"
    body.write_text("Who paused the queue\n\nThe host-pressure run paused it at 7:02 PM.\n\n- 1615 pending\n- 122 paused\n")
    asked = {"seq": 10, "lane": "dashboard", "kind": "owner", "at": "2026-10-30T08:00:00Z", "text": "Asked on the dashboard: who paused the cleanup queue?", "refs": {}}
    record = {"seq": 11, "lane": "tooling-misc-2", "kind": "answer", "at": "2026-10-30T08:08:48Z", "re": 10, "text": "1:08 AM Paused by the host-pressure run pr:Forge-AI/monorepo#45381 /tmp/p8a7-a200848.txt", "refs": {"path": str(body)}}

    page = server.record_page(record, asked, None)

    assert "<h1>who paused the cleanup queue?</h1>" in page
    assert "tooling-misc-2 answered at 1:08 AM" in page
    assert "<p>Paused by the host-pressure run</p><p>Who paused the queue</p><p>The host-pressure run paused it at 7:02 PM.</p><ul><li>1615 pending</li><li>122 paused</li></ul>" in page
    assert "pr:" not in page and "/tmp/" not in page
    assert 'href="/record/11?raw=1"' in page


def test_a_tap_row_whose_live_head_or_parent_is_not_green_leaves_the_deck_and_says_why():
    """Owner 2:11 AM on #45412 (root #203813): 'why am I being asked to review this if tests are failing?'"""
    rows = [{"number": n, "head": "aaaa111", "state": "you", "blocker": "Green, reviewed and checked against the plan; it waits on your tap"} for n in (45411, 45412, 45413, 45414)]
    prs = {
        "45411": {"state": "open", "branch": "b11", "base": "dev", "head": "aaaa111", "test_state": "failure"},
        "45412": {"state": "open", "branch": "b12", "base": "b11", "head": "bbbb222", "test_state": "pending"},
        "45413": {"state": "open", "branch": "b13", "base": "b11", "head": "aaaa111", "test_state": "success"},
        "45414": {"state": "open", "branch": "b14", "base": "dev", "head": "aaaa111", "test_state": "success"},
    }
    records = [{"kind": "head", "at": "2026-10-30T09:06:00Z", "text": "HEAD #45412 bbbb2222 (was aaaa111)"}]

    out = {row["number"]: row for row in server.at_live_head(rows, prs, records)}

    assert (out[45411]["state"], out[45411]["blocker"]) == ("failing", "Tests failing on the version; back when green.")
    assert (out[45412]["state"], out[45412]["blocker"]) == ("failing", "Tests still running on the version pushed at 2:06 AM; back when green.")
    assert (out[45413]["state"], out[45413]["blocker"]) == ("failing", "#45411 below it fails its tests; back when green.")
    assert out[45414]["state"] == "you"
    assert [need["prs"][0]["number"] for need in server.merge_needs([{**row, "block": "b", "group_id": "g", "group": "G", "group_summary": "", "key": str(row["number"])} for row in out.values()], "u")] == [45414]


def test_evidence_written_before_the_live_head_was_pushed_never_reaches_the_owner():
    """#45412 at 2:11 AM showed 'Green, reviewed and checked against the plan; it waits on your tap' from before its 2:06 AM push, whose tests failed."""
    evidence = {
        "45412": {"state": "Green, reviewed and checked against the plan; it waits on your tap.", "at": "2026-10-30T08:50:00Z"},
        "45414": {"state": "Its build is red; its worker is fixing the failing build.", "at": "2026-10-30T09:10:00Z"},
    }
    prs = {"45412": {"state": "open", "head": "bbbb2222"}, "45414": {"state": "open", "head": "cccc3333"}}
    records = [{"kind": "head", "at": "2026-10-30T09:06:00Z", "text": "HEAD #45412 bbbb2222 (was aaaa111)"}, {"kind": "head", "at": "2026-10-30T09:00:00Z", "text": "HEAD #45414 cccc3333"}]

    assert list(server.live_evidence(evidence, prs, {}, records)) == ["45414"]


def finding_row(seq, sweep, state, at, kind="finding"):
    fields = {"kind": kind, "sweep": sweep, "seq": str(seq), "at": at, "pr": "45001", "line": "go/sand/a.go:12", "verdict": "fix", "owner": "port-misc-3", "text": f"finding {seq}", "state": state, "by": ""}
    return {"key": f"{kind}/{seq}", "fields": fields}


FINDING_ROWS = [
    finding_row(4, "go-sweep-6", "open", "2026-10-30T10:05:00Z"),
    finding_row(1, "go-sweep-6", "routed", "2026-10-30T09:00:00Z"),
    finding_row(2, "go-sweep-6", "fixed", "2026-10-30T09:10:00Z"),
    finding_row(3, "owner", "open", "2026-10-30T09:30:00Z", kind="pick"),
    finding_row(5, "old-world-audit-go", "closed", "2026-10-30T08:00:00Z"),
]


def test_open_findings_group_by_sweep_oldest_first_and_drop_fixed_and_closed_rows():
    groups = open_findings(FINDING_ROWS)

    assert [(group["sweep"], [row["seq"] for row in group["rows"]]) for group in groups] == [("go-sweep-6", ["1", "4"]), ("owner", ["3"])]
    assert groups[1]["rows"][0]["kind"] == "pick"


def test_findings_read_from_the_ledger_named_in_findings_json_and_a_missing_file_shows_nothing(tmp_path):
    drive = Drive({"findings": str(tmp_path / "findings.json")}, None)

    assert drive.read_findings() == []
    assert "findings" in drive.errors

    (tmp_path / "findings.json").write_text(json.dumps({"ledger": "abc123", "cursor": 7}))
    asked = []
    drive.ccn = lambda *args: asked.append(args) or json.dumps({"rows": FINDING_ROWS})

    assert [group["sweep"] for group in drive.read_findings()] == ["go-sweep-6", "owner"]
    assert asked == [("ledger", "show", "abc123", "--json")]
    assert "findings" not in drive.errors


def test_a_release_record_becomes_the_newest_story_line_of_its_worker_once():
    history = {"lane:release": {"events": [{"ts": "2026-10-30T13:14:00Z", "kind": "state", "text": "Release failed at its first step", "holder": None, "next": None}]}}
    releases = [
        {"lane": "release-5", "kind": "release", "at": "2026-10-30T13:26:00Z", "text": "Release started"},
        {"lane": "release-5", "kind": "release", "at": "2026-10-30T13:14:00Z", "text": "Release failed"},
    ]
    events = server.with_releases(history, releases)["lane:release"]["events"]
    assert [event["text"] for event in events] == ["Release started", "Release failed at its first step"]
    assert len(history["lane:release"]["events"]) == 1


def test_answer_times_reads_the_board_store_whose_events_and_subjects_both_carry_created_at(tmp_path, monkeypatch):
    store = tmp_path / "state.db"
    with contextlib.closing(sqlite3.connect(store)) as db, db:
        db.execute("CREATE TABLE subjects (id TEXT PRIMARY KEY, slug TEXT NOT NULL, created_at INTEGER NOT NULL)")
        db.execute("CREATE TABLE events (subject_id TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL)")
        db.execute("INSERT INTO subjects VALUES ('s1', 'board', 1)")
        db.executemany("INSERT INTO events VALUES ('s1', ?, ?, ?, ?)", [(1, "choice.selected", json.dumps({"blockId": "pick"}), 1793390000), (2, "choice.selected", json.dumps({"blockId": "pick"}), 1793390060), (3, "triage.decided", json.dumps({"blockId": "merge-prs"}), 1793390120)])
    monkeypatch.setattr(server, "STORE", store)

    assert Drive({"board_url": "http://board/p/board"}, None).answer_times() == {"pick": "2026-10-30T19:54:20+00:00"}


def test_a_page_that_stops_updating_reports_each_new_failure_to_root_once(monkeypatch):
    drive = Drive({"board_url": "http://board/p/x"}, None)
    drive.live_model = {"built_at": "2026-10-30T19:42:28+00:00"}
    reported = []
    drive.report_defect = reported.append
    failures = iter([sqlite3.OperationalError("ambiguous column name: created_at")] * 2 + [KeyError("rows")])

    def live(model):
        raise next(failures)

    drive.live = live
    assert [drive.live_or_report({}) for _ in range(3)] == [None, None, None]
    assert reported == ["the page stopped updating at 2026-10-30T19:42:28+00:00, every refresh fails with OperationalError: ambiguous column name: created_at", "the page stopped updating at 2026-10-30T19:42:28+00:00, every refresh fails with KeyError: 'rows'"]

    drive.live = lambda model: {"built_at": "2026-10-30T21:00:00+00:00"}
    assert drive.live_or_report({}) == {"built_at": "2026-10-30T21:00:00+00:00"}
    assert drive.reported_live is None


def board_with(prs, **triage):
    return {"doc": {"blocks": [{"id": "card-merge", "type": "card", "children": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "title": "G", "prs": prs}]}]}]}, "interactions": {"triage": {"merge-prs": triage}}}


@pytest.mark.parametrize(
    ("store", "number", "verdict", "head", "status", "out", "posted"),
    [
        (board_with([{"number": 45579, "head": "598d220"}]), 45579, "approved", "598d220", 200, {"seq": 9}, True),
        (board_with([], **{"45533": {"verdict": "approved", "head": "705ab74bdf157bd115542a4b02e2c20c8022f6ce", "at": "2026-10-30T20:05:04Z"}}), 45533, "approved", "705ab74", 200, {"seq": 9, "recorded": "2026-10-30T20:05:04Z"}, False),
        (board_with([]), 45533, "approved", "705ab74", 409, {"error": "#45533 left your board after this page last updated, so this tap changed nothing; reload the page to see where it went"}, False),
        (board_with([], **{"45533": {"verdict": "approved", "head": "705ab74", "at": "2026-10-30T20:05:04Z"}}), 45533, "cleared", "705ab74", 409, {"error": "#45533 left your board after this page last updated, so this tap changed nothing; reload the page to see where it went"}, False),
        (board_with([{"number": 45533, "head": "9e0a1b2"}]), 45533, "approved", "705ab74", 409, {"error": "#45533 is now at 9e0a1b2, not the 705ab74 this page shows, so this tap changed nothing; reload the page to see what changed"}, False),
    ],
)
def test_a_tap_records_only_on_a_row_the_board_still_holds_at_the_head_the_page_shows(monkeypatch, store, number, verdict, head, status, out, posted):
    monkeypatch.setattr(server, "run", lambda argv: json.dumps(store) if argv[:3] == ["cc-present", "outcomes", "--block"] else pytest.fail(argv))
    monkeypatch.setattr(Drive, "store_seq", lambda self: 9)
    sent = []
    monkeypatch.setattr(Drive, "interact", lambda self, interaction: sent.append(interaction) or (200, {"seq": 9}))

    assert Drive({"board_url": "http://board/p/x", "board_session": "s"}, None).decide("merge-prs", number, verdict, head, None) == (status, out)
    assert bool(sent) is posted
