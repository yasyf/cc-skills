import datetime as dt
import json
import subprocess
from pathlib import Path

import pytest
import server
from brief import Curator
from pages import ASK_BATCH, PAGES, REVIEW_SCHEMA, asks_brief, asks_carry, asks_digest, open_question, plan_slot, review_digest, review_slot, stamp, titled_asks, waiting_since
from server import ALERTS_SENT, bot_words, drawable, drive_alerts, one_pagers, published_page, stored_replies, unanswered

NOW = dt.datetime(2026, 10, 27, 23, 0, tzinfo=dt.timezone.utc)
ROW = {
    "number": 43210,
    "head": "886920e",
    "title": "Editing a message forks the chat",
    "change": "Today every edit is refused; after this, the edit starts a new chat.",
    "plan": "Matches the plan.",
    "risk": "Low: forks into another team are refused first.",
    "review": "Codex found nothing; the reviewer bot approved.",
    "blocker": "Your approval.",
}
COUNTED = {**ROW, "number": 43211, "review": "Codex found three problems in an earlier version."}
CARD = {
    "id": "card-release-plan",
    "title": "Release plan: ten releases until everyone is on the new build",
    "summary": "Old sand 0.162.1852 rode the api release.",
    "recommended": "Release now",
    "options": [{"label": "Release now"}, {"label": "Wait for the browser pair"}],
    "parts": [
        {"kind": "record", "facts": []},
        {"kind": "choice", "prompt": "Release the SandSQL replica chain now?", "answer": None},
    ],
}


def drive(*rows, cards=(), groups=()):
    board = {"rows": [dict(row) for row in rows], "cards": [dict(card) for card in cards]}
    stamp(board)
    needs = [{"kind": "card", "card": card} for card in board["cards"]]
    needs += [{"kind": "prs", "key": key, "group": "Tooling PRs whose workers died, re-owned", "relation": "Three small tooling fixes.", "prs": board["rows"]} for key in groups]
    return {"board": board, "needs": needs, "records": [], "lanes": [], "workers": [], "prs": {}, "drive": {"prs": []}, "fetch": lambda kinds, since: []}


def rewrite_of(model, row, **fields):
    keyed = next(r for r in model["board"]["rows"] if r["number"] == row["number"])
    return {"key": keyed["key"], **{k: keyed[k] for k in ("title", "change", "plan", "risk", "review", "blocker")}, **fields}


def test_rows_that_pass_the_gate_need_no_run():
    assert review_digest(drive(ROW), {}, None, NOW) is None


def test_only_a_failing_row_goes_to_the_model_with_its_failing_fields():
    facts = review_digest(drive(ROW, COUNTED), {}, None, NOW)
    assert [list(row["problems"]) for row in facts["rows"]] == [["review"]]


def test_a_row_rewritten_from_the_same_words_needs_no_new_run():
    model = drive(COUNTED)
    previous = {"rows": [rewrite_of(model, COUNTED, review="Codex found the edit lost its parent, now kept.")], "groups": [], "cards": []}
    assert review_digest(model, {}, previous, NOW) is None


def test_a_new_head_is_rewritten_again():
    previous = {"rows": [rewrite_of(drive(COUNTED), COUNTED, review="Codex found the edit lost its parent, now kept.")], "groups": [], "cards": []}
    assert len(review_digest(drive({**COUNTED, "head": "1111111"}), {}, previous, NOW)["rows"]) == 1


def test_a_board_correction_at_the_same_head_is_rewritten_again():
    previous = {"rows": [rewrite_of(drive(COUNTED), COUNTED, review="Codex found the edit lost its parent, now kept.")], "groups": [], "cards": []}
    corrected = {**COUNTED, "blocker": "Conflicts with its base, so it cannot land until its worker updates it."}
    facts = review_digest(drive(corrected), {}, previous, NOW)
    assert facts["rows"][0]["previous_rewrite"] is None


