import datetime as dt

import pytest
from server import Drive, drive_prs, drive_workers, needs, parse_plan, plan_prs, step_rows, verdict_state

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
        ({"head": "bbbbbbb1", "verdict": None, "go_head": "bbbbbbb"}, "approved"),
        ({"head": "bbbbbbb1", "verdict": {"verdict": "rejected", "head": "bbbbbbb"}}, "held"),
        ({"head": "bbbbbbb1", "verdict": {"verdict": "cleared"}, "go_head": "aaaaaaa"}, "you"),
    ],
)
def test_a_tap_at_the_head_or_a_carried_head_is_not_waiting_on_him(row, state):
    assert verdict_state(row) == state


def ledger(**rows):
    return {number.removeprefix("pr"): {"title": "ci: x", "head": "h" * 8, **fields} for number, fields in rows.items()}


def listed(prs, rows=(), gos=None, records=(), trees=None, steps=()):
    return {entry["number"]: entry for entry in drive_prs(prs, list(rows), gos or {}, list(records), trees or {}, TREES, list(steps), PARTS, NOW)["prs"]}


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
    ],
)
def test_each_pull_request_takes_the_first_state_in_precedence(fields, row_state, go, state):
    rows = [{"number": 42001, "state": row_state, "head": "h" * 8, "title": "T", "block": "b"}] if row_state else []

    assert listed(ledger(pr42001=fields), rows, {42001: go} if go else {})[42001]["state"] == state


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
    drive = {"prs": [{"number": n, "state": state} for n, state in states.items()]}
    workers = [{"lane": "p8a-step7", "at": "2026-10-27T07:55:00Z", "prs": [], "step": "s", "live": live}]

    row = step_rows([step([*states, 99])], drive, workers)[0]

    assert row["tone"] == tone
    assert [entry["number"] for entry in row["prs"]] == list(states)


def record(lane, minutes_ago, kind="state", text="", topic=""):
    return {"lane": lane, "kind": kind, "text": text, "topic": topic, "at": (NOW - dt.timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")}


def test_a_worker_box_takes_its_most_severe_record_and_desks_are_not_workers():
    records = [
        record("p8e-desktop-ship-5", 1, text="pushed #42900"),
        record("p8e-desktop-ship-5", 2, kind="blocker"),
        record("p8e-desktop-ship-5", 33, kind="go"),
        record("landing-desk", 1),
    ]

    workers = drive_workers(records, [step([42900], step_id="journal")], NOW)

    assert [w["lane"] for w in workers] == ["p8e-desktop-ship-5"]
    assert workers[0]["boxes"][-1] == "bad"
    assert workers[0]["boxes"][5] == "ok"
    assert workers[0]["prs"] == [42900]
    assert workers[0]["step"] == "journal"


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
    rows = Drive({"board_url": "http://board/p/x"}, None).read_board(store)["rows"]

    out = needs(rows, [], [], "http://board/p/x")

    assert [(item["group"], item["relation"]) for item in out] == [("Faster tools", "Four tooling changes; none reaches a user."), ("Release 1 waits on this", "The first release cannot start without it.")]
    assert out[0]["prs"] == [{"number": 43246, "head": "43246aaaa", "title": "T43246", "change": "C43246", "plan": "P43246", "risk": "R43246", "review": "V43246", "blocker": "B43246", "block": "merge-prs", "state": "you"}]
    assert [row["number"] for row in out[0]["others"]] == [43243]
    assert out[1]["source"] == {"label": "Merge board: Release 1 waits on this", "href": "http://board/p/x"}
    assert "1 pull request in “Release 1 waits on this”" in out[1]["why"]


def test_a_board_row_missing_a_fact_still_reaches_needs_you():
    row = board_row(43250, "you")
    del row["risk"], row["review"]
    store = merge_board(("gate", "Release 1 waits on this", "The first release cannot start without it.", [row]))
    rows = Drive({"board_url": "http://board/p/x"}, None).read_board(store)["rows"]

    out = needs(rows, [], [], "http://board/p/x")

    assert out[0]["prs"][0]["number"] == 43250
    assert out[0]["prs"][0]["risk"] is None


def test_an_ask_carries_the_lane_and_record_that_raised_it_and_an_unsourced_ask_says_so():
    record = {"seq": 140321, "lane": "release-plan-2", "at": "2026-10-27T09:16:23Z", "text": "Where is that key kept?"}
    asks = [{"what": "Say where the key is kept", "why": "The flag read needs it.", "record": record}, {"what": "Rotate a key", "why": "It sat in chat."}, {"what": "Resume", "why": "W", "optional": True}]

    out = needs([], [{"id": "card-a", "title": "Pick one", "summary": "Two ways.", "recommended": "A"}], asks, "http://board/p/x")

    assert [item["kind"] for item in out] == ["card", "ask", "ask"]
    assert out[0]["source"] == {"label": "Board card: Pick one", "href": "http://board/p/x"}
    assert out[0]["why"].endswith("Two ways. Recommended: A.")
    assert out[1]["source"] == {"label": "Record #140321 from release-plan-2", "href": "/record/140321"}
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
