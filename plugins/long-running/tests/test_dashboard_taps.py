import taps


def row(number, **fields):
    return {"number": number, "head": "abc1234", "title": f"Change {number}", "change": f"Change {number} does a thing for users.", "blocker": "Your approval.", "state": "you", "awaiting": None, **fields}


def card(card_id, **fields):
    return {"id": card_id, "title": f"Question {card_id}", "summary": "The whole story of it. More after that.", "recommended": "Yes", "choice": f"{card_id}-pick", "options": [{"id": "yes", "label": "Yes", "recommended": True}], "decides": True, "prs": [], **fields}


def entry(number, base=None, state="you"):
    return {"number": number, "base": base, "state": state}


def blocked(lane, text, seq=1, **fields):
    return {"seq": seq, "lane": lane, "text": text, "status": "open", **fields}


def queue(rows=(), cards=(), slack=(), prs=(), blocks=(), urgent=()):
    return taps.tap_queue(list(rows), list(cards), list(slack), list(prs), list(blocks), lambda number: f"Change {number}", urgent)


def test_the_change_with_the_most_stacked_above_it_comes_first():
    prs = [entry(1), entry(2), entry(3, base=2), entry(4, base=3)]
    assert [tap["number"] for tap in queue([row(1), row(2), row(3), row(4)], prs=prs)] == [2, 3, 1, 4]


def test_a_card_frees_the_change_a_parked_worker_names_with_it_and_what_stacks_on_that():
    prs = [entry(1), entry(2, state="doing"), entry(3, base=2, state="doing")]
    out = queue([row(1)], [card("card-a"), card("card-b")], prs=prs, blocks=[blocked("run-fix-1", "Landing waits on go. #00002 also waits on card-a.".replace("00002", "48977"))])
    assert out[0]["id"] == "card-a"
    assert out[0]["frees"] == 1
    assert "1 worker" in out[0]["line"]


def test_a_worker_parked_on_his_tap_for_a_change_counts_toward_that_change():
    prs = [entry(48001), entry(48002)]
    out = queue([row(48001), row(48002)], prs=prs, blocks=[blocked("land-1", "#48002 is green and waits on his tap.")])
    assert [tap["number"] for tap in out] == [48002, 48001]
    assert out[0]["line"] == "1 worker stopped until you tap."


def test_a_refs_only_record_names_the_change_it_waits_for():
    out = queue([row(48001), row(48002)], prs=[entry(48001), entry(48002)], blocks=[blocked("land-1", "Green, and waiting on the owner's tap.", refs={"prs": ["Forge-AI/monorepo#48002"]})])
    assert out[0]["number"] == 48002


def test_a_card_cited_change_counts_only_while_it_is_open():
    prs = [entry(48001, state="landed"), entry(48002, state="doing")]
    assert queue(cards=[card("card-a", prs=[48001])], prs=prs)[0]["frees"] == 0
    assert queue(cards=[card("card-a", prs=[48002])], prs=prs)[0]["frees"] == 1


def test_urgent_comes_before_anything_it_frees():
    prs = [entry(1), entry(2, base=1)]
    assert queue([row(1)], [card("card-now")], prs=prs, urgent={"card-now"})[0]["id"] == "card-now"


def test_only_a_tap_he_can_make_now_is_a_row():
    rows = [row(1), row(2, awaiting=991), row(3, state="not-ready"), row(4, state="approved")]
    assert [tap["number"] for tap in queue(rows)] == [1]


def test_a_row_the_ledger_misses_or_reads_at_an_old_head_stays_in_the_queue():
    prs = [{**entry(1), "head": "bbbbbbb"}]
    assert [tap["number"] for tap in queue([row(1), row(2)], prs=prs)] == [1, 2]


def test_a_card_thread_keeps_his_words_and_each_answer_in_order_and_skips_the_boards_own_lines():
    events = [
        ("card-a", "feedback.created", 1, "why this?", None),
        ("card-a", "reply.created", 2, None, "Because of the bucket."),
        ("card-a", "reply.created", 3, None, "Answered on [Slack](u) at 6:26PM: **Yes**"),
        ("card-b", "reply.created", 4, None, "Withdrawn on [Slack](u): replaced"),
    ]
    assert taps.card_threads(events) == {"card-a": [{"who": "you", "text": "why this?", "at": 1}, {"who": "answer", "text": "Because of the bucket.", "at": 2}]}


def test_a_card_with_no_decision_left_is_not_a_row():
    assert queue(cards=[card("card-note", decides=False)]) == []


def test_a_row_says_what_changes_when_nothing_waits_on_it():
    assert queue([row(1)], prs=[entry(1)])[0]["line"] == "Nothing else waits on it. Change 1 does a thing for users."
    assert queue(cards=[card("card-a")])[0]["line"] == "No recorded work waits on it. The whole story of it."
    assert queue(cards=[card("reply-a", title="Can I respond to their crash thread?")])[0]["line"] == "No worker is waiting on this post. The whole story of it."


def test_a_card_asking_to_post_in_a_thread_is_a_slack_post_and_a_repost_of_a_question_is_a_pick():
    assert queue(cards=[card("reply-josh-crash", title="Can I respond to Josh's crash thread with the finding?")])[0]["kind"] == taps.SLACK
    assert queue(cards=[card("reply-sofi", title="Re-posted: fix SoFi's pool-cap refusals?")])[0]["kind"] == taps.PICK


