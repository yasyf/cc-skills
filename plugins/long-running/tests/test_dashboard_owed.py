import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "dashboard" / "app"))

from owed import closed_asks, kept, order_replies, overdue_rows, owner_ask_rows, told_asks

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


NOW = dt.datetime(2026, 11, 4, 18, 0, tzinfo=dt.timezone.utc)


def test_overdue_rows_name_each_open_ask_and_late_retro_with_who_holds_it():
    store = [
        {"key": "ask/000025", "text": "Cut the job", "lane": "ci-speed-1", "state": "IN-PROGRESS"},
        {"key": "ask/000041", "text": "Fix the flake", "lane": "red-sweep-6", "state": "LOST"},
        {"key": "ask/000020", "text": "Delivered", "lane": "desk", "state": "LIVE"},
    ]
    retros = [
        {"key": "retro/000018", "state": "building", "due": "2026-11-04T17:00:00Z", "failure": "The queue dropped changes", "owner": "meta-desk"},
        {"key": "retro/000019", "state": "building", "due": "2026-11-05T17:00:00Z", "failure": "Not late yet", "owner": "meta-desk"},
        {"key": "retro/000001", "state": "live", "due": "2026-11-02T17:00:00Z", "failure": "Closed", "owner": "meta-desk"},
        {"key": "retro/000020", "state": "open", "due": "", "failure": "No date", "owner": "meta-desk"},
    ]
    assert [(row["group"], row["due"], row["text"], row["holder"]) for row in overdue_rows(store, retros, NOW)] == [
        ("ask", "In progress", "Cut the job", "ci-speed-1"),
        ("ask", "Lost", "Fix the flake", "red-sweep-6"),
        ("retro", "Nov 4, 9:00 AM", "The queue dropped changes", "meta-desk"),
    ]


def test_a_message_carries_what_the_audit_says_is_owed_on_it():
    found = owner_ask_rows(LEDGER, [], {}, [], set(), 2026, {}, None, {"m7": {"id": "m7", "status": "in progress", "lane": "tap-rows-1"}})
    assert by_message(found)[7]["owed"]["lane"] == "tap-rows-1"
    assert by_message(found)[8]["owed"] is None


def test_only_records_a_row_can_use_are_kept():
    assert kept(record(1, "a", "ask", "anything"))
    assert kept(record(1, "a", "report", "m7 done"))
    assert not kept(record(1, "a", "report", "no message named"))


def test_a_split_message_keeps_each_part_and_a_relayed_copy_of_a_row_is_skipped():
    ledger = """| m388.1 | Nov 3 3:00 AM | render the card whole on the dashboard |  | open |
| m388.2 | Nov 3 3:00 AM | remove the stale cards from my board |  | open |
| m231 | Nov 3 3:01 AM | m230: "no browser notifs" - dashboard raises none | 55 | open |
"""
    messages = owner_ask_rows(ledger, [], {}, [], set(), 2026, {"m388.2": "Removed."})["messages"]
    assert [(message["m"], message["answer"]) for message in messages] == [("388.1", ""), ("388.2", "Removed.")]


def test_a_row_answered_with_no_reply_to_him_carries_no_answer_and_never_shows_the_record_between_lanes():
    found = by_message(rows([record(1, "lane-1", "report", "m8 board-refresher-7: withdraw the card.")]))
    assert found[8]["answer"] == ""
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


DUE_LEDGER = """| m | at | text | record | mark |
|---|---|---|---|---|
| m20 | Nov 3 9:17 PM | why? | 77 | due Nov 3 9:32 PM |
| m21 | Nov 3 9:18 PM | Owner on board d50e, Merge it? (merge-prs): wrote \u201conly for the team\u201d | 78 | due Nov 3 9:33 PM |
| m22 | Nov 3 9:19 PM | is this the same bug as before? | 79 | responded |
"""


def due_rows(threads=None):
    return owner_ask_rows(DUE_LEDGER, [], {}, [], set(), 2026, {}, threads)


def test_a_due_question_keeps_its_row_with_its_clock_however_short_his_words():
    found = by_message(due_rows({77: {"answered": False, "holder": "the coordinator"}}))
    assert found[20]["state"] == "asked"
    assert found[20]["due"] == "2026-11-03T21:32:00-08:00"
    assert found[20]["holder"] == "the coordinator"


def test_a_due_board_note_is_still_asked_in_the_chats_hands_and_shows_no_card_id():
    tap = due_rows()["taps"][0]
    assert (tap["state"], tap["due"], tap["holder"]) == ("asked", "2026-11-03T21:33:00-08:00", "your chat")
    assert tap["question"] == "Merge it?: wrote \u201conly for the team\u201d"


def test_a_settled_thread_answers_a_short_question_and_drops_its_clock():
    found = by_message(due_rows({77: {"answered": True, "holder": "the coordinator"}}))
    assert (found[20]["state"], found[20]["due"]) == ("answered", None)


def test_a_closed_row_closes_the_question_it_routed_and_a_due_one_does_not():
    assert closed_asks(DUE_LEDGER) == {79: {"closed": "responded"}}


def test_a_message_moves_from_received_to_routed_to_done_as_the_ledger_is_marked():
    ledger = """| m1 | Nov 3 3:00 AM | please fix the typing box now |  | open |
| m2 | Nov 3 3:01 AM | please fix the other box now | 55 | open |
| m3 | Nov 3 3:02 AM | promise me a clock on this one |  | due Nov 3 6:40 PM |
| m4 | Nov 3 3:03 AM | tell me when this one is done | 56 | responded |
| m5 | Nov 3 3:04 AM | this was only a remark, thanks | 57 | no-order |
"""
    messages = owner_ask_rows(ledger, [], {}, [], set(), 2026, {})["messages"]
    assert [message["ack"] for message in messages] == ["received", "routed", "routed", "done", "done"]