def test_a_question_goes_to_the_model_with_its_open_step():
    facts = review_digest(drive(cards=[CARD]), {}, None, NOW)
    assert [(card["open_question"], card["recommended"]) for card in facts["cards"]] == [("Release the SandSQL replica chain now?", "Release now")]


def test_an_answered_question_has_no_open_step():
    answered = {**CARD, "parts": [{"kind": "choice", "prompt": "Release now?", "answer": {"optionIds": ["now"]}}]}
    assert open_question(answered) is None


def test_a_group_with_a_rewrite_at_its_key_needs_no_new_run():
    model = drive(ROW, groups=["g1"])
    assert review_digest(model, {}, None, NOW)["groups"][0]["title"] == "Tooling PRs whose workers died, re-owned"
    previous = {"rows": [], "groups": [{"key": "g1", "title": "Dashboard test fixes", "relation": "The test naming fix lands first."}], "cards": []}
    assert review_digest(model, {}, previous, NOW) is None


@pytest.mark.parametrize(("path", "slot"), [(("rows", 0, "review"), "headline"), (("groups", 1, "relation"), "latest"), (("cards", 0, "question"), "latest"), (("cards", 0, "answers", 1), "title"), (("cards", 0, "key"), None)])
def test_review_slots_cover_rows_groups_and_cards(path, slot):
    assert review_slot(path) == slot


@pytest.mark.parametrize("kind", ["done", "stopped", "stand-down"])
def test_a_finished_worker_wakes_the_work_page(kind):
    curator = Curator({}, None, None, next(page for page in PAGES if page.name == "work"))
    curator.poke(kind)
    assert curator.dirty.is_set()


def test_a_question_whose_open_step_changes_is_rewritten_again():
    before = drive(cards=[CARD])
    previous = {"rows": [], "groups": [], "cards": [{"key": before["board"]["cards"][0]["key"], "question": "Release the SandSQL replica chain now?", "means": "Starts it now."}]}
    moved = {**CARD, "parts": [CARD["parts"][0], {"kind": "choice", "prompt": "Release the browser live-update pair now?", "answer": None}]}
    assert review_digest(before, {}, previous, NOW) is None
    assert review_digest(drive(cards=[moved]), {}, previous, NOW)["cards"][0]["previous_rewrite"] is None


def test_a_landed_change_name_is_gated_as_a_title():
    assert plan_slot(("parts", 2, "landed", 0)) == "title"


def test_a_question_leaves_waiting_once_his_answer_names_it():
    cards = [{"id": "card-r2-failed-runs"}, {"id": "card-r2-retro"}, {"id": "card-browser-signal-token"}]
    words = [
        {"title": "Owner board write-in 10:39 AM Oct 27 on card-r2-failed-runs: re-trigger all the runs"},
        {"title": "Board retry-fork-point-choice (card-r2-retro), 7:25 PM Oct 26: where does a failed run retry"},
        {"title": "Owner 3:47 PM Oct 27: a real UI pass"},
    ]
    assert [card["id"] for card in unanswered(cards, words)] == ["card-browser-signal-token"]


def test_each_answer_label_is_its_own_string():
    card = REVIEW_SCHEMA["properties"]["cards"]["items"]["properties"]
    assert card["answers"] == {"type": "array", "items": {"type": "string"}}


def test_work_waits_from_the_change_s_last_push_like_review():
    records = [
        {"kind": "head", "at": "2026-10-28T00:10:00Z", "refs": {"prs": [43686]}},
        {"kind": "review", "at": "2026-10-28T00:15:00Z", "refs": {"prs": [43686]}},
        {"kind": "head", "at": "2026-10-28T01:17:00Z", "refs": {"prs": [43686]}},
        {"kind": "head", "at": "2026-10-28T01:30:00Z", "refs": {"prs": [43700]}},
        {"kind": "state", "at": "2026-10-28T01:40:00Z", "refs": None},
    ]
    assert waiting_since(records, 43686) == "6:17 PM"
    assert waiting_since(records, 49001) == ""


