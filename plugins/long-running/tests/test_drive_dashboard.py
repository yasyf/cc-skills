import contextlib
import datetime as dt
import itertools
import gzip
import json
import os
import socket
import sqlite3
import subprocess
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import queue
import urllib.error
import urllib.parse
from types import SimpleNamespace

import pytest
from holds import Entry
from pages import stamp
from review_assistant import ReviewAssistant
from screens import screen_kind
import server
from server import QUEUED_NEXT, Drive, changed_since, drive_prs, drive_workers, journeys, needs, open_findings, parse_plan, plan_prs, step_rows, release_path, paths_current, hold_reasons, verdict_state, with_conflicts

TAILNET_OWNER = "owner@example.test"
OWNER_WHOIS = {"Node": {"Name": "owners-mac."}, "UserProfile": {"LoginName": TAILNET_OWNER}}
TAGGED_WHOIS = {"Node": {"Name": "sprite.", "Tags": ["tag:sprite"]}, "UserProfile": {"LoginName": "tagged-devices"}}
TAGGED_AS_OWNER_WHOIS = {"Node": {"Name": "sprite.", "Tags": ["tag:sprite"]}, "UserProfile": {"LoginName": TAILNET_OWNER}}
OTHER_WHOIS = {"Node": {"Name": "laptop."}, "UserProfile": {"LoginName": "someone-else@example.test"}}
MODEL_KEY = "model-key-the-browser-never-sees"
MODEL_REPLY = b'data: {"choices": [{"delta": {"content": "hello"}}]}\n\ndata: [DONE]\n\n'
ROUTE_ACTIONS = {"/api/verdict": "decide", "/api/answer": "answer", "/api/asked": "asked", "/api/change": "change_request", "/api/browser": "browser_report"}
WRITES = {
    "/api/verdict": {"block": "b", "number": 7, "verdict": "approved", "head": "abc"},
    "/api/answer": {"kind": "input", "block": "b", "text": "yes"},
    "/api/asked": {"text": "why?"},
    "/api/change": {"number": 7, "head": "abc", "text": "no lock"},
    "/api/browser": {"secure": True, "permission": "granted"},
}
REFUSED_CALLERS = [json.dumps(TAGGED_WHOIS), json.dumps(TAGGED_AS_OWNER_WHOIS), json.dumps(OTHER_WHOIS), "not json", "null", json.dumps({"Node": {}}), json.dumps({"Node": {}, "UserProfile": None}), json.dumps({"Node": None, "UserProfile": {"LoginName": TAILNET_OWNER}}), subprocess.CalledProcessError(1, "tailscale", stderr="no match for IP"), subprocess.TimeoutExpired("tailscale", 60), FileNotFoundError(2, "No such file or directory", "tailscale")]
NOW = dt.datetime(2026, 10, 27, 8, 0, tzinfo=dt.timezone.utc)
TREES =["New tree", "Old tree", "Shared"]
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
    assert out[0]["prs"] == [{"number": 43246, "head": "43246aaaa", "title": "T43246", "change": "C43246", "plan": "P43246", "risk": "R43246", "review": "V43246", "blocker": "B43246", "md": "M", "block": "merge-prs", "state": "you", "key": rows[1]["key"], "ci_fix_go": None, "checked_at": None, "awaiting": None}]
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
    drive.interact = lambda posted, subject=None: sent.append(posted) or (200, "")

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
    body = json.dumps({"version": 3, **{key: {} for key in server.NOW_DRAWS}, "prs": {}, "pr_state": {}, "history": {}, "drive": {"prs": []}, "records": [], "lanes": []}).encode()
    snapshot = tmp_path / "served.json.gz"
    snapshot.write_bytes(gzip.compress(body))
    drive.restore(snapshot)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(drive, TAILNET_OWNER, {"127.0.0.1"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/model", timeout=5).read() == body
        assert json.loads(urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/model?tab=now", timeout=5).read())["cut"] is True
    finally:
        httpd.shutdown()


def test_a_worker_answer_routed_through_root_reads_as_the_owners_question_then_labelled_lines():
    """Owner 12:03 AM Nov 2: the parsed records were still blocks of text. Records 307877, 307888 and 307894 from that night."""
    owner = {"seq": 307877, "lane": "owner-board", "kind": "ask", "at": "2026-11-02T07:27:00Z", "text": 'what is a "chat copy"?', "refs": {"prs": [46811]}}
    routed = {"seq": 307888, "lane": "root", "kind": "ask", "at": "2026-11-02T07:28:00Z", "re": 307877, "text": '11:28 PM owner on the #46811 board row, 11:27 PM: "what is a chat copy?" Answer within 5 minutes in two plain sentences.', "refs": {"prs": [46811]}}
    answer = {"seq": 307894, "lane": "signal-valkey-2", "kind": "answer", "at": "2026-11-02T07:30:00Z", "re": 307888, "to": ["root", "board-refresher-6"], "refs": {"prs": [46811]},
              "text": "11:30 PM A chat copy is a reader's local replica of a shared chat: in code a ReplicaChat opened from a Mount whose At (incarnation) and Stop fix the point the share was taken. The row borrowed 'copy' from #46811's title. board-refresher-6: replace 'copy' in the #46811 row with 'share replica (ReplicaChat at a stop)'."}

    page = server.record_page(answer, [routed, owner], None, {"46811": "replica: a copy never lists"})

    assert "<h1>what is a &quot;chat copy&quot;?</h1>" in page
    assert "<dt>Who</dt><dd>signal-valkey-2, to root, board-refresher-6</dd>" in page
    assert "<dt>Answers</dt><dd>Your question on #46811 at 11:27 PM: “what is a &quot;chat copy&quot;?”, routed through #307888</dd>" in page
    assert "<dt>Says</dt><dd>A chat copy is a reader&#x27;s local replica of a shared chat: in code a ReplicaChat opened from a Mount whose At (incarnation) and Stop fix the point the share was taken.</dd>" in page
    assert "<dt>Asks board-refresher-6</dt><dd>replace &#x27;copy&#x27; in the #46811 row with &#x27;share replica (ReplicaChat at a stop)&#x27;.</dd>" in page
    assert "<li>The row borrowed &#x27;copy&#x27; from #46811&#x27;s title.</li>" in page
    assert '<li><a href="/#/review/46811">#46811</a> replica: a copy never lists</li>' in page
    assert '<details class="rec-raw"><summary>The record as written</summary><pre>11:30 PM A chat copy' in page


def test_a_ruling_that_quotes_the_owner_says_what_was_decided_not_his_words_again():
    """Record 307433: root's ruling opened with the owner's own words, which the page already shows as the question."""
    owner = {"seq": 307420, "lane": "owner-board", "kind": "ask", "at": "2026-11-02T06:51:00Z", "text": "we control both ends right so we dont need http/1 at all right? even IPC could work", "refs": {"prs": [47206]}}
    ruling = {"seq": 307433, "lane": "root", "kind": "decide", "at": "2026-11-02T06:53:00Z", "re": 307420, "to": ["signal-valkey-2"], "refs": {"prs": [47206]},
              "text": '10:53 PM OWNER RULING 10:51 PM on #47206, verbatim: "we control both ends right so we dont need http/1 at all right? even IPC could work". The lane owning #47206 drops HTTP/1 from the wire. Owner: the lane that holds #47206, within 15 minutes.'}

    lines = dict(server.parse_record(ruling, [owner])["lines"])

    assert lines["Says"] == "The lane owning #47206 drops HTTP/1 from the wire."
    assert lines["Answers"].startswith("Your question on #47206 at 10:51 PM")


def test_a_head_record_names_its_pull_request_at_its_commit_and_a_dump_says_why_it_is_not_parsed():
    """Record 308569, a HEAD post, and a record whose body is a JSON dump."""
    head = {"seq": 308569, "lane": "s22-class-1", "kind": "head", "at": "2026-11-02T08:07:00Z", "to": ["root"], "refs": {},
            "text": "12:07 AM HEAD #47760 at eebb3a105452: answers fidelity 195812@bd2bdc0c. The composer box is the column's content box. Also fixes the style guard that failed at 0ecc726, see #308001 and https://buildkite.com/forge/builds/9."}
    dump = {"seq": 308600, "lane": "desk-runner", "kind": "report", "at": "2026-11-02T08:09:00Z", "refs": {}, "text": json.dumps({"rows": [{"pr": 47760, "state": "open"}] * 40})}

    parsed = server.parse_record(head, [])
    page = server.record_page(dump, [], None, {})

    assert dict(parsed["lines"])["Says"] == "answers fidelity 195812@bd2bdc0c."
    assert (parsed["prs"], parsed["heads"], parsed["commits"], parsed["records"], parsed["urls"]) == ([47760], {47760: "eebb3a105452"}, ["bd2bdc0c", "0ecc726"], [308001], ["https://buildkite.com/forge/builds/9"])
    assert "<dt>Not parsed</dt><dd>It is JSON data, not sentences.</dd>" in page
    assert "<dt>Says</dt>" not in page and "<dt>Details</dt>" not in page


def test_every_board_row_waiting_on_the_owner_stays_on_the_deck_whatever_the_ledger_says_about_its_tests():
    """Owner 12:14 PM Nov 2: 'why are there PRs to approve on my board that are not showing in the long-running dashboard?'"""
    rows = [{"number": n, "key": str(n), "head": "aaaa111", "state": "you", "verdict": None, "go_head": None, "blocker": "", "block": "b", "group_id": "g", "group": "G", "group_summary": ""} for n in (45411, 45412, 45413)]
    prs = {
        "45411": {"state": "open", "head": "aaaa111", "test_state": "failure"},
        "45412": {"state": "open", "head": "bbbb222", "test_state": "pending"},
    }

    out = server.with_conflicts(rows, prs)

    assert [row["number"] for need in server.merge_needs(out, "u") for row in need["prs"]] == [45411, 45412, 45413]


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


def test_a_change_request_the_record_tool_cannot_post_answers_with_the_reason(monkeypatch):
    drive = Drive({"drive": "d", "board_url": "http://board/p/x"}, None)

    def record_tool_missing(argv, cwd=None):
        if argv[0] == "tailscale":
            return json.dumps(OWNER_WHOIS)
        raise FileNotFoundError(2, "No such file or directory", "cci")

    monkeypatch.setattr(server, "run", record_tool_missing)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(drive, TAILNET_OWNER, {"127.0.0.1"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/change", data=json.dumps({"number": 7, "head": "abc", "text": "no lock"}).encode(), headers={"Content-Type": "application/json"})
    try:
        with pytest.raises(urllib.error.HTTPError) as failed:
            urllib.request.urlopen(request, timeout=5)
        assert failed.value.code == 502
        assert "cci" in json.loads(failed.value.read())["error"]
    finally:
        httpd.shutdown()


class FakeModel(BaseHTTPRequestHandler):
    calls = []
    status = 200
    retry_after = None

    def log_message(self, *args):
        pass

    def do_POST(self):
        FakeModel.calls.append({"path": self.path, "auth": self.headers["Authorization"], "agent": self.headers["User-Agent"], "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
        reply = MODEL_REPLY if FakeModel.status == 200 else b'{"message": "slow down"}'
        self.send_response(FakeModel.status)
        self.send_header("Content-Type", "text/event-stream" if FakeModel.status == 200 else "application/json")
        if FakeModel.retry_after:
            self.send_header("Retry-After", FakeModel.retry_after)
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)


@pytest.fixture
def dashboard(monkeypatch):
    monkeypatch.setenv("CEREBRAS_API_KEY", MODEL_KEY)
    model = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    monkeypatch.setattr(server, "MODEL_URL", f"http://127.0.0.1:{model.server_port}/v1/chat/completions")
    monkeypatch.setattr(FakeModel, "calls", [])
    monkeypatch.setattr(FakeModel, "status", 200)
    monkeypatch.setattr(FakeModel, "retry_after", None)
    drive = Drive({"drive": "d", "board_url": "http://board/p/x"}, None)
    drive.acted = []
    for name in ROUTE_ACTIONS.values():
        setattr(drive, name, lambda *args, name=name: (drive.acted.append(name), (200, {"recorded": name}))[1])
    drive.review_url = lambda number: drive.acted.append("review_url") or "http://review/"
    drive.assistant = SimpleNamespace(watching={}, stale=set())
    drive.work = lambda item: drive.acted.append("work")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(drive, TAILNET_OWNER, {"127.0.0.1"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{httpd.server_port}", drive=drive)
    finally:
        httpd.shutdown()
        model.shutdown()


@pytest.fixture
def live_alerts():
    drive = Drive({"drive": "d", "board_url": "http://board/p/x", "board_session": "s"}, None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(drive, TAILNET_OWNER, {"127.0.0.1"}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{httpd.server_port}", drive=drive)
    finally:
        httpd.shutdown()


def sse_events(stream):
    event = {}
    for raw in stream:
        line = raw.decode().rstrip("\n")
        if line.startswith(":"):
            continue
        if not line:
            if event:
                yield event
            event = {}
            continue
        field, _, value = line.partition(": ")
        event[field] = value


def board_of_cards(*titles):
    return json.dumps({"interactions": {"triage": {}, "feedback": {}}, "doc": {"blocks": [{"type": "card", "id": title, "title": title, "summary": "", "status": "open"} for title in titles]}})


def test_the_alert_stream_names_the_build_waits_for_the_first_read_and_carries_a_new_card(live_alerts, monkeypatch):
    drive = live_alerts.drive
    monkeypatch.setattr(server, "run", lambda argv, cwd=None: board_of_cards("card-one"))
    with urllib.request.urlopen(f"{live_alerts.url}/api/alerts", timeout=5) as stream:
        events = sse_events(stream)
        assert next(events) == {"event": "build", "data": server.BUILD}
        assert json.loads(urllib.request.urlopen(f"{live_alerts.url}/api/browser", timeout=5).read())["listening"] == {"127.0.0.1": 1}
        drive.read_cards()
        first = next(events)
        assert first["event"] == "alerts" and [alert["id"] for alert in json.loads(first["data"])] == ["one"]
        monkeypatch.setattr(server, "run", lambda argv, cwd=None: board_of_cards("card-one", "card-two"))
        drive.read_cards()
        assert [alert["id"] for alert in json.loads(next(events)["data"])] == ["one", "two"]


def test_an_unchanged_board_wakes_no_alert_stream(live_alerts, monkeypatch):
    drive = live_alerts.drive
    monkeypatch.setattr(server, "run", lambda argv, cwd=None: board_of_cards("card-one"))
    drive.read_cards()
    published = drive.alerts
    drive.read_cards()
    assert drive.alerts is published


def test_a_failed_board_read_keeps_the_last_alerts_and_says_so(live_alerts, monkeypatch):
    drive = live_alerts.drive
    monkeypatch.setattr(server, "run", lambda argv, cwd=None: board_of_cards("card-one"))
    drive.read_cards()
    published = drive.alerts

    def down(argv, cwd=None):
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(server, "run", down)
    drive.read_cards()
    assert drive.alerts is published and "cards" in drive.errors


def test_a_browser_report_from_the_owner_reads_back_with_its_address_and_agent(live_alerts, monkeypatch):
    whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    report = {"url": "https://mac:1", "secure": True, "permission": "granted", "service_worker": False, "hidden": True, "fired": {"title": "t", "at": "2026-11-03T21:00:00Z", "outcome": "shown"}}
    status, _, _ = call(live_alerts.url, "/api/browser", report)
    assert status == 200
    browsers = json.loads(call(live_alerts.url, "/api/browser")[1])["browsers"]
    assert len(browsers) == 1 and browsers[0]["client"] == "127.0.0.1" and {key: browsers[0][key] for key in report} == report


def test_a_second_report_from_one_browser_replaces_its_row(live_alerts, monkeypatch):
    whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    call(live_alerts.url, "/api/browser", {"permission": "default"})
    call(live_alerts.url, "/api/browser", {"permission": "granted"})
    assert [row["permission"] for row in json.loads(call(live_alerts.url, "/api/browser")[1])["browsers"]] == ["granted"]


def test_the_page_names_the_address_where_notifications_work(live_alerts, monkeypatch):
    live_alerts.drive.secure_url = "https://mac.example.ts.net:2/"
    monkeypatch.setattr(server.ssl, "SSLSocket", socket.socket)
    assert b'<meta name="secure-url" content="https://mac.example.ts.net:2/">' in call(live_alerts.url, "/")[1]


def test_a_plain_address_goes_to_the_one_where_notifications_work(live_alerts):
    live_alerts.drive.secure_url = "https://mac.example.ts.net:2/"

    class NoFollow(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None

    with pytest.raises(urllib.error.HTTPError) as moved:
        urllib.request.build_opener(NoFollow).open(f"{live_alerts.url}/", timeout=5)
    assert moved.value.code == 302 and moved.value.headers["Location"] == "https://mac.example.ts.net:2/"


def whois_answers(monkeypatch, answer):
    asked = []

    def whois(argv, cwd=None):
        asked.append(argv)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(server, "run", whois)
    return asked


def call(url, path, body=None, host=None):
    request = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(), headers={**({"Host": host} if host else {}), "Content-Type": "application/json", "Authorization": "Bearer from-the-page"})
    try:
        with urllib.request.urlopen(request, timeout=5) as reply:
            return reply.status, reply.read(), reply.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers


def test_the_key_route_is_gone_and_the_page_never_carries_the_key(dashboard, monkeypatch):
    whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    assert call(dashboard.url, "/ai.json")[0] == 404
    status, page, _ = call(dashboard.url, "/")
    assert status == 200 and '<html class="ai-on"' in page.decode()
    assert MODEL_KEY.encode() not in page


@pytest.mark.parametrize("answer", [json.dumps(TAGGED_WHOIS), json.dumps(OTHER_WHOIS), subprocess.CalledProcessError(1, "tailscale", stderr="no match for IP")])
def test_only_the_owners_device_sees_the_assistant(dashboard, monkeypatch, answer):
    whois_answers(monkeypatch, answer)
    assert "ai-on" not in call(dashboard.url, "/")[1].decode().split("<head>")[0]


def test_a_server_with_no_key_keeps_the_assistant_hidden_from_the_owner(dashboard, monkeypatch):
    whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    monkeypatch.delenv("CEREBRAS_API_KEY")
    assert "ai-on" not in call(dashboard.url, "/")[1].decode().split("<head>")[0]


def test_one_model_call_adds_the_key_on_the_server_and_fixes_the_endpoint_and_model(dashboard, monkeypatch):
    whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    asked = {"messages": [{"role": "user", "content": "why is it slow?"}], "stream": True, "tool_choice": "none", "model": "someone-elses-model", "endpoint": "https://elsewhere.example", "seed": 7}
    status, body, headers = call(dashboard.url, server.MODEL_ROUTE, asked)
    assert (status, body, headers.get_content_type()) == (200, MODEL_REPLY, "text/event-stream")
    assert MODEL_KEY.encode() not in body and MODEL_KEY not in str(headers)
    [sent] = FakeModel.calls
    assert sent["path"] == "/v1/chat/completions" and sent["auth"] == f"Bearer {MODEL_KEY}" and sent["agent"] == "drive-dashboard"
    assert sent["body"] == {"messages": asked["messages"], "stream": True, "tool_choice": "none", "model": server.MODEL_NAME}


def test_a_refused_model_call_passes_its_status_and_wait_to_the_page_without_the_key(dashboard, monkeypatch):
    whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    monkeypatch.setattr(FakeModel, "status", 429)
    monkeypatch.setattr(FakeModel, "retry_after", "3")
    status, body, headers = call(dashboard.url, server.MODEL_ROUTE, {"messages": []})
    assert (status, headers["Retry-After"]) == (429, "3")
    assert b"slow down" in body and MODEL_KEY.encode() not in body
    assert len(FakeModel.calls) == 1


def test_a_model_that_does_not_answer_comes_back_as_a_bad_gateway_without_the_key(dashboard, monkeypatch):
    whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    monkeypatch.setattr(server, "MODEL_URL", f"http://127.0.0.1:{closed.getsockname()[1]}/v1/chat/completions")
    closed.close()
    status, body, _ = call(dashboard.url, server.MODEL_ROUTE, {"messages": []})
    assert status == 502 and MODEL_KEY.encode() not in body


@pytest.mark.parametrize("answer", REFUSED_CALLERS)
@pytest.mark.parametrize("route", ROUTE_ACTIONS)
def test_every_write_refuses_anyone_but_the_owner_and_a_failed_lookup(dashboard, monkeypatch, route, answer):
    whois_answers(monkeypatch, answer)
    status, body, _ = call(dashboard.url, route, WRITES[route])
    assert status == 403 and "error" in json.loads(body)
    assert dashboard.drive.acted == []


@pytest.mark.parametrize("answer", REFUSED_CALLERS)
def test_the_model_route_refuses_anyone_but_the_owner_and_calls_nothing(dashboard, monkeypatch, answer):
    whois_answers(monkeypatch, answer)
    status, body, _ = call(dashboard.url, server.MODEL_ROUTE, {"messages": []})
    assert status == 403 and MODEL_KEY.encode() not in body
    assert FakeModel.calls == []


@pytest.mark.parametrize("route", ROUTE_ACTIONS)
def test_a_write_from_the_owner_lands_and_the_lookup_names_the_callers_address(dashboard, monkeypatch, route):
    asked = whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    status, body, _ = call(dashboard.url, route, WRITES[route])
    assert status == 200 and json.loads(body) == {"recorded": ROUTE_ACTIONS[route]}
    assert dashboard.drive.acted == [ROUTE_ACTIONS[route]]
    assert asked == [["tailscale", "whois", "--json", "127.0.0.1"]]


@pytest.mark.parametrize(("path", "body"), [("/api/review/7", {}), ("/api/story/some-work", None)])
def test_a_tagged_device_cannot_start_a_review_or_a_story(dashboard, monkeypatch, path, body):
    whois_answers(monkeypatch, json.dumps(TAGGED_WHOIS))
    assert call(dashboard.url, path, body)[0] == 403
    assert dashboard.drive.acted == []


def test_the_owner_still_opens_a_review(dashboard, monkeypatch):
    whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    status, body, _ = call(dashboard.url, "/api/review/7", {})
    assert status == 200 and json.loads(body)["url"] == "http://review/"
    assert dashboard.drive.acted == ["review_url"]


def test_a_link_or_image_on_another_site_cannot_start_a_review(dashboard, monkeypatch):
    asked = whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    assert call(dashboard.url, "/api/review/7")[0] == 404
    assert dashboard.drive.acted == []
    assert asked == []


@pytest.mark.parametrize(("path", "body"), [(route, WRITES[route]) for route in WRITES] + [("/api/review/7", {}), ("/api/story/some-work", None)])
def test_a_rebinding_page_is_refused_though_the_owner_s_device_carries_it(dashboard, monkeypatch, path, body):
    asked = whois_answers(monkeypatch, json.dumps(OWNER_WHOIS))
    assert call(dashboard.url, path, body, host="evil.example:61210")[0] == 403
    assert dashboard.drive.acted == []
    assert asked == []


def test_the_names_the_owner_may_use_are_this_macs_own(monkeypatch):
    status = {"Self": {"DNSName": "Studio.tail.ts.net.", "TailscaleIPs": ["100.1.2.3", "fd7a::1"]}}
    monkeypatch.setattr(server, "run", lambda argv, cwd=None: json.dumps(status))
    assert server.own_names("100.1.2.3") == {"studio.tail.ts.net", "studio", "100.1.2.3", "fd7a::1"}


def test_the_review_assistant_never_runs_with_permissions_bypassed(tmp_path, monkeypatch):
    class Stop(Exception):
        pass

    def run(argv, **_):
        ran.append(argv)
        raise Stop

    ran = []
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("subprocess.run", run)
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {"cc-review@cc-review": [{"installPath": "/cc-review/0.46.1"}]}}))
    assistant = ReviewAssistant({"repo": "a/b", "drive": "d"}, tmp_path, "http://model")
    assistant.queues[7] = queue.Queue()
    assistant.scope(7).mkdir(parents=True)
    assistant.queues[7].put({"type": "ai.request.created", "request": {"id": "1", "source": "system"}})
    monkeypatch.setattr(assistant, "board_row", lambda number: [{"number": number, "head": "abc"}])
    with pytest.raises(Stop):
        assistant.serve(7)
    argv = ran[0]
    assert "bypassPermissions" not in argv and argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    assert "--restricted" in argv and argv[argv.index("--plugin-dir") + 1] == "/cc-review/0.46.1"
    assert not {"Write", "Edit", "WebFetch", "WebSearch"} & set(argv[argv.index("--tools") + 1].split(","))
    assert set(assistant.allowed_tools(7)) <= set(argv)
    assert not {"Bash", "Read", "Write", "Edit"} & set(argv)
    assert "curl" not in argv[2] and '"head": "abc"' in argv[2]


def test_the_review_assistant_reads_its_row_and_runs_on_without_one(tmp_path):
    model = tmp_path / "model.json"
    model.write_text(json.dumps({"board": {"rows": [{"number": 7, "head": "abc"}, {"number": 8, "head": "def"}]}}))
    assert ReviewAssistant({"repo": "a/b", "drive": "d"}, tmp_path, model.as_uri()).board_row(7) == [{"number": 7, "head": "abc"}]
    assert ReviewAssistant({"repo": "a/b", "drive": "d"}, tmp_path, (tmp_path / "gone.json").as_uri()).board_row(7) == []


def test_a_read_stays_open_and_asks_nobody_who_is_calling(dashboard, monkeypatch):
    asked = whois_answers(monkeypatch, json.dumps(TAGGED_WHOIS))
    dashboard.drive.model = {"words": ["one"]}
    status, body, _ = call(dashboard.url, "/api/words")
    assert status == 200 and json.loads(body) == ["one"]
    assert asked == []


def test_the_retro_table_reads_through_the_installed_retro_py_drops_duplicates_and_names_a_failed_read(tmp_path, monkeypatch):
    """Owner 10:26 PM Nov 1: the dashboard fails loudly on a row it cannot show, and shows each row's real state."""
    reader = Drive({"drive": "durable-agents-rebuild"}, None)
    rows = [{"key": "retro/000001", "failure": "a", "state": "open", "problems": []}, {"key": "retro/000002", "failure": "a", "dup_of": "retro/000001", "problems": []}]
    asked = []
    monkeypatch.setattr(server.drive, "newest_install", lambda: {"installPath": str(tmp_path)})
    monkeypatch.setattr(server, "run", lambda argv: asked.append(argv) or "".join(json.dumps(row) + "\n" for row in rows))

    assert [row["key"] for row in reader.read_preventions()] == ["retro/000001"]
    assert asked == [[str(tmp_path / "bin" / "retro.py"), "--drive", "durable-agents-rebuild", "rows", "--json"]]
    assert "preventions" not in reader.errors

    monkeypatch.setattr(server, "run", lambda argv: (_ for _ in ()).throw(subprocess.CalledProcessError(1, argv, stderr="ccn down")))
    assert reader.read_preventions() == []
    assert reader.errors["preventions"].startswith("the retro table did not read") and "ccn down" in reader.errors["preventions"]


def test_a_refresh_carries_only_the_parts_that_changed_since_the_version_the_browser_holds():
    pieces = {key: server.encoded(value) for key, value in {"version": 8, "prs": {"1": "big"}, "brief": "new"}.items()}
    past = {"version": server.encoded(7)[2], "prs": pieces["prs"][2], "brief": server.encoded("old")[2]}
    assert json.loads(changed_since(8, pieces, past)) == {"version": 8, "changed": {"version": 8, "brief": "new"}}
    assert changed_since(8, pieces, None) is None
    assert changed_since(8, pieces, {"version": past["version"], "prs": past["prs"]}) is None


def test_a_view_joined_from_its_encoded_parts_is_the_view_encoded_whole():
    view = {"version": 8, "prs": {"1": {"title": "a \u201cquote\u201d"}}, "records": [{"seq": 1}], "words_at": None}
    assert server.joined({key: server.encoded(value) for key, value in view.items()}, view) == json.dumps(view).encode()


def test_nothing_builds_the_view_while_no_page_curator_or_request_reads_it(monkeypatch):
    drive = Drive({"board_url": "http://board/p/x"}, None)
    drive.rebuild = lambda: (_ for _ in ()).throw(AssertionError("built with no reader"))
    naps = []

    class Idle(Exception):
        pass

    def nap(seconds):
        naps.append(seconds)
        if len(naps) > 3:
            raise Idle

    monkeypatch.setattr(server.time, "sleep", nap)
    with pytest.raises(Idle):
        drive.refresh_loop(None)


def test_a_repeated_fetch_reads_only_the_records_after_the_newest_it_holds(monkeypatch):
    drive = Drive({"drive": "d", "board_url": "http://board/p/x"}, None)
    now = dt.datetime(2026, 11, 2, 12, tzinfo=dt.timezone.utc)
    queries = []
    pages = iter([[{"seq": 1, "at": "2026-11-02T11:00:00Z"}, {"seq": 2, "at": "2026-11-02T11:30:00Z"}], [{"seq": 3, "at": "2026-11-02T12:05:00Z"}]])
    monkeypatch.setattr(server, "cci", lambda path, **query: (queries.append(query), next(pages))[1])
    assert [r["seq"] for r in drive.fetch({"blocker"}, now - dt.timedelta(hours=2))] == [1, 2]
    assert [r["seq"] for r in drive.fetch({"blocker"}, now - dt.timedelta(minutes=45))] == [2, 3]
    assert queries == [{"drive": "d", "kind": ["blocker"], "limit": 500, "since_time": "2026-11-02T10:00:00Z"}, {"drive": "d", "kind": ["blocker"], "limit": 500, "since": 2}]


def test_the_lanes_are_read_again_only_after_a_record_arrives(monkeypatch):
    drive = Drive({"drive": "d", "board_url": "http://board/p/x", "title": "T", "repo": "r", "trees": []}, None)
    drive.slow = {"prs": {}}
    calls = []
    monkeypatch.setattr(server, "cci", lambda path, **query: calls.append(path) or [{"lane": "w-1"}])
    for _ in range(2):
        drive.model = drive.build(refresh_slow=False)
    drive.lanes_due = True
    drive.model = drive.build(refresh_slow=False)
    assert calls == ["lanes", "lanes"] and drive.model["lanes"] == [{"lane": "w-1"}]


def publishable_model(**fields):
    return {"words": [], "records": [], "lanes": [], "prs": {}, "pr_state": {}, "history": {}, "drive": {"prs": []}, **{key: {} for key in server.NOW_DRAWS}, "steps": [], "board": {"rows": [], "cards": []}, **fields}


def test_the_now_tab_keeps_its_slice_beside_the_now_screen(tmp_path):
    drive = Drive({"board_url": "http://board/p/x"}, None)
    drive.publish(publishable_model(), tmp_path / "served.json.gz")
    assert json.loads(drive.body("now")[0])["cut"] is True
    assert json.loads(drive.body(screen_kind("now"))[0])["screen"] == "now"


def test_a_curator_waits_for_a_view_built_after_it_asked(monkeypatch, tmp_path):
    drive = Drive({"board_url": "http://board/p/x"}, None)
    drive.live_model, drive.live_at = {"built_at": "old"}, 1.0
    drive.model = {"prs": {}}
    drive.rebuild = lambda: None
    drive.live = lambda model: publishable_model(built_at="fresh")
    got = []

    class Served(Exception):
        pass

    def nap(seconds):
        if got:
            raise Served

    monkeypatch.setattr(server.time, "sleep", nap)
    curator = threading.Thread(target=lambda: got.append(drive.curator_model()), daemon=True)
    curator.start()
    with pytest.raises(Served):
        drive.refresh_loop(tmp_path / "served.json.gz")
    curator.join()
    assert got[0]["built_at"] == "fresh" and drive.watchers == 0


def test_the_first_load_carries_what_the_now_tab_draws_and_leaves_the_rest_for_a_later_request():
    """Root #306148: one 4.8 MB model was parsed on every load; Now draws from open pull requests and the ones it names."""
    event = lambda i: {"ts": f"2026-11-02T08:{i:02d}:00Z", "kind": "state", "text": f"step {i}", "holder": None, "next": None}
    landed = {"number": 41000, "state": "landed", "base": None, "tree": "Shared", "part": "tools", "step": None, "owner": "w-1", "word": {"text": "long"}, "next": "Nothing: it has landed."}
    view = {
        "version": 9, "title": "Drive", "board": {"rows": []}, "brief": {"work": [{"changes": [46811], "workers": ["signal-valkey-2"]}]}, "milestone_state": [], "pages": {}, "workers": [], "now": {}, "status_now": {},
        "needs": [{"kind": "prs", "prs": [{"number": 45000}]}],
        "prs": {"45000": {"state": "landed", "lane": "old-1"}, "46811": {"state": "open", "lane": "signal-valkey-2"}, "47999": {"state": "open", "lane": "w-2"}, "41000": {"state": "landed", "lane": "w-1"}},
        "pr_state": {"45000": {"state": "landed"}, "41000": {"state": "landed"}},
        "history": {"pr:46811": {"events": [event(i) for i in range(9)]}, "pr:41000": {"events": [event(1)]}, "lane:signal-valkey": {"events": [event(2)]}, "lane:far-away": {"events": [event(3)]}},
        "drive": {"source": "ledger", "tracked": [], "prs": [landed]},
        "records": [{"seq": n} for n in range(400)],
        "lanes": [{"lane": "w-1"}],
    }

    now = server.now_view(view)

    assert now["cut"] is True and now["title"] == "Drive" and now["needs"] == view["needs"]
    assert sorted(now["prs"]) == ["45000", "46811", "47999"] and list(now["pr_state"]) == ["45000"]
    assert sorted(now["history"]) == ["lane:signal-valkey", "pr:46811"] and len(now["history"]["pr:46811"]["events"]) == 5
    assert now["drive"]["prs"] == [{"number": 41000, "state": "landed", "part": "tools", "step": None, "tree": "Shared"}]
    assert now["records"][0] == {"seq": 100} and now["lanes"] == []


def test_an_owner_question_stays_open_until_an_answer_lands_down_its_chain_or_a_new_version_is_pushed():
    """Root #308820: a row question is answered by the answer record that replies to it, through root's relay too, and the row then returns to the owner's queue."""
    ask = lambda seq, number, head: {"seq": seq, "lane": "owner-board", "kind": "ask", "at": "2026-11-02T07:27:00Z", "text": "why?", "refs": {"prs": [number]}, "fields": {"attrs": {"head": head}}}
    records = [
        ask(1, 46811, "aaaa111"),
        {"seq": 2, "lane": "root", "kind": "ask", "at": "2026-11-02T07:28:00Z", "re": 1, "text": "answer it", "refs": {}},
        {"seq": 3, "lane": "signal-valkey-2", "kind": "answer", "at": "2026-11-02T07:30:00Z", "re": 2, "text": "A replica.", "refs": {}},
        ask(4, 46742, "bbbb222"),
        {"seq": 5, "lane": "root", "kind": "decide", "at": "2026-11-02T07:39:00Z", "re": 4, "text": "board-refresher-6: rewrite the row.", "refs": {}},
        ask(6, 47329, "cccc333"),
    ]
    prs = {"46811": {"head": "aaaa111"}, "46742": {"head": "bbbb222"}, "47329": {"head": "dddd444"}}
    rows = [{"number": n, "head": "x", "state": "you"} for n in (46811, 46742, 47329, 47400)]

    asks = server.owner_asks(records, prs)
    awaiting = {row["number"]: row["awaiting"] for row in server.awaiting_reply(rows, asks)}

    assert asks["46811"][0]["answered"] is True
    assert asks["46742"][0]["answered"] is False
    assert awaiting == {46811: None, 46742: 4, 47329: None, 47400: None}


def test_a_lanes_confirmation_clears_his_order_and_only_a_reply_to_him_keeps_it():
    ask = lambda seq, number: {"seq": seq, "lane": "owner-board", "kind": "ask", "at": "2026-11-04T05:27:00Z", "text": "store it", "refs": {"prs": [number]}, "fields": {"attrs": {"head": "aaaa111"}}}
    records = [
        ask(1, 7),
        {"seq": 2, "lane": "root", "kind": "report", "at": "2026-11-04T05:29:00Z", "re": 1, "text": "For him on this: order applied, and board-refresher-7 withdraws the card.", "refs": {}},
        ask(3, 8),
        {"seq": 4, "lane": "owner-board", "kind": "posted", "at": "2026-11-04T05:29:00Z", "re": 3, "text": "Received, with the coordinator", "refs": {}},
        {"seq": 5, "lane": "lane-1", "kind": "blocked", "at": "2026-11-04T05:29:00Z", "re": 3, "text": "Waiting on a build.", "refs": {}},
        ask(6, 9),
        ask(7, 10),
        ask(8, 11),
        {"seq": 9, "lane": "root", "kind": "report", "at": "2026-11-04T05:29:00Z", "resolves": 8, "text": "Applied.", "refs": {}},
    ]
    prs = {str(n): {"head": "aaaa111"} for n in (7, 8, 9, 10, 11)}
    told = {6: {"id": "r1", "md": "Pick where it stores on your board.\n\nTwo places.", "action": "Pick where it stores on your board"}, 7: {"id": "r2", "md": "Done, nothing for you.\n\nStored."}}

    asks = server.owner_asks(records, prs, told)

    assert (asks["7"][0]["answered"], asks["7"][0]["action"]) == (True, None)
    assert "For him" not in json.dumps(asks)
    assert asks["8"][0]["answered"] is False
    assert (asks["9"][0]["answered"], asks["9"][0]["action"], asks["9"][0]["reply"]) == (True, "Pick where it stores on your board", "r1")
    assert (asks["10"][0]["answered"], asks["10"][0]["action"]) == (True, None)
    assert asks["11"][0]["answered"] is True


def test_a_thread_line_names_its_parent_to_the_communicator_and_reads_back_clean(monkeypatch):
    posted = []
    monkeypatch.setattr(server, "run", lambda argv: posted.append(argv) or "#7\n")
    drive = Drive({"board_url": "http://board/p/x", "drive": "d"}, None)
    assert drive.reply_back("yes do that", "reply:abc", 'reply abc "Title"') == (200, {"seq": 7})
    argv = posted[0]
    assert argv[argv.index("--topic") + 1] == "reply:abc"
    said = argv[argv.index("--text") + 1]
    assert 'reply abc "Title"' in said and said.endswith("yes do that")
    record = {"seq": 7, "at": "t", "kind": "owner", "lane": "dashboard", "to": ["owner-relay"], "topic": "reply:abc", "text": said}
    assert server.typed_in_chat(record)
    assert server.chat_line(record) == {"seq": 7, "at": "t", "on": "reply:abc", "text": "yes do that"}
    assert not server.typed_in_chat({**record, "to": ["root"]})

