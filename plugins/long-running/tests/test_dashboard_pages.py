import datetime as dt

import pytest
from brief import Curator
from pages import PAGES, REVIEW_SCHEMA, open_question, plan_slot, review_digest, review_slot, stamp, waiting_since
from server import bot_words, drawable, unanswered

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