COMPANION = "<!-- pr-reviewer bot companion -->\n\n### ELI5\n\nEditing a message now starts a new chat instead of failing.\n\n### Read in this order\n\n| File | What it settles |\n\n<sub>`606342b`</sub>"


def test_the_review_page_quotes_the_bot_s_eli5_section_alone():
    assert bot_words([COMPANION])["eli5"] == "Editing a message now starts a new chat instead of failing."


def test_a_diagram_only_companion_gives_its_titled_diagram():
    companion = "<!-- pr-reviewer bot companion -->\n\n### GitHub merge state gates untracked dev PRs\n\n```mermaid\nflowchart LR\n    CLI -->|\"enqueue\"| Queue\n```\n\n<sub>`7bed615`</sub>"
    summary = "<!-- pr-reviewer bot summary -->\n\n> [!TIP]\n> **No blocking findings**\n\n**Since [`7bed615`](x):** Adds a fallback.\n\nNo new findings at this head. The existing thread still applies.\n\n<details></details>"
    diagram = {"title": "GitHub merge state gates untracked dev PRs", "source": "flowchart LR\n    CLI -->|\"enqueue\"| Queue"}
    assert bot_words([summary, companion]) == {"eli5": None, "diagram": diagram, "summary": None}


def test_an_eli5_companion_gives_its_prose_and_the_diagram_after_it():
    companion = "<!-- pr-reviewer bot companion -->\n\n### ELI5\n\nShared chats fork from the sharer's backups.\n\n### Share forks restore base files first\n\n```mermaid\nflowchart LR\n  A --> B\n```\n\n<sub>`fd04919`</sub>"
    words = bot_words([companion])
    assert (words["eli5"], words["diagram"]) == ("Shared chats fork from the sharer's backups.", {"title": "Share forks restore base files first", "source": "flowchart LR\n  A --> B"})


def test_a_companion_section_that_is_not_a_diagram_gives_no_diagram():
    companion = "<!-- pr-reviewer bot companion -->\n<!-- ships-with -->\n### Ships with\n- `api`, `dashboard` ship in the same release\n<!-- /ships-with -->"
    assert bot_words([companion])["diagram"] is None


def test_without_a_companion_the_summary_s_plain_lead_says_what_it_does():
    summary = "<!-- pr-reviewer bot summary -->\n\n> [!TIP]\n> **No open findings**\n\nThe test fixtures now build their run ID the way production sends do.\n\n> [!WARNING]\n> callouts\n\n<details></details>"
    assert bot_words([summary])["summary"] == "The test fixtures now build their run ID the way production sends do."


def test_a_later_run_s_findings_lead_is_not_taken_for_what_it_does():
    summary = "<!-- pr-reviewer bot summary -->\n\n> [!TIP]\n> **1 blocking**\n\n**Since [`606342b`](x):** Adds a check.\n\nThis head adds two problems. Share recipients are not held to the shared position.\n\n<details></details>"
    assert bot_words([summary])["summary"] is None


def test_a_later_run_s_plain_lead_still_says_what_the_change_does():
    summary = "<!-- pr-reviewer bot summary -->\n\n> [!TIP]\n> **No open findings**\n\n**Since [`606342b`](x):** Adds a retry.\n\nA refused write now retries once with a fresh credential.\n\n<details></details>"
    assert bot_words([summary])["summary"] == "A refused write now retries once with a fresh credential."


def test_a_lead_that_counts_new_findings_is_not_taken_for_what_it_does():
    summary = "<!-- pr-reviewer bot summary -->\n\n> [!WARNING]\n> **1 non-blocking**\n\nOne new nonblocking finding: the old release still reads the next tree's config.\n\n<details></details>"
    assert bot_words([summary])["summary"] is None


