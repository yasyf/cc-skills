import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "dashboard" / "app"))

from owed import kept, order_replies, overdue_rows, owner_ask_rows, told_asks

LEDGER = """| m | at | text | record | mark |
|---|---|---|---|---|
| m7 | Nov 3 3:01 AM | fold the dashboard to one-line events |  |  |
M12 carries this.
| m8 | Nov 3 3:05 AM | what is the status of the gateway rollout |  | responded |
| m9 | Nov 3 3:09 AM | > quoted words from the agent<br>move the owner table to a tab |  |  |
| m10 | Nov 3 3:10 AM | Owner on board d50e, Merge it? (merge-prs): 49000 approved at abc |  |  |
| m11 | Nov 3 3:11 AM | ok |  |  |
"""


def record(seq, lane, kind, text):
    return {"seq": seq, "lane": lane, "kind": kind, "at": "2026-11-03T12:00:00Z", "text": text, "refs": None}


def rows(records, prs=None, answers=None):
    return owner_ask_rows(LEDGER, records, prs or {}, [], set(), 2026, answers or {})


def by_message(found):
    return {row["m"]: row for row in found["messages"]}


def test_a_worker_done_on_a_linked_item_is_applied():
    found = by_message(rows([record(1, "dash-1", "done", "M12 shipped")]))
    assert found[7]["state"] == "applied"
    assert found[7]["lanes"] == ["dash-1"]


def test_a_relay_saying_done_applies_nothing():
    found = by_message(rows([record(1, "owner-relay", "done", "m7 forwarded")]))
    assert found[7]["state"] == "asked"


def test_a_responded_mark_is_answered_never_applied():
    assert by_message(rows([]))[8]["state"] == "answered"


def test_a_captured_chat_answer_answers_the_message_and_carries_its_text():
    found = by_message(rows([], answers={"m7": "| bug | cause |\n|---|---|\n| stale row | no capture |"}))
    assert found[7]["state"] == "answered"
    assert found[7]["answer"] == "| bug | cause |\n|---|---|\n| stale row | no capture |"


def test_a_message_with_no_answer_carries_none():
    found = by_message(rows([]))
    assert found[7]["state"] == "asked"
    assert found[7]["answer"] == ""


def test_a_landed_pull_request_a_worker_names_applies_the_message():
    found = by_message(rows([record(1, "dash-1", "report", "m9 is in #49000")], {"49000": {"state": "landed"}}))
    assert found[9]["state"] == "applied"
    assert found[9]["prs"] == ["49000"]


def test_quoted_lines_are_not_his_words():
    assert by_message(rows([]))[9]["text"] == "move the owner table to a tab"


def test_taps_leave_the_messages_and_carry_their_pull_request():
    found = rows([], {"49000": {"state": "landed"}})
    assert found["taps"][0]["pr"] == "49000"
    assert found["taps"][0]["state"] == "applied"
    assert 10 not in by_message(found)


def test_a_one_word_reply_is_not_an_ask():
    assert 11 not in by_message(rows([]))


RENDER = """# Owner update

3 overdue:
- unfinished pick/100, due 4:20 AM
- ask #200, due 3:14 AM
- repeat grade overdue, due 12:45 AM

Owner orders with no live lane (1):
- m1 Nov 2 9:04 PM: words
"""


def test_overdue_rows_name_what_is_owed_and_stop_at_the_section_end():
    records = [record(100, "board-watch", "owner", "Owner on board d50e, Start the release? (catchup-start-choice): picked \u201cYes\u201d, at 2:28 AM"), record(200, "lane-1", "ask", "Please lift the hold")]
    found = overdue_rows(RENDER, records, [])
    assert [(row["group"], row["seq"], row["due"], row["text"]) for row in found] == [
        ("pick", 100, "4:20 AM", "Start the release?"),
        ("ask", 200, "3:14 AM", "Please lift the hold"),
        ("repeat", None, "12:45 AM", "grade overdue"),
    ]


def test_a_tap_row_keeps_every_question_it_asked_and_drops_the_board_words():
    text = "Owner on board d50e, Five picks for the new system, Which store holds it? (actions-store): picked \u201cA\u201d, at 2:28 AM"
    render = "1 overdue:\n- unfinished pick/100, due 4:20 AM\n"
    assert overdue_rows(render, [record(100, "board-watch", "owner", text)], [])[0]["text"] == "Five picks for the new system, Which store holds it?"


def test_a_tap_cut_off_before_its_answer_still_drops_the_board_words():
    render = "1 overdue:\n- unfinished pick/100, due 4:20 AM\n"
    assert overdue_rows(render, [record(100, "board-watch", "owner", "Owner on board d50e, A very long question that was cut")], [])[0]["text"] == "A very long question that was cut"


def test_a_store_ask_takes_its_words_from_the_store():
    render = "1 overdue:\n- unfinished ask/000025, due 10:00 AM\n"
    assert overdue_rows(render, [], [{"key": "ask/000025", "text": "Cut the job"}])[0]["text"] == "Cut the job"


def test_nothing_overdue_is_no_rows():
    assert overdue_rows("# Owner update\n", [], []) == []


def test_only_records_a_row_can_use_are_kept():
    assert kept(record(1, "a", "ask", "anything"))
    assert kept(record(1, "a", "report", "m7 done"))
    assert not kept(record(1, "a", "report", "no message named"))


def test_a_row_answered_with_no_reply_to_him_says_so_and_never_shows_the_record_between_lanes():
    found = by_message(rows([record(1, "lane-1", "report", "m8 board-refresher-7: withdraw the card.")]))
    assert found[8]["answer"] == "Answered, nothing for you."
    assert "latest" not in found[8] and "withdraw" not in str(found[8])


def test_the_newest_reply_naming_a_row_is_its_answer_and_reaches_the_question_that_row_routed():
    newest_first = [
        {"reply": {"id": "b", "md": "Done, nothing for you.\n\nSecond.", "order": "m20"}},
        {"reply": {"id": "a", "md": "First.", "order": "m20"}},
        {"reply": {"id": "c", "md": "An update."}},
    ]
    told = order_replies(newest_first)
    ledger = "| m20 | Nov 3 9:17 PM | why? | 77 | responded |\n| m21 | Nov 3 9:18 PM | how? | 78 | responded |\n"
    assert {row: reply["id"] for row, reply in told.items()} == {"m20": "b"}
    assert {seq: reply["id"] for seq, reply in told_asks(ledger, told).items()} == {77: "b"}
