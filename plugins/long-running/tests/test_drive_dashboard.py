import datetime as dt
import json
import queue

import pytest
from review_assistant import ReviewAssistant
from server import Drive, drive_prs, parse_plan, plan_prs, step_rows, verdict_state

NOW = dt.datetime(2026, 10, 27, 8, 0, tzinfo=dt.timezone.utc)
TREES = ["New tree", "Old tree", "Shared"]

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
    ],
)
def test_a_question_he_answered_leaves_the_board_count(store, open_ids):
    assert [c["id"] for c in Drive({"board_url": "http://board/p/x"}, None).read_board(store)["cards"]] == open_ids


@pytest.mark.parametrize(
    ("row", "state"),
    [
        ({"head": "bbbbbbb1", "verdict": {"verdict": "approved", "head": "bbbbbbb"}}, "approved"),
        ({"head": "bbbbbbb1", "verdict": {"verdict": "approved", "head": "aaaaaaa"}, "carried": ["aaaaaaa9"]}, "approved"),
        ({"head": "bbbbbbb1", "verdict": {"verdict": "approved", "head": "aaaaaaa"}}, "you"),
        ({"head": "bbbbbbb1", "verdict": None, "go_head": "bbbbbbb"}, "go"),
        ({"head": "bbbbbbb1", "verdict": {"verdict": "rejected", "head": "bbbbbbb"}}, "held"),
        ({"head": "bbbbbbb1", "verdict": {"verdict": "cleared"}, "go_head": "aaaaaaa"}, "you"),
    ],
)
def test_a_tap_at_the_head_or_a_carried_head_is_not_waiting_on_him(row, state):
    assert verdict_state(row) == state


def test_a_board_row_waits_on_him_only_at_the_pr_current_head():
    prs = {"42001": {"state": "open", "head": "bbbbbbb1", "test_state": "success"}, "42002": {"state": "open", "head": "ccccccc1", "test_state": "success"}}
    rows = [{"number": 42001, "head": "aaaaaaa1", "state": "you"}, {"number": 42002, "head": "ccccccc1", "state": "you"}]

    drive = drive_prs(prs, rows, [], {}, TREES, NOW)

    assert {square["number"]: square["state"] for square in drive["prs"]} == {42001: "work", 42002: "you"}


def test_the_ring_counts_exactly_the_green_squares():
    prs = {
        "42001": {"state": "landed"},
        "42002": {"state": "landed"},
        "42003": {"state": "landed"},
        "42004": {"state": "open", "test_state": "failure"},
        "42005": {"state": "open"},
        "42006": {"state": "closed-without-squash"},
        "42007": {"state": None},
    }
    trees = {"42001": ["Old tree", "New tree"], "42002": ["Shared"], "42004": ["Old tree"]}
    records = [{"lane": "p8a-step7", "at": "2026-10-27T07:50:00Z", "text": "pushed #42005", "topic": ""}]

    drive = drive_prs(prs, [], records, trees, TREES, NOW)

    squares = drive["prs"]
    assert drive["landed"] == sum(1 for square in squares if square["state"] == "landed") == 3
    assert drive["total"] == len(squares) == 5
    assert [square["tree"] for square in squares] == ["New tree", "Shared", "Shared", "Old tree", "Shared"]
    assert {square["number"]: square["state"] for square in squares}[42005] == "doing"
    assert drive["moving"] == 1


def step(prs, lanes=(), tone="later"):
    return {"id": "s", "title": "T", "state": "S", "tone": tone, "blocker": None, "next": "N", "prs": list(prs), "lanes": list(lanes)}


@pytest.mark.parametrize(
    ("states", "lanes", "tone"),
    [
        ({1: "landed", 2: "landed"}, [], "done"),
        ({1: "landed", 2: "you"}, [], "you"),
        ({1: "doing", 2: "failing"}, [], "failing"),
        ({1: "work"}, ["p8a-step7"], "moving"),
        ({1: "work"}, [], "queued"),
        ({}, [], "later"),
    ],
)
def test_a_step_takes_its_tone_and_fill_from_the_drive_list(states, lanes, tone):
    drive = {"prs": [{"number": n, "state": state, "tree": "Shared", "worker": None} for n, state in states.items()]}
    workers = [{"lane": "p8a-step7", "at": "2026-10-27T07:55:00Z", "text": "pushed"}]

    row = step_rows([step([*states, 99], lanes)], drive, workers, NOW)[0]

    assert row["tone"] == tone
    assert [entry["number"] for entry in row["prs"]] == list(states)
    assert row["landed"] == sum(1 for state in states.values() if state == "landed")


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


def test_a_cc_notes_doc_is_read_once_until_its_ref_moves(monkeypatch):
    refs = {"refs/cc-notes/docs/aaa1111": "oid-a1", "refs/cc-notes/docs/bbb2222": "oid-b1"}
    bodies = {"aaa1111": "id: aaa1111\nupdated: first\n\nRelease one", "bbb2222": DOC}
    shown = []

    def run(argv, cwd=None):
        assert cwd == "/repo"
        if argv[0] == "git":
            return "".join(f"{oid}\n" for ref, oid in sorted(refs.items()) if ref.startswith(argv[-1].rstrip("*")))
        if argv[1:3] == ["doc", "list"]:
            return "aaa1111\t2026-10-29\tprogress:x\tnot a plan\nbbb2222\t2026-10-29\tprogress:x\tplan\n"
        shown.append(argv[3])
        return bodies[argv[3]]

    monkeypatch.setattr("server.run", run)
    drive = Drive({"checkout": "/repo", "progress_label": "progress:x"}, None)
    for _ in range(3):
        assert drive.read_plan()[0] == "2026-10-27T06:30:00Z"
        assert drive.read_doc("aaa1111") == {"markdown": "Release one", "updated": "first"}
    assert shown == ["aaa1111", "bbb2222"]

    bodies["aaa1111"] = "id: aaa1111\nupdated: second\n\nRelease two"
    refs["refs/cc-notes/docs/aaa1111"] = "oid-a2"
    assert drive.read_doc("aaa1111") == {"markdown": "Release two", "updated": "second"}
    assert drive.read_plan()[0] == "2026-10-27T06:30:00Z"
    assert shown == ["aaa1111", "bbb2222", "aaa1111"]


def test_the_bus_alarm_beside_the_steps_is_the_drive_wide_stop_until_the_runner_clears_it(tmp_path, monkeypatch):
    """4:31 to 4:40 PM Oct 29 the bus hung and nothing on the dashboard said so (root #190549)."""
    steps = tmp_path / "progress-desk" / "steps.json"
    steps.parent.mkdir()
    steps.write_text(json.dumps({"updated": "2026-10-30T13:00:00Z", "steps": [], "now": {"blocker": None, "release": {"name": "New code in the cloud"}}}))
    drive = Drive({"steps": str(steps)}, None)
    monkeypatch.setattr(drive, "board", lambda: {"rows": [], "cards": []})
    monkeypatch.setattr(drive, "check_gos", lambda rows, records: None)
    model = {"prs": {}, "records": [], "lanes": [], "trees": {}, "tree_names": TREES}
    assert drive.live(model)["now"] == {"blocker": None, "release": {"name": "New code in the cloud"}}

    alarm = {"since": "2026-10-30T13:31:00Z", "text": "BUS-DOWN since 6:31 AM: cci has not answered 2 probes in a row"}
    (tmp_path / "bus-down.json").write_text(json.dumps(alarm))
    assert drive.live(model)["now"] == {"blocker": alarm, "release": {"name": "New code in the cloud"}}