def test_a_sequence_participant_named_like_a_mermaid_keyword_is_renamed_where_it_is_an_id():
    source = "sequenceDiagram\n    participant Actor as Session actor\n    participant End\n    Host->>Actor: Halt the Actor turns\n    Note over Actor,End: closes"
    assert drawable(source) == "sequenceDiagram\n    participant Actor_ as Session actor\n    participant End_ as End\n    Host->>Actor_: Halt the Actor turns\n    Note over Actor_,End_: closes"


def test_a_flowchart_is_drawn_as_written():
    source = "flowchart LR\n    Actor --> End"
    assert drawable(source) == source


def test_replies_read_newest_first_by_time_and_skip_a_staged_write(tmp_path):
    for name, at in (("older", "2026-11-03T23:00:00Z"), ("later-in-the-second", "2026-11-04T00:14:44.764488193Z"), ("earlier-in-the-second", "2026-11-04T00:14:44.76Z")):
        (tmp_path / f"{name}.json").write_text(json.dumps({"reply": {"id": name, "title": name, "md": name}, "at": at, "surfaces": []}))
    (tmp_path / "half-written.json.staged").write_text("{")
    read = stored_replies(lambda path: json.loads(Path(path).read_text()), tmp_path)
    assert [entry["reply"]["id"] for entry in read] == ["later-in-the-second", "earlier-in-the-second", "older"]


def reply_entry(id, **fields):
    return {"at": "2026-11-03T18:00:00Z", "surfaces": [], "reply": {"id": id, "title": f"title {id}", "md": "body", **fields}}


def alert_card(id, **fields):
    return {"id": id, "title": f"title {id}", "summary": f"summary {id}", **fields}


def test_every_new_reply_raises_an_alert_whatever_its_status():
    replies = [reply_entry("n", status="needs_you"), reply_entry("i", status="incident"), reply_entry("p", status="info", picks=["a", "b"]), reply_entry("d", status="done"), reply_entry("g", status="progress", md="**9:00 PM**\nsecond line"), reply_entry("x")]
    assert drive_alerts(replies, []) == [
        {"id": "n", "title": "title n", "body": "body", "incident": False},
        {"id": "i", "title": "title i", "body": "body", "incident": True},
        {"id": "p", "title": "title p", "body": "a · b", "incident": False},
        {"id": "d", "title": "title d", "body": "body", "incident": False},
        {"id": "g", "title": "title g", "body": "9:00 PM", "incident": False},
        {"id": "x", "title": "title x", "body": "body", "incident": False},
    ]


def test_a_card_alone_raises_its_own_alert_and_a_card_with_a_reply_raises_one():
    alerts = drive_alerts([reply_entry("r1", status="needs_you")], [alert_card("card-r1"), alert_card("card-alone")])
    assert [(alert["id"], alert.get("card")) for alert in alerts] == [("r1", None), ("alone", "card-alone")]
    assert alerts[1]["title"] == "title card-alone" and alerts[1]["body"] == "summary card-alone"


def test_a_card_keeps_the_same_id_after_its_reply_ages_out_so_it_never_fires_twice():
    replies = [reply_entry(f"r{n}") for n in range(ALERTS_SENT)] + [reply_entry("old")]
    assert [alert["id"] for alert in drive_alerts(replies, [alert_card("card-old")])][-1] == "old"


def test_alerts_keep_the_newest_when_many_wait():
    replies = [reply_entry(f"r{n}", status="needs_you") for n in range(ALERTS_SENT + 5)]
    assert [alert["id"] for alert in drive_alerts(replies, [])] == [f"r{n}" for n in range(ALERTS_SENT)]