def test_a_block_a_later_one_replaced_or_a_finished_or_desk_lane_parks_nothing():
    records = [blocked("a-1", "old", seq=1), blocked("a-1", "new", seq=2, resolves=1), blocked("b-1", "x", seq=3), blocked("c-1", "y", seq=4), blocked("board-refresher-7", "z", seq=5), blocked("d-1", "gone", seq=6, status="closed")]
    assert [record["text"] for record in taps.open_blocks(records, [{"lane": "b-1", "kind": "done"}, {"lane": "c-1", "kind": "report"}])] == ["new", "y"]


def outcomes(*blocks, choices=None, replies=None, retired=()):
    return {"doc": {"blocks": list(blocks)}, "retired": list(retired), "interactions": {"choices": choices or {}, "replies": replies or {}}}


def post_card(card_id, title="Post this in C0BGWATH8JU, thread 1793742513.233839?"):
    return {
        "id": card_id,
        "type": "card",
        "title": title,
        "slack": {"url": "https://slack.example/p1"},
        "children": [
            {"id": f"{card_id}-why", "type": "markdown", "md": "Why: Tevon has no answer yet."},
            {"id": f"{card_id}-context", "type": "markdown", "md": "Where: a thread.\n\n<@U0APTH9UB2B>, the fix is in review: <https://github.com/o/r/pull/49638|#49638>. <!here>"},
            {"id": f"{card_id}-answer", "type": "choice", "options": [{"id": "yes", "label": "Yes", "recommended": True}, {"id": "no", "label": "No"}]},
        ],
    }


def test_a_slack_post_card_reads_in_words_and_carries_its_answer_path():
    out = taps.slack_cards(outcomes(post_card("slack-1")), "questions-for-you--x")[0]
    assert out["title"] == "Post this in a Slack thread?"
    assert out["summary"] == "Tevon has no answer yet."
    assert out["text"] == "Where: a thread.\n\n@someone, the fix is in review: #49638. @here"
    assert (out["choice"], out["subject"], out["recommended"]) == ("slack-1-answer", "questions-for-you--x", "Yes")


def test_an_answered_withdrawn_or_retired_slack_post_is_gone():
    board = outcomes(
        post_card("slack-tapped"),
        post_card("slack-on-slack"),
        post_card("slack-withdrawn"),
        post_card("slack-retired"),
        post_card("slack-open"),
        {"id": "other", "type": "card", "title": "Not a Slack post", "children": []},
        choices={"slack-tapped-answer": {"optionIds": ["yes"]}},
        replies={"slack-on-slack": [{"md": "Answered on [Slack](u) at 6:26PM: **Yes**"}], "slack-withdrawn": [{"md": "Withdrawn on [Slack](u): replaced"}], "slack-open": [{"md": "A note from the worker"}]},
        retired=["slack-retired"],
    )
    assert [card["id"] for card in taps.slack_cards(board, "s")] == ["slack-open"]


def test_a_slack_post_frees_no_change_it_only_mentions():
    slack = [{"id": "slack-a", "title": "Post this?", "summary": "Why", "text": "t", "choice": "c", "options": [], "recommended": "Yes", "subject": "s", "prs": [48001]}]
    out = queue(slack=slack, prs=[entry(48001, state="doing")], blocks=[blocked("bugs-2", "Waits on slack-a (#48001 in review).")])
    assert out[0]["frees"] == 1
    assert out[0]["line"] == "1 worker stopped until you tap. Why"


def test_an_urgent_tap_says_so_and_no_other_does():
    out = queue(cards=[card("card-now"), card("card-later")], urgent={"card-now"})
    assert [(tap["id"], tap["urgent"]) for tap in out] == [("card-now", True), ("card-later", False)]


def test_a_worker_naming_a_slack_post_ranks_it_above_one_nobody_waits_on():
    slack = [{"id": f"slack-{name}", "title": name, "summary": "Why", "text": "t", "choice": f"slack-{name}-answer", "options": [], "recommended": "Yes", "subject": "s"} for name in ("old", "live")]
    out = queue(slack=slack, blocks=[blocked("bugs-comms-2", "bugs-comms-2 waits on the owner own taps: Tevon follow-up slack-live and more.")])
    assert [tap["id"] for tap in out] == ["slack-live", "slack-old"]
    assert out[0]["subject"] == "s"


def test_the_review_page_and_the_board_take_the_queue_order_and_keep_the_rest_in_place():
    order = [{"id": "land-2"}, {"id": "land-1"}]
    rows = [{"number": 9}, {"number": 1}, {"number": 8}, {"number": 2}]
    assert [item["number"] for item in taps.in_queue_order(order, rows, lambda item: f"land-{item['number']}")] == [2, 1, 9, 8]


def test_a_landing_tap_reads_the_plain_title():
    tap = queue([row(1, title="sandsql: 🐛 a stalled sync logs what holds it")], prs=[entry(1)])[0]
    assert tap["title"] == "A stalled sync logs what holds it"


def test_a_pull_request_title_loses_its_scope_and_emoji():
    assert taps.plain_title("sandsql: 🐛 a stalled sync logs what holds it") == "A stalled sync logs what holds it"
    assert taps.plain_title("api, dashboard: ✨ one credential") == "One credential"


def test_a_change_with_no_title_still_reads_as_a_sentence():
    assert taps.freed_words({1}, set(), lambda number: "") == "Frees 1 change."
    assert taps.freed_words({1, 2, 3}, set(), {1: "", 2: "Second", 3: "Third"}.get) == "Frees 3 changes: Second; Third; and 1 more."