def test_what_waits_on_him_still_alerts_when_newer_replies_fill_the_newest_while_his_laptop_sleeps():
    newer = [reply_entry(f"r{n}", status="done") for n in range(ALERTS_SENT)]
    older = [reply_entry("asked", status="needs_you"), reply_entry("down", status="incident"), reply_entry("chose", status="info", picks=["a"]), reply_entry("finished", status="done")]
    assert [alert["id"] for alert in drive_alerts(newer + older, [])] == [f"r{n}" for n in range(ALERTS_SENT)] + ["asked", "down", "chose"]


@pytest.mark.parametrize("script", sorted(Path(server.__file__).parent.glob("*.js")), ids=lambda path: path.name)
def test_every_page_script_parses(script):
    subprocess.run(["node", "--check", str(script)], check=True)


def test_a_folded_ask_gets_one_title_asked_for_once_and_kept_across_runs():
    rows = [{"m": "5.1", "text": "first part"}, {"m": "5.2", "text": "second part"}, {"m": 6, "text": "known"}]
    model = {"owner_asks": {"messages": rows}, "records": [], "lanes": []}
    previous = {"titles": [{"key": "m6", "title": "Known"}]}
    assert asks_digest(model, {}, previous, NOW)["asks"] == [{"key": "m5", "said": "first part second part"}]
    kept = asks_carry({"titles": [{"key": "m5", "title": "Both parts"}, {"key": "m9", "title": ""}]}, previous)
    assert kept["titles"] == [{"key": "m6", "title": "Known"}, {"key": "m5", "title": "Both parts"}]
    assert asks_digest(model, {}, kept, NOW) is None
    assert [row["title"] for row in titled_asks(model["owner_asks"], kept)["messages"]] == ["Both parts", "Both parts", "Known"]


def test_titles_past_one_batch_say_more_is_left_so_a_quiet_drive_drains():
    rows = [{"m": n, "text": f"ask {n}"} for n in range(ASK_BATCH + 1)]
    facts = asks_digest({"owner_asks": {"messages": rows}, "records": [], "lanes": []}, {}, None, NOW)
    assert facts["more"] and len(facts["asks"]) == ASK_BATCH and facts["asks"][-1]["key"] == f"m{ASK_BATCH}"


def test_a_mirror_rebuilds_the_titles_from_the_rows_it_was_served():
    asks = {"messages": [{"m": "5.1", "title": "Both parts"}, {"m": 6, "text": "no title yet"}]}
    assert asks_brief(asks) == {"titles": [{"key": "m5", "title": "Both parts"}]}


def test_an_ask_with_no_title_yet_stays_untitled_so_the_page_falls_back():
    asks = {"messages": [{"m": 7, "text": "just arrived"}], "taps": []}
    assert "title" not in titled_asks(asks, None)["messages"][0]


def test_a_published_one_pager_is_listed_once_with_no_reply_naming_it():
    published = published_page({"at": "2026-11-04T12:00:00Z", "fields": {"attrs": {"one-pager-url": "https://mac.ts.net:8665/one-pagers/lane-resume/", "one-pager-title": "How a lane resumes", "one-pager-lede": "It picks up from its handoff."}}})
    linked_later = {"at": "2026-11-04T13:00:00Z", "reply": {"title": "Lane resume answered", "links": [{"label": "Lane resume", "url": "https://docs.example/one-pagers/lane-resume/"}, {"label": "A PR", "url": "https://github.com/a/b/pull/1"}]}}
    linked_only = {"at": "2026-11-04T11:00:00Z", "reply": {"title": "Why the cap exists", "links": [{"label": "Gateway cap", "url": "https://mac.ts.net:8665/one-pagers/gateway-cap/"}]}}

    assert published_page({"at": "2026-11-04T12:30:00Z", "fields": {"attrs": {"delivered": "owner:m1"}}}) is None
    assert one_pagers([published], [linked_later, linked_only]) == [
        published,
        {"at": "2026-11-04T11:00:00Z", "url": "https://mac.ts.net:8665/one-pagers/gateway-cap/", "title": "Gateway cap", "lede": "Why the cap exists"},
    ]
